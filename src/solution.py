"""Larkspur Health agent - walking skeleton.

One agent across phone, text and portal chat for Saguaro Family Medicine.

This is a runnable simulation of the design in APPROACH.md. The five sample
contacts (A-E) are pushed through the same pipeline a real deployment uses:

    channel -> identity/consent -> safety gate -> orchestration -> integrations -> action

Hard rules encoded here (they are controls, not preferences):

  * Nothing clinical is decided by the agent. Clinical contacts leave through a
    human, or an emergency path, with no advice given.
  * No PHI moves on an unverified channel, ever.
  * Raw audio is never persisted (see AUDIO_POLICY).

The LLM is abstracted behind ``classify`` / ``detect_language``. In production
those call a BAA-covered model; here they are deterministic rules so the
skeleton is testable and auditable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional

TODAY = date.today()


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #
class Channel(str, Enum):
    PHONE = "phone"
    TEXT = "text"
    PORTAL_CHAT = "portal_chat"
    OUTBOUND = "outbound"


class Language(str, Enum):
    EN = "en"
    ES = "es"


class Intent(str, Enum):
    REFILL = "refill"
    INSURANCE = "insurance"
    PA_STATUS = "prior_auth_status"
    RESULTS = "results"
    NEW_PATIENT = "new_patient"
    SYMPTOM = "symptom"
    HOURS = "hours"
    UNKNOWN = "unknown"


class Disposition(str, Enum):
    RESOLVED = "resolved"
    BOOKED = "booked"
    TASK_CREATED = "task_created"
    CALLBACK_PROMISED = "callback_promised"
    ESCALATED_EMERGENCY = "escalated_emergency"
    ESCALATED_CRISIS = "escalated_crisis"
    ESCALATED_NURSE = "escalated_nurse"
    REFUSED_PHI = "refused_phi"


# --------------------------------------------------------------------------- #
# Domain
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Representative:
    """A person on the chart who may act for the patient (proxy)."""

    name: str
    dob: date
    relationship: str


@dataclass(frozen=True)
class Patient:
    patient_id: str
    name: str
    dob: date
    language: Language
    phones_on_file: tuple[str, ...]
    last4_ssn: str
    zip_code: str
    representatives: tuple[Representative, ...] = ()

    def find_representative(self, name: str, dob: date) -> Optional[Representative]:
        for rep in self.representatives:
            if rep.name.casefold() == name.casefold() and rep.dob == dob:
                return rep
        return None


@dataclass(frozen=True)
class Medication:
    name: str
    dose: str
    controlled: bool = False


@dataclass(frozen=True)
class Coverage:
    plan_name: str
    active: bool
    in_network: bool


@dataclass(frozen=True)
class Eligibility:
    active: bool
    in_network: bool
    plan_name: str
    note: str = ""


@dataclass(frozen=True)
class PriorAuthStatus:
    procedure: str
    status: str  # "approved" | "pending" | "denied" | "not_found"
    decided_on: str = ""


@dataclass(frozen=True)
class FormularyResult:
    covered: bool
    tier: int
    note: str = ""


@dataclass(frozen=True)
class Task:
    task_id: str
    patient_id: str
    queue: str
    subject: str


@dataclass(frozen=True)
class Appointment:
    appointment_id: str
    patient_id: str
    visit_type: str
    when: str


def normalize_phone(number: Optional[str]) -> Optional[str]:
    if not number:
        return None
    digits = "".join(ch for ch in number if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


class PatientRegistry:
    """Stands in for Epic patient search."""

    def __init__(self, patients: list[Patient]) -> None:
        self._by_id = {p.patient_id: p for p in patients}
        self._by_phone = {
            normalize_phone(ph): p for p in patients for ph in p.phones_on_file
        }

    def by_id(self, patient_id: str) -> Optional[Patient]:
        return self._by_id.get(patient_id)

    def by_phone(self, number: Optional[str]) -> Optional[Patient]:
        return self._by_phone.get(normalize_phone(number))

    def by_name(self, name: str) -> Optional[Patient]:
        for patient in self._by_id.values():
            if patient.name.casefold() == name.casefold():
                return patient
        return None


# --------------------------------------------------------------------------- #
# Inbound contact + identity
# --------------------------------------------------------------------------- #
@dataclass
class IdentityClaim:
    channel: Channel
    from_number: Optional[str] = None
    portal_authenticated: bool = False
    dob: Optional[date] = None
    knowledge_factor: Optional[str] = None  # last-4 SSN
    otp_verified: bool = False
    caller_name: Optional[str] = None
    caller_dob: Optional[date] = None
    on_behalf_of: Optional[str] = None  # patient name the caller asks about


@dataclass
class InboundMessage:
    contact_id: str
    channel: Channel
    text: str
    claim: IdentityClaim
    language: Optional[Language] = None
    note: str = ""


@dataclass(frozen=True)
class Verification:
    verified: bool
    level: str
    patient: Optional[Patient]
    is_proxy: bool = False
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class SafetyAssessment:
    red_flag: bool
    crisis: bool
    clinical_question: bool
    reasons: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Integrations (in-memory stubs for Epic / payer / pharmacy / language line)
# --------------------------------------------------------------------------- #
class EpicGateway:
    """Read/write against Epic.

    Production: Epic on FHIR R4 for reads, Cadence / Open Scheduling for
    appointment writes and In Basket for tasks, usually through Redox
    middleware so we are not blocked on native API enablement.
    """

    def __init__(self, registry: PatientRegistry) -> None:
        self.registry = registry
        self.tasks: list[Task] = []
        self.appointments: list[Appointment] = []
        self._coverage = {
            "P-1001": Coverage("Humana Gold Plus HMO", True, True),
            "P-1002": Coverage("Medicare Part A/B", True, True),
            "P-1003": Coverage("Aetna Choice POS II", True, True),
            "P-1004": Coverage("Blue Cross Blue Shield", True, True),
        }
        self._medications = {
            "P-1001": [Medication("lisinopril", "10 mg")],
            "P-1002": [Medication("furosemide", "40 mg"), Medication("carvedilol", "6.25 mg")],
        }
        self._last_visit = {
            "P-1001": date(2025, 1, 10),  # > 12 months ago: refill needs a visit
            "P-1002": date(2026, 9, 30),
            "P-1003": date(2026, 8, 4),
            "P-1004": date(2026, 6, 21),
        }
        self._task_seq = 0
        self._appt_seq = 0

    def get_coverage(self, patient_id: str) -> Optional[Coverage]:
        return self._coverage.get(patient_id)

    def get_medications(self, patient_id: str) -> list[Medication]:
        return list(self._medications.get(patient_id, []))

    def last_visit(self, patient_id: str) -> Optional[date]:
        return self._last_visit.get(patient_id)

    def get_prior_auth(self, patient_id: str, procedure: str) -> Optional[PriorAuthStatus]:
        # Prior-auth status is generally NOT tracked in Epic; see PayerGateway.
        return None

    def create_in_basket_task(self, patient_id: str, queue: str, subject: str) -> Task:
        self._task_seq += 1
        task = Task(f"TASK-{self._task_seq:04d}", patient_id, queue, subject)
        self.tasks.append(task)
        return task

    def book_appointment(self, patient_id: Optional[str], visit_type: str, when: str) -> Appointment:
        self._appt_seq += 1
        appt = Appointment(f"APPT-{self._appt_seq:04d}", patient_id or "NEW", visit_type, when)
        self.appointments.append(appt)
        return appt


class PayerGateway:
    """Eligibility (X12 270/271) and prior-auth status.

    Production: pVerify or Availity. This is where contact C is actually
    answered - the payer, not Epic, knows whether the MRI was approved.
    """

    def __init__(self) -> None:
        self._prior_auths = {
            ("P-1003", "MRI brain"): PriorAuthStatus("MRI brain", "approved", "2026-10-20"),
        }

    def check_eligibility(self, patient: Optional[Patient], plan_name: str) -> Eligibility:
        return Eligibility(
            active=True,
            in_network=True,
            plan_name=plan_name,
            note="verified via 270/271",
        )

    def get_prior_auth_status(self, patient: Patient, procedure: str) -> PriorAuthStatus:
        return self._prior_auths.get(
            (patient.patient_id, procedure),
            PriorAuthStatus(procedure, "not_found"),
        )


class PharmacyGateway:
    def formulary_check(self, medication: Medication, plan_name: str) -> FormularyResult:
        return FormularyResult(covered=True, tier=1, note="preferred generic")


class LanguageLine:
    """Human interpretation fallback for anything clinical or uncertain."""

    def request_interpreter(self, language: Language) -> str:
        return f"interpreter-{language.value}-queued"


@dataclass(frozen=True)
class AudioPolicy:
    """The answer to the privacy officer's question."""

    transport: str = "encrypted SIP/TLS to telephony vendor under BAA"
    asr: str = "HIPAA-eligible, US region, BAA, zero-retention, no vendor training"
    persist_raw_audio: bool = False  # default: transcribe-and-discard
    retention_opt_in_days: int = 30


AUDIO_POLICY = AudioPolicy()


# --------------------------------------------------------------------------- #
# Safety gate - runs BEFORE identity so an emergency is never delayed
# --------------------------------------------------------------------------- #
_CRISIS_PATTERNS = (
    r"\bkill myself\b", r"\bsuicid", r"\bend my life\b", r"\bhurt myself\b",
    r"\bwant to die\b", r"quitarme la vida", r"suicid",
)
_RED_FLAG_PATTERNS = (
    r"short(ness)? of breath", r"can'?t breathe", r"trouble breathing",
    r"difficulty breathing", r"chest pain", r"crushing chest", r"stroke",
    r"slurred speech", r"face droop", r"bleeding", r"blood in", r"vomiting blood",
    r"ankles? (look )?bigger", r"swell(ing|en)", r"swollen",
    r"falta de aire", r"no puedo respirar", r"no puede respirar",
    r"le cuesta respirar", r"dificultad para respirar", r"dolor de pecho",
    r"sangrado",
)
_CLINICAL_QUESTION_PATTERNS = (
    r"should i be worried", r"should i worry", r"do i need to (come in|be seen)",
    r"is this serious", r"what should i do", r"is it an emergency",
    r"debo preocuparme", r"tengo que venir", r"es grave",
)


class SafetyGate:
    def assess(self, text: str) -> SafetyAssessment:
        low = text.lower()
        crisis = any(re.search(p, low) for p in _CRISIS_PATTERNS)
        red_flag = any(re.search(p, low) for p in _RED_FLAG_PATTERNS)
        clinical = any(re.search(p, low) for p in _CLINICAL_QUESTION_PATTERNS)
        reasons: list[str] = []
        if crisis:
            reasons.append("self-harm / crisis language")
        if red_flag:
            reasons.append("red-flag symptom")
        if clinical:
            reasons.append("clinical question")
        return SafetyAssessment(red_flag, crisis, clinical, tuple(reasons))


# --------------------------------------------------------------------------- #
# Identity service - per-channel proof, plus authorized-representative path
# --------------------------------------------------------------------------- #
class IdentityService:
    def __init__(self, registry: PatientRegistry) -> None:
        self.registry = registry

    def verify(self, claim: IdentityClaim) -> Verification:
        if claim.channel is Channel.PORTAL_CHAT:
            if claim.portal_authenticated:
                patient = self.registry.by_name(claim.on_behalf_of) if claim.on_behalf_of else None
                return Verification(
                    True,
                    "portal session" + (" (proxy access)" if patient else ""),
                    patient,
                    is_proxy=bool(patient),
                )
            return Verification(False, "no authenticated session", None,
                                reasons=("portal session missing",))

        if claim.channel is Channel.TEXT:
            patient = self.registry.by_phone(claim.from_number)
            if patient is None:
                return Verification(False, "number not on file", None,
                                    reasons=("sender number not on file",))
            if claim.otp_verified and claim.dob == patient.dob:
                return Verification(True, "OTP to number on file", patient)
            return Verification(False, "OTP required", None,
                                reasons=("one-time code not completed",))

        if claim.channel is Channel.PHONE:
            patient = self.registry.by_phone(claim.from_number)
            if (
                patient is not None
                and claim.dob == patient.dob
                and claim.knowledge_factor == patient.last4_ssn
            ):
                return Verification(True, "ANI + DOB + knowledge factor", patient)
            # Proxy path: caller is an authorized representative on the chart.
            if claim.on_behalf_of and claim.caller_name and claim.caller_dob:
                target = self.registry.by_name(claim.on_behalf_of)
                if target is not None and target.find_representative(claim.caller_name, claim.caller_dob):
                    return Verification(True, "authorized representative", target, is_proxy=True)
            return Verification(False, "verification failed", None,
                                reasons=("could not verify caller",))

        return Verification(False, "unsupported channel", None)


# --------------------------------------------------------------------------- #
# NLU (rule-based here; BAA-covered model in production)
# --------------------------------------------------------------------------- #
_INTENT_PATTERNS: list[tuple[Intent, tuple[str, ...]]] = [
    (Intent.REFILL, (r"\brefill", r"\breceta", r"\bsurtir", r"\bprescription",
                     r"\bmedicamento", r"\blisinopril", r"\bpharmacy")),
    (Intent.INSURANCE, (r"insurance", r"\bplan\b", r"\bhumana\b", r"\bseguro\b",
                        r"\bcoverage\b", r"\bmedicare\b", r"\bmedicaid\b",
                        r"do you take", r"\baceptan\b")),
    (Intent.PA_STATUS, (r"prior auth", r"authoriz", r"\bapproved\b", r"aprobaron",
                        r"autoriz", r"\bresonancia\b", r"\bmri\b", r"\bscan\b",
                        r"\bimaging\b")),
    (Intent.RESULTS, (r"\bresults?\b", r"\ba1c\b", r"\blabs?\b", r"resultados",
                      r"\bblood work\b")),
    (Intent.NEW_PATIENT, (r"never been", r"new patient", r"first time",
                          r"nuevo paciente", r"nunca he")),
    (Intent.SYMPTOM, (r"\bcough\b", r"\btos\b", r"symptom", r"síntoma", r"worried",
                      r"preocup", r"\bpain\b", r"\bdolor\b", r"\bfever\b", r"\bfiebre\b")),
    (Intent.HOURS, (r"\bhours?\b", r"\bopen\b", r"\blocation\b", r"\baddress\b",
                    r"\bhorario\b", r"\bdirecci")),
]

_ES_MARKERS = ("hola", "quiero", "gracias", "esposo", "esposa", "resonancia",
               "aprobaron", "por favor", "cita", "seguro", "receta", "favor")


def detect_language(text: str) -> Language:
    low = text.lower()
    return Language.ES if any(m in low for m in _ES_MARKERS) else Language.EN


def classify(text: str) -> list[Intent]:
    low = text.lower()
    found: list[Intent] = []
    for intent, patterns in _INTENT_PATTERNS:
        if any(re.search(p, low) for p in patterns):
            found.append(intent)
    return found


def t(lang: Language, en: str, es: str) -> str:
    return es if lang is Language.ES else en


def _extract_zip(text: str) -> Optional[str]:
    match = re.search(r"\b(\d{5})\b", text)
    return match.group(1) if match else None


def _extract_medication(text: str, medications: list[Medication]) -> Optional[Medication]:
    low = text.lower()
    for med in medications:
        if med.name.lower() in low:
            return med
    return medications[0] if medications else None


def _extract_procedure(text: str) -> str:
    low = text.lower()
    if "resonancia" in low or "mri" in low:
        return "MRI brain"
    if "ct" in low or "scan" in low:
        return "CT"
    return "imaging study"


# --------------------------------------------------------------------------- #
# Result of a handled contact
# --------------------------------------------------------------------------- #
@dataclass
class AgentResult:
    contact_id: str
    disposition: Disposition
    language: Language
    reply: str
    actions: list[str] = field(default_factory=list)
    escalated_to: Optional[str] = None
    phi_released: bool = False
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# The agent
# --------------------------------------------------------------------------- #
class Agent:
    def __init__(
        self,
        registry: PatientRegistry,
        epic: EpicGateway,
        payer: PayerGateway,
        pharmacy: PharmacyGateway,
        language_line: LanguageLine,
    ) -> None:
        self.registry = registry
        self.epic = epic
        self.payer = payer
        self.pharmacy = pharmacy
        self.language_line = language_line
        self.safety = SafetyGate()
        self.identity = IdentityService(registry)

    # -- pipeline ---------------------------------------------------------- #
    def handle(self, msg: InboundMessage) -> AgentResult:
        lang = msg.language or detect_language(msg.text)
        assessment = self.safety.assess(msg.text)          # safety first
        verification = self.identity.verify(msg.claim)      # then identity
        intents = classify(msg.text)                        # then intent

        if assessment.crisis:
            return self._crisis(msg, lang)
        if assessment.red_flag:
            return self._emergency(msg, lang, assessment, verification)
        if Intent.RESULTS in intents:
            return self._results(msg, lang, verification)
        if Intent.REFILL in intents or Intent.INSURANCE in intents:
            return self._refill_and_coverage(msg, lang, verification, intents)
        if Intent.PA_STATUS in intents:
            return self._prior_auth(msg, lang, verification)
        if Intent.NEW_PATIENT in intents:
            return self._new_patient(msg, lang, assessment)
        if Intent.SYMPTOM in intents:
            return self._symptom(msg, lang, verification)
        if Intent.HOURS in intents:
            return self._hours(msg, lang)
        return self._fallback(msg, lang, verification)

    # -- safety paths ------------------------------------------------------ #
    def _crisis(self, msg: InboundMessage, lang: Language) -> AgentResult:
        return AgentResult(
            msg.contact_id,
            Disposition.ESCALATED_CRISIS,
            lang,
            t(
                lang,
                "I can't help with this myself, and I want you to be safe. Please call or "
                "text 988 right now - it's free and available 24/7. I'm alerting a clinician.",
                "No puedo ayudarle con esto yo mismo y quiero que esté a salvo. Llame o "
                "envíe un mensaje al 988 ahora - es gratis y disponible 24/7. Aviso a un clínico.",
            ),
            actions=["route to 988", "alert on-call clinician"],
            escalated_to="988 crisis line + on-call clinician",
            notes=["no clinical advice given"],
        )

    def _emergency(
        self,
        msg: InboundMessage,
        lang: Language,
        assessment: SafetyAssessment,
        verification: Verification,
    ) -> AgentResult:
        actions = ["warm transfer to on-call RN (24/7)"]
        if verification.patient is not None:
            self.epic.create_in_basket_task(
                verification.patient.patient_id, "RN Triage", "Post-discharge red flag"
            )
            actions.append("urgent task to care team")
        return AgentResult(
            msg.contact_id,
            Disposition.ESCALATED_EMERGENCY,
            lang,
            t(
                lang,
                "This could be an emergency. If he is struggling to breathe or getting worse "
                "right now, call 911. I'm connecting you to the on-call nurse now, and I've "
                "flagged this to his care team.",
                "Esto podría ser una emergencia. Si le cuesta respirar o está empeorando ahora "
                "mismo, llame al 911. Lo estoy conectando con la enfermera de guardia y avisé "
                "a su equipo de cuidado.",
            ),
            actions=actions,
            escalated_to="on-call RN (24/7) / 911",
            phi_released=False,
            notes=["no diagnosis, no reassurance", "reasons: " + ", ".join(assessment.reasons)],
        )

    # -- privacy-critical path --------------------------------------------- #
    def _results(self, msg: InboundMessage, lang: Language, verification: Verification) -> AgentResult:
        if not verification.verified:
            # Do not confirm or deny that the patient exists. No PHI.
            return AgentResult(
                msg.contact_id,
                Disposition.REFUSED_PHI,
                lang,
                t(
                    lang,
                    "I can't send test results to this number because I can't verify it. If you "
                    "have a MyChart account, results appear there. Otherwise I can call the "
                    "number we have on file and verify you, and a clinician can review the result.",
                    "No puedo enviar resultados a este número porque no lo puedo verificar. Si "
                    "tiene una cuenta de MyChart, los resultados aparecen ahí. De lo contrario, "
                    "puedo llamar al número que tenemos registrado y verificar su identidad.",
                ),
                actions=["no message sent", "offered portal or verified callback"],
                notes=["unverified channel: no confirmation patient exists", "phi not released"],
            )
        if msg.channel is Channel.TEXT:
            return AgentResult(
                msg.contact_id,
                Disposition.CALLBACK_PROMISED,
                lang,
                t(
                    lang,
                    "For your privacy I don't send results by text. I can have your care team "
                    "call you, or you can view them in MyChart.",
                    "Por su privacidad no envío resultados por mensaje de texto. Su equipo de "
                    "cuidado puede llamarle, o puede verlos en MyChart.",
                ),
                actions=["offered clinician callback / MyChart"],
                phi_released=False,
                notes=["SMS is not an approved channel for results"],
            )
        return AgentResult(
            msg.contact_id,
            Disposition.CALLBACK_PROMISED,
            lang,
            t(
                lang,
                "I can have your care team review and call you with your results.",
                "Su equipo de cuidado puede revisar y llamarle con sus resultados.",
            ),
            actions=["callback queued with care team"],
            phi_released=False,
            notes=["results release policy: clinician review first"],
        )

    # -- administrative paths ---------------------------------------------- #
    def _refill_and_coverage(
        self,
        msg: InboundMessage,
        lang: Language,
        verification: Verification,
        intents: list[Intent],
    ) -> AgentResult:
        if not verification.verified or verification.patient is None:
            return AgentResult(
                msg.contact_id,
                Disposition.CALLBACK_PROMISED,
                lang,
                t(
                    lang,
                    "I can help with that, but I need to verify who I'm speaking with first. "
                    "I can call the number we have on file, or you can message us in MyChart.",
                    "Puedo ayudarle, pero primero necesito verificar con quién hablo. Puedo "
                    "llamar al número que tenemos registrado, o puede escribirnos en MyChart.",
                ),
                actions=["no record action taken"],
                notes=["identity not verified"],
            )

        patient = verification.patient
        notes: list[str] = []
        actions: list[str] = []

        # Insurance change (contact A also asks this).
        coverage = self.epic.get_coverage(patient.patient_id)
        plan_name = "Humana" if "humana" in msg.text.lower() else (coverage.plan_name if coverage else "your plan")
        eligibility = self.payer.check_eligibility(patient, plan_name)
        actions.append(f"eligibility check: {eligibility.plan_name} ({eligibility.note})")

        # Refill request.
        medications = self.epic.get_medications(patient.patient_id)
        medication = _extract_medication(msg.text, medications)
        refill_line = ""
        if medication is not None:
            formulary = self.pharmacy.formulary_check(medication, eligibility.plan_name)
            last_visit = self.epic.last_visit(patient.patient_id)
            needs_visit = last_visit is None or (TODAY - last_visit).days > 365
            if medication.controlled:
                notes.append("controlled substance: prescriber review required")
            if needs_visit:
                notes.append("no visit in > 12 months: refill may require a visit")
            self.epic.create_in_basket_task(
                patient.patient_id,
                "Prescriber",
                f"Refill request: {medication.name} {medication.dose}",
            )
            actions.append(f"refill task -> Prescriber ({medication.name})")
            refill_line = t(
                lang,
                f"I've sent your refill request for {medication.name} to your care team"
                + (" and flagged that you may need an appointment first" if needs_visit else "")
                + f". It's {formulary.note or 'covered'} under {eligibility.plan_name}.",
                f"Envié su solicitud de resurtido de {medication.name} a su equipo de cuidado"
                + (" y marqué que podría necesitar una cita primero" if needs_visit else "")
                + f". Está cubierto bajo {eligibility.plan_name}.",
            )

        coverage_line = t(
            lang,
            f"Your {eligibility.plan_name} plan is active and in network. Please update your "
            "insurance at check-in or in MyChart so your billing is correct.",
            f"Su plan {eligibility.plan_name} está activo y dentro de la red. Actualice su "
            "seguro al registrarse o en MyChart para que su facturación sea correcta.",
        )
        reply = " ".join(part for part in (refill_line, coverage_line) if part)
        return AgentResult(
            msg.contact_id,
            Disposition.TASK_CREATED,
            lang,
            reply,
            actions=actions,
            phi_released=True,
            notes=notes + ["prescriber decides; agent never auto-approves a refill"],
        )

    def _prior_auth(self, msg: InboundMessage, lang: Language, verification: Verification) -> AgentResult:
        if not verification.verified or verification.patient is None:
            return AgentResult(
                msg.contact_id,
                Disposition.CALLBACK_PROMISED,
                lang,
                t(
                    lang,
                    "I can look that up, but I first need to verify you're authorized to hear "
                    "about this patient. I can call back on the number we have on file.",
                    "Puedo consultarlo, pero primero necesito verificar que está autorizado a "
                    "recibir esta información. Puedo llamarle al número que tenemos registrado.",
                ),
                actions=["no record action taken"],
                notes=["identity / proxy not verified"],
            )

        patient = verification.patient
        procedure = _extract_procedure(msg.text)
        status = self.epic.get_prior_auth(patient.patient_id, procedure)  # not tracked in Epic
        if status is None:
            status = self.payer.get_prior_auth_status(patient, procedure)  # payer portal

        proxy_note = " (released to authorized representative)" if verification.is_proxy else ""
        if status.status == "approved":
            appointment = self.epic.book_appointment(
                patient.patient_id, f"{procedure} - scheduling", "offered this week"
            )
            return AgentResult(
                msg.contact_id,
                Disposition.RESOLVED,
                lang,
                t(
                    lang,
                    f"Yes - the prior authorization for {procedure} was approved on "
                    f"{status.decided_on}. I can help schedule the appointment.",
                    f"Sí - la autorización previa para {procedure} fue aprobada el "
                    f"{status.decided_on}. Puedo ayudarle a programar la cita.",
                ),
                actions=[f"PA status via payer portal: {status.status}", "offered scheduling"],
                phi_released=True,
                notes=[f"source: payer, not Epic{proxy_note}"],
            )
        self.epic.create_in_basket_task(
            patient.patient_id, "Prior Auth", f"{procedure} status follow-up"
        )
        return AgentResult(
            msg.contact_id,
            Disposition.CALLBACK_PROMISED,
            lang,
            t(
                lang,
                f"I don't have a decision on {procedure} yet. I've asked our prior-auth team to "
                "follow up and someone will call you back.",
                f"Aún no tengo una decisión sobre {procedure}. Pedí a nuestro equipo de "
                "autorizaciones que dé seguimiento y alguien le llamará.",
            ),
            actions=["prior-auth task created", "callback promised"],
            phi_released=False,
            notes=[f"status not found{proxy_note}"],
        )

    def _new_patient(self, msg: InboundMessage, lang: Language, assessment: SafetyAssessment) -> AgentResult:
        # New-patient intake: no existing PHI to protect, but we still never triage.
        zip_code = _extract_zip(msg.text)
        appointment = self.epic.book_appointment(
            None, "New patient - establish care", "this week"
        )
        notes = [
            "out-of-area / snowbird: confirm the caller's plan is in network in Arizona",
            "no PHI: caller is new, nothing on file to protect",
        ]
        if zip_code:
            notes.append(f"caller location zip {zip_code}")
        return AgentResult(
            msg.contact_id,
            Disposition.BOOKED,
            lang,
            t(
                lang,
                "I can get you in this week at one of our Mesa-area clinics. Please bring a "
                "photo ID and your insurance card; we'll confirm your plan covers care in "
                "Arizona. If your cough gets worse - fever, trouble breathing - call us back "
                "right away.",
                "Puedo darle una cita esta semana en una de nuestras clínicas del área de Mesa. "
                "Traiga una identificación con foto y su tarjeta de seguro; confirmaremos que su "
                "plan cubre atención en Arizona. Si su tos empeora - fiebre, dificultad para "
                "respirar - llámenos de inmediato.",
            ),
            actions=[f"booked {appointment.appointment_id} (new patient)", "flag plan/network check"],
            notes=notes,
        )

    def _symptom(self, msg: InboundMessage, lang: Language, verification: Verification) -> AgentResult:
        # No red flag, but still clinical: a human decides, never the agent.
        return AgentResult(
            msg.contact_id,
            Disposition.ESCALATED_NURSE,
            lang,
            t(
                lang,
                "I'm not able to tell you whether you need to be seen - that's a question for a "
                "nurse. I'm getting a nurse to call you back today.",
                "No puedo decirle si necesita atención - esa es una pregunta para una enfermera. "
                "Voy a pedir que una enfermera le llame hoy.",
            ),
            actions=["nurse callback queued"],
            escalated_to="RN triage queue",
            notes=["no triage advice given by the agent"],
        )

    def _hours(self, msg: InboundMessage, lang: Language) -> AgentResult:
        return AgentResult(
            msg.contact_id,
            Disposition.RESOLVED,
            lang,
            t(
                lang,
                "Our clinics are open 8am-5pm Monday to Friday. For anything urgent after hours, "
                "we have a nurse line and an on-call clinician.",
                "Nuestras clínicas abren de 8am a 5pm de lunes a viernes. Para algo urgente "
                "fuera de horario, tenemos una línea de enfermería y un médico de guardia.",
            ),
        )

    def _fallback(self, msg: InboundMessage, lang: Language, verification: Verification) -> AgentResult:
        return AgentResult(
            msg.contact_id,
            Disposition.ESCALATED_NURSE,
            lang,
            t(
                lang,
                "Let me get a person from the clinic to help you with that.",
                "Permítame comunicarle con una persona de la clínica.",
            ),
            actions=["routed to front desk queue"],
            escalated_to="front desk",
            notes=["intent not confidently classified"],
        )


# --------------------------------------------------------------------------- #
# Fixtures: the five sample contacts (A-E)
# --------------------------------------------------------------------------- #
def build_registry() -> PatientRegistry:
    return PatientRegistry(
        [
            Patient(
                "P-1001", "Robert Alvarez", date(1960, 5, 2), Language.EN,
                ("+1 702-555-0111",), "4821", "89109",
            ),
            Patient(
                "P-1002", "Hector Salazar", date(1948, 11, 30), Language.EN,
                ("+1 702-555-0222",), "7734", "89119",
            ),
            Patient(
                "P-1003", "Jorge Castillo", date(1971, 2, 9), Language.ES,
                ("+1 520-555-0333",), "1190", "85711",
                representatives=(Representative("Marisol Castillo", date(1973, 7, 14), "spouse"),),
            ),
            Patient(
                "P-1004", "Dana Whitfield", date(1961, 3, 14), Language.EN,
                ("+1 612-555-0444",), "5522", "55401",
            ),
        ]
    )


def build_contacts() -> list[InboundMessage]:
    return [
        InboundMessage(
            "A", Channel.PHONE,
            "I need my lisinopril refilled. And my insurance changed on January 1st, "
            "I'm on a Humana plan now. Do I need to do anything?",
            IdentityClaim(
                channel=Channel.PHONE, from_number="+1 702-555-0111",
                dob=date(1960, 5, 2), knowledge_factor="4821",
            ),
        ),
        InboundMessage(
            "B", Channel.PORTAL_CHAT,
            "my dad was discharged from Desert Valley Medical Center yesterday for his heart "
            "failure. tonight he's more short of breath than this morning and his ankles look "
            "bigger. should I be worried",
            IdentityClaim(
                channel=Channel.PORTAL_CHAT, portal_authenticated=True,
                on_behalf_of="Hector Salazar",
            ),
        ),
        InboundMessage(
            "C", Channel.PHONE,
            "Hola, quiero saber si ya aprobaron la resonancia de mi esposo. "
            "El doctor la pidió hace dos semanas.",
            IdentityClaim(
                channel=Channel.PHONE, from_number="+1 520-555-0777",
                caller_name="Marisol Castillo", caller_dob=date(1973, 7, 14),
                on_behalf_of="Jorge Castillo",
            ),
            language=Language.ES,
        ),
        InboundMessage(
            "D", Channel.TEXT,
            "this is Dana Whitfield DOB 3/14/1961 can you text me my A1C results",
            IdentityClaim(
                channel=Channel.TEXT, from_number="+1 612-555-0999",
                dob=date(1961, 3, 14), otp_verified=False,
            ),
        ),
        InboundMessage(
            "E", Channel.PHONE,
            "I'm down here for the winter, staying in Mesa, 85206. Can somebody see me this "
            "week for a cough that won't go away? I've never been to you before.",
            IdentityClaim(channel=Channel.PHONE, from_number="+1 612-555-0456"),
        ),
    ]


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def print_result(result: AgentResult) -> None:
    print("-" * 74)
    print(f"Contact {result.contact_id}  |  disposition: {result.disposition.value}")
    print(f"  language   : {result.language.value}")
    if result.escalated_to:
        print(f"  escalated  : {result.escalated_to}")
    print(f"  phi        : {'released' if result.phi_released else 'NOT released'}")
    print(f"  reply      : {result.reply}")
    if result.actions:
        for action in result.actions:
            print(f"  action     : {action}")
    for note in result.notes:
        print(f"  note       : {note}")


def run_self_checks(results: list[AgentResult]) -> None:
    by_id = {r.contact_id: r for r in results}
    assert by_id["A"].disposition is Disposition.TASK_CREATED
    assert by_id["B"].disposition is Disposition.ESCALATED_EMERGENCY
    assert by_id["B"].phi_released is False
    assert by_id["C"].disposition is Disposition.RESOLVED
    assert by_id["C"].language is Language.ES
    assert by_id["D"].disposition is Disposition.REFUSED_PHI
    assert by_id["D"].phi_released is False
    assert by_id["E"].disposition is Disposition.BOOKED
    print("-" * 74)
    print("self-checks passed: B escalates, D refuses PHI, C answers in Spanish, A tasks, E books")


def main() -> None:
    registry = build_registry()
    epic = EpicGateway(registry)
    agent = Agent(registry, epic, PayerGateway(), PharmacyGateway(), LanguageLine())

    print("Larkspur Health agent - walking skeleton")
    print("Where the audio goes:", AUDIO_POLICY.transport)
    print("  ASR:", AUDIO_POLICY.asr)
    print("  raw audio persisted:", AUDIO_POLICY.persist_raw_audio,
          f"(opt-in retention: {AUDIO_POLICY.retention_opt_in_days}d)")

    results = [agent.handle(msg) for msg in build_contacts()]
    for result in results:
        print_result(result)

    run_self_checks(results)


if __name__ == "__main__":
    main()
