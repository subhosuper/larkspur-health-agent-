"""Tests for the Larkspur Health agent walking skeleton (src/solution.py).

Run with the standard library (no dependencies required):

    python3 -m unittest discover -s tests -v

or, if pytest is installed:

    python3 -m pytest tests -v

The suite covers every layer of the pipeline - safety gate, language/intent
NLU, patient registry, per-channel identity, the integration stubs, the
end-to-end handling of contacts A-E, and the safety/privacy edge cases that
the design treats as controls rather than preferences.
"""

import unittest
from datetime import date

from src.solution import (
    Agent,
    Channel,
    Disposition,
    EpicGateway,
    IdentityClaim,
    IdentityService,
    InboundMessage,
    Intent,
    Language,
    LanguageLine,
    Medication,
    PatientRegistry,
    PayerGateway,
    PharmacyGateway,
    SafetyGate,
    build_contacts,
    build_registry,
    classify,
    detect_language,
    normalize_phone,
    t,
)


def make_message(contact_id, channel, text, claim, language=None):
    return InboundMessage(contact_id, channel, text, claim, language=language)


class AgentFixture(unittest.TestCase):
    """Base case wiring fresh (isolated) dependencies for each test."""

    def setUp(self):
        self.registry = build_registry()
        self.epic = EpicGateway(self.registry)
        self.payer = PayerGateway()
        self.pharmacy = PharmacyGateway()
        self.language_line = LanguageLine()
        self.agent = Agent(
            self.registry, self.epic, self.payer, self.pharmacy, self.language_line
        )


# --------------------------------------------------------------------------- #
# normalize_phone
# --------------------------------------------------------------------------- #
class TestNormalizePhone(unittest.TestCase):
    def test_strips_formatting(self):
        self.assertEqual(normalize_phone("+1 702-555-0111"), "7025550111")

    def test_keeps_last_ten_digits(self):
        self.assertEqual(normalize_phone("+1 (702) 555-0111"), "7025550111")

    def test_none_returns_none(self):
        self.assertIsNone(normalize_phone(None))

    def test_empty_returns_none(self):
        self.assertIsNone(normalize_phone(""))

    def test_short_number_kept(self):
        self.assertEqual(normalize_phone("12345"), "12345")


# --------------------------------------------------------------------------- #
# SafetyGate
# --------------------------------------------------------------------------- #
class TestSafetyGate(unittest.TestCase):
    def setUp(self):
        self.gate = SafetyGate()

    def test_clean_administrative_text_has_no_flags(self):
        a = self.gate.assess("I need my lisinopril refilled.")
        self.assertFalse(a.red_flag)
        self.assertFalse(a.crisis)
        self.assertFalse(a.clinical_question)

    def test_detects_red_flag_english(self):
        self.assertTrue(self.gate.assess("I can't breathe").red_flag)
        self.assertTrue(self.gate.assess("he has chest pain").red_flag)

    def test_detects_red_flag_spanish(self):
        self.assertTrue(self.gate.assess("no puedo respirar").red_flag)
        self.assertTrue(self.gate.assess("tengo dolor de pecho").red_flag)

    def test_detects_crisis_english(self):
        self.assertTrue(self.gate.assess("I want to die").crisis)

    def test_detects_crisis_spanish(self):
        self.assertTrue(self.gate.assess("quiero quitarme la vida").crisis)

    def test_detects_clinical_question(self):
        self.assertTrue(self.gate.assess("should I be worried").clinical_question)
        self.assertTrue(self.gate.assess("do I need to come in").clinical_question)

    def test_case_insensitive(self):
        self.assertTrue(self.gate.assess("SHORTNESS OF BREATH").red_flag)

    def test_contact_b_triggers_red_flag_and_clinical_question(self):
        text = (
            "my dad was discharged from Desert Valley Medical Center yesterday for his "
            "heart failure. tonight he's more short of breath than this morning and his "
            "ankles look bigger. should I be worried"
        )
        a = self.gate.assess(text)
        self.assertTrue(a.red_flag)
        self.assertTrue(a.clinical_question)
        self.assertIn("red-flag symptom", a.reasons)

    def test_contact_e_cough_is_not_a_red_flag(self):
        self.assertFalse(self.gate.assess("a cough that won't go away").red_flag)

    def test_reasons_are_populated(self):
        self.assertTrue(self.gate.assess("chest pain").reasons)


# --------------------------------------------------------------------------- #
# Language + intent NLU
# --------------------------------------------------------------------------- #
class TestLanguageAndIntent(unittest.TestCase):
    def test_detect_language_english(self):
        self.assertEqual(detect_language("I need a refill"), Language.EN)

    def test_detect_language_spanish(self):
        self.assertEqual(
            detect_language("Hola, quiero saber si aprobaron la resonancia"), Language.ES
        )

    def test_classify_contact_a_is_refill_and_insurance(self):
        intents = classify(
            "I need my lisinopril refilled. And my insurance changed, I'm on a "
            "Humana plan now."
        )
        self.assertIn(Intent.REFILL, intents)
        self.assertIn(Intent.INSURANCE, intents)

    def test_classify_contact_c_is_prior_auth(self):
        self.assertIn(Intent.PA_STATUS, classify("ya aprobaron la resonancia de mi esposo"))

    def test_classify_contact_d_is_results(self):
        self.assertIn(Intent.RESULTS, classify("can you text me my A1C results"))

    def test_classify_contact_e_is_new_patient_and_symptom(self):
        intents = classify("a cough that won't go away? I've never been to you before")
        self.assertIn(Intent.NEW_PATIENT, intents)
        self.assertIn(Intent.SYMPTOM, intents)

    def test_classify_hours(self):
        self.assertIn(Intent.HOURS, classify("what are your hours"))

    def test_classify_empty(self):
        self.assertEqual(classify(""), [])

    def test_localized_string_helper(self):
        self.assertEqual(t(Language.EN, "hello", "hola"), "hello")
        self.assertEqual(t(Language.ES, "hello", "hola"), "hola")


# --------------------------------------------------------------------------- #
# Patient registry
# --------------------------------------------------------------------------- #
class TestPatientRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = build_registry()

    def test_by_id(self):
        patient = self.registry.by_id("P-1001")
        self.assertIsNotNone(patient)
        self.assertEqual(patient.name, "Robert Alvarez")

    def test_by_id_unknown(self):
        self.assertIsNone(self.registry.by_id("P-9999"))

    def test_by_phone_formatted(self):
        self.assertEqual(self.registry.by_phone("(702) 555-0111").patient_id, "P-1001")

    def test_by_phone_unknown(self):
        self.assertIsNone(self.registry.by_phone("+1 000-000-0000"))

    def test_by_name_case_insensitive(self):
        self.assertEqual(self.registry.by_name("jorge castillo").patient_id, "P-1003")

    def test_representative_match(self):
        jorge = self.registry.by_name("Jorge Castillo")
        rep = jorge.find_representative("Marisol Castillo", date(1973, 7, 14))
        self.assertIsNotNone(rep)
        self.assertEqual(rep.relationship, "spouse")

    def test_representative_no_match(self):
        jorge = self.registry.by_name("Jorge Castillo")
        self.assertIsNone(jorge.find_representative("Marisol Castillo", date(1980, 1, 1)))


# --------------------------------------------------------------------------- #
# IdentityService
# --------------------------------------------------------------------------- #
class TestIdentityService(unittest.TestCase):
    def setUp(self):
        self.service = IdentityService(build_registry())

    def test_portal_authenticated(self):
        v = self.service.verify(IdentityClaim(channel=Channel.PORTAL_CHAT, portal_authenticated=True))
        self.assertTrue(v.verified)
        self.assertFalse(v.is_proxy)

    def test_portal_authenticated_with_proxy(self):
        v = self.service.verify(
            IdentityClaim(
                channel=Channel.PORTAL_CHAT,
                portal_authenticated=True,
                on_behalf_of="Hector Salazar",
            )
        )
        self.assertTrue(v.verified)
        self.assertTrue(v.is_proxy)
        self.assertEqual(v.patient.patient_id, "P-1002")

    def test_portal_not_authenticated(self):
        v = self.service.verify(IdentityClaim(channel=Channel.PORTAL_CHAT))
        self.assertFalse(v.verified)

    def test_phone_verified(self):
        v = self.service.verify(
            IdentityClaim(
                channel=Channel.PHONE,
                from_number="+1 702-555-0111",
                dob=date(1960, 5, 2),
                knowledge_factor="4821",
            )
        )
        self.assertTrue(v.verified)
        self.assertEqual(v.patient.patient_id, "P-1001")

    def test_phone_wrong_knowledge_factor(self):
        v = self.service.verify(
            IdentityClaim(
                channel=Channel.PHONE,
                from_number="+1 702-555-0111",
                dob=date(1960, 5, 2),
                knowledge_factor="0000",
            )
        )
        self.assertFalse(v.verified)

    def test_phone_unknown_number(self):
        v = self.service.verify(
            IdentityClaim(channel=Channel.PHONE, from_number="+1 000-000-0000")
        )
        self.assertFalse(v.verified)

    def test_phone_authorized_representative(self):
        v = self.service.verify(
            IdentityClaim(
                channel=Channel.PHONE,
                from_number="+1 520-555-0777",
                caller_name="Marisol Castillo",
                caller_dob=date(1973, 7, 14),
                on_behalf_of="Jorge Castillo",
            )
        )
        self.assertTrue(v.verified)
        self.assertTrue(v.is_proxy)
        self.assertEqual(v.patient.patient_id, "P-1003")

    def test_phone_representative_wrong_dob(self):
        v = self.service.verify(
            IdentityClaim(
                channel=Channel.PHONE,
                from_number="+1 520-555-0777",
                caller_name="Marisol Castillo",
                caller_dob=date(1980, 1, 1),
                on_behalf_of="Jorge Castillo",
            )
        )
        self.assertFalse(v.verified)

    def test_text_number_not_on_file(self):
        v = self.service.verify(
            IdentityClaim(channel=Channel.TEXT, from_number="+1 612-555-0999", dob=date(1961, 3, 14))
        )
        self.assertFalse(v.verified)

    def test_text_on_file_but_no_otp(self):
        v = self.service.verify(
            IdentityClaim(
                channel=Channel.TEXT,
                from_number="+1 612-555-0444",
                dob=date(1961, 3, 14),
                otp_verified=False,
            )
        )
        self.assertFalse(v.verified)

    def test_text_verified_with_otp(self):
        v = self.service.verify(
            IdentityClaim(
                channel=Channel.TEXT,
                from_number="+1 612-555-0444",
                dob=date(1961, 3, 14),
                otp_verified=True,
            )
        )
        self.assertTrue(v.verified)
        self.assertEqual(v.patient.patient_id, "P-1004")

    def test_unsupported_channel(self):
        v = self.service.verify(IdentityClaim(channel=Channel.OUTBOUND))
        self.assertFalse(v.verified)


# --------------------------------------------------------------------------- #
# Integration stubs
# --------------------------------------------------------------------------- #
class TestEpicGateway(unittest.TestCase):
    def setUp(self):
        self.epic = EpicGateway(build_registry())

    def test_get_coverage(self):
        self.assertEqual(self.epic.get_coverage("P-1001").plan_name, "Humana Gold Plus HMO")

    def test_get_coverage_unknown(self):
        self.assertIsNone(self.epic.get_coverage("P-9999"))

    def test_get_medications(self):
        meds = self.epic.get_medications("P-1001")
        self.assertEqual(meds[0].name, "lisinopril")

    def test_last_visit(self):
        self.assertIsNotNone(self.epic.last_visit("P-1001"))

    def test_prior_auth_not_tracked_in_epic(self):
        self.assertIsNone(self.epic.get_prior_auth("P-1003", "MRI brain"))

    def test_create_in_basket_task(self):
        task = self.epic.create_in_basket_task("P-1001", "Prescriber", "Refill")
        self.assertEqual(task.queue, "Prescriber")
        self.assertIn(task, self.epic.tasks)

    def test_book_appointment_for_new_patient(self):
        appt = self.epic.book_appointment(None, "New patient", "this week")
        self.assertEqual(appt.patient_id, "NEW")
        self.assertIn(appt, self.epic.appointments)


class TestPayerGateway(unittest.TestCase):
    def setUp(self):
        self.payer = PayerGateway()

    def test_check_eligibility(self):
        eligibility = self.payer.check_eligibility(None, "Humana")
        self.assertTrue(eligibility.active)
        self.assertTrue(eligibility.in_network)

    def test_prior_auth_approved(self):
        jorge = build_registry().by_id("P-1003")
        status = self.payer.get_prior_auth_status(jorge, "MRI brain")
        self.assertEqual(status.status, "approved")

    def test_prior_auth_not_found(self):
        jorge = build_registry().by_id("P-1003")
        status = self.payer.get_prior_auth_status(jorge, "CT abdomen")
        self.assertEqual(status.status, "not_found")


class TestSmallGateways(unittest.TestCase):
    def test_formulary_check(self):
        result = PharmacyGateway().formulary_check(Medication("lisinopril", "10 mg"), "Humana")
        self.assertTrue(result.covered)

    def test_language_line(self):
        self.assertIn("es", LanguageLine().request_interpreter(Language.ES))


# --------------------------------------------------------------------------- #
# End-to-end: contacts A-E
# --------------------------------------------------------------------------- #
class TestContactsEndToEnd(AgentFixture):
    def setUp(self):
        super().setUp()
        self.contacts = build_contacts()
        self.results = {m.contact_id: self.agent.handle(m) for m in self.contacts}

    def test_all_five_contacts_present(self):
        self.assertEqual(set(self.results), {"A", "B", "C", "D", "E"})

    def test_contact_a_refill_and_coverage(self):
        r = self.results["A"]
        self.assertIs(r.disposition, Disposition.TASK_CREATED)
        self.assertIs(r.language, Language.EN)
        self.assertTrue(r.phi_released)
        self.assertTrue(any("eligibility" in a for a in r.actions))
        self.assertTrue(any("refill task" in a for a in r.actions))
        self.assertTrue(any("visit" in n for n in r.notes))

    def test_contact_a_creates_prescriber_task(self):
        queues = [task.queue for task in self.epic.tasks]
        self.assertIn("Prescriber", queues)

    def test_contact_b_escalates_as_emergency(self):
        r = self.results["B"]
        self.assertIs(r.disposition, Disposition.ESCALATED_EMERGENCY)
        self.assertFalse(r.phi_released)
        self.assertIn("RN", r.escalated_to)
        self.assertTrue(any("warm transfer" in a for a in r.actions))
        self.assertTrue(any("no diagnosis" in n for n in r.notes))

    def test_contact_b_creates_urgent_task(self):
        self.assertIn("RN Triage", [task.queue for task in self.epic.tasks])

    def test_contact_c_answers_in_spanish(self):
        r = self.results["C"]
        self.assertIs(r.disposition, Disposition.RESOLVED)
        self.assertIs(r.language, Language.ES)
        self.assertTrue(r.phi_released)
        self.assertTrue(any("approved" in a for a in r.actions))
        self.assertTrue(any("authorized representative" in n for n in r.notes))

    def test_contact_d_refuses_phi(self):
        r = self.results["D"]
        self.assertIs(r.disposition, Disposition.REFUSED_PHI)
        self.assertFalse(r.phi_released)
        self.assertIn("no message sent", r.actions)

    def test_contact_d_does_not_leak_patient_identity(self):
        reply = self.results["D"].reply
        self.assertNotIn("Whitfield", reply)
        self.assertNotIn("A1C", reply)

    def test_contact_e_books_new_patient(self):
        r = self.results["E"]
        self.assertIs(r.disposition, Disposition.BOOKED)
        self.assertFalse(r.phi_released)
        self.assertTrue(any("new patient" in a for a in r.actions))
        self.assertTrue(any("85206" in n for n in r.notes))

    def test_no_phi_released_on_unverified_or_emergency(self):
        for contact_id in ("B", "D", "E"):
            with self.subTest(contact=contact_id):
                self.assertFalse(self.results[contact_id].phi_released)


# --------------------------------------------------------------------------- #
# Edge cases and safety/privacy controls
# --------------------------------------------------------------------------- #
class TestEdgeCases(AgentFixture):
    def test_refill_without_verification_promises_callback(self):
        m = make_message(
            "X1",
            Channel.PHONE,
            "I need a refill of my lisinopril",
            IdentityClaim(channel=Channel.PHONE, from_number="+1 000-000-0000"),
        )
        r = self.agent.handle(m)
        self.assertIs(r.disposition, Disposition.CALLBACK_PROMISED)
        self.assertEqual(self.epic.tasks, [])

    def test_prior_auth_without_verification_promises_callback(self):
        m = make_message(
            "X2",
            Channel.PHONE,
            "has my MRI been approved yet",
            IdentityClaim(channel=Channel.PHONE, from_number="+1 000-000-0000"),
        )
        r = self.agent.handle(m)
        self.assertIs(r.disposition, Disposition.CALLBACK_PROMISED)
        self.assertFalse(r.phi_released)

    def test_results_on_verified_text_never_sends_phi(self):
        m = make_message(
            "X3",
            Channel.TEXT,
            "can we text over my A1C results",
            IdentityClaim(
                channel=Channel.TEXT,
                from_number="+1 612-555-0444",
                dob=date(1961, 3, 14),
                otp_verified=True,
            ),
        )
        r = self.agent.handle(m)
        self.assertIs(r.disposition, Disposition.CALLBACK_PROMISED)
        self.assertFalse(r.phi_released)

    def test_crisis_routes_to_988(self):
        m = make_message(
            "X4",
            Channel.PHONE,
            "I want to die",
            IdentityClaim(channel=Channel.PHONE, from_number="+1 000-000-0000"),
        )
        r = self.agent.handle(m)
        self.assertIs(r.disposition, Disposition.ESCALATED_CRISIS)
        self.assertIn("988", r.reply)

    def test_red_flag_beats_an_administrative_intent(self):
        m = make_message(
            "X5",
            Channel.PHONE,
            "I need a refill but I have chest pain right now",
            IdentityClaim(
                channel=Channel.PHONE,
                from_number="+1 702-555-0111",
                dob=date(1960, 5, 2),
                knowledge_factor="4821",
            ),
        )
        r = self.agent.handle(m)
        self.assertIs(r.disposition, Disposition.ESCALATED_EMERGENCY)
        self.assertEqual(self.epic.tasks[0].queue, "RN Triage")

    def test_red_flag_without_identity_still_escalates(self):
        m = make_message(
            "X6",
            Channel.PHONE,
            "my husband is short of breath",
            IdentityClaim(channel=Channel.PHONE, from_number="+1 000-000-0000"),
        )
        r = self.agent.handle(m)
        self.assertIs(r.disposition, Disposition.ESCALATED_EMERGENCY)

    def test_spanish_emergency_reply_is_spanish(self):
        m = make_message(
            "X7",
            Channel.PHONE,
            "no puede respirar",
            IdentityClaim(channel=Channel.PHONE, from_number="+1 000-000-0000"),
            language=Language.ES,
        )
        r = self.agent.handle(m)
        self.assertIs(r.disposition, Disposition.ESCALATED_EMERGENCY)
        self.assertIn("911", r.reply)
        self.assertIn("enfermera", r.reply.lower())

    def test_clinical_question_without_red_flag_goes_to_nurse(self):
        m = make_message(
            "X8",
            Channel.PHONE,
            "I have a fever, do I need to come in",
            IdentityClaim(channel=Channel.PHONE, from_number="+1 000-000-0000"),
        )
        r = self.agent.handle(m)
        self.assertIs(r.disposition, Disposition.ESCALATED_NURSE)
        self.assertEqual(r.escalated_to, "RN triage queue")

    def test_hours_question(self):
        m = make_message(
            "X9",
            Channel.TEXT,
            "what are your hours",
            IdentityClaim(channel=Channel.TEXT, from_number="+1 000-000-0000"),
        )
        r = self.agent.handle(m)
        self.assertIs(r.disposition, Disposition.RESOLVED)

    def test_unclassified_message_routes_to_front_desk(self):
        m = make_message(
            "X10",
            Channel.PHONE,
            "ummm",
            IdentityClaim(channel=Channel.PHONE, from_number="+1 000-000-0000"),
        )
        r = self.agent.handle(m)
        self.assertIs(r.disposition, Disposition.ESCALATED_NURSE)
        self.assertEqual(r.escalated_to, "front desk")

    def test_new_patient_with_red_flag_is_not_booked(self):
        m = make_message(
            "X11",
            Channel.PHONE,
            "I'm new here and I have chest pain",
            IdentityClaim(channel=Channel.PHONE, from_number="+1 612-555-0456"),
        )
        r = self.agent.handle(m)
        self.assertIs(r.disposition, Disposition.ESCALATED_EMERGENCY)
        self.assertEqual(self.epic.appointments, [])

    def test_controlled_medication_is_flagged(self):
        self.epic._medications["P-1001"] = [Medication("oxycodone", "5 mg", controlled=True)]
        m = make_message(
            "X12",
            Channel.PHONE,
            "I need a refill of my oxycodone",
            IdentityClaim(
                channel=Channel.PHONE,
                from_number="+1 702-555-0111",
                dob=date(1960, 5, 2),
                knowledge_factor="4821",
            ),
        )
        r = self.agent.handle(m)
        self.assertIs(r.disposition, Disposition.TASK_CREATED)
        self.assertTrue(any("controlled substance" in n for n in r.notes))

    def test_prior_auth_pending_creates_follow_up_task(self):
        # Authorization not found for a different procedure -> task + callback.
        m = make_message(
            "X13",
            Channel.PHONE,
            "did they approve my CT scan",
            IdentityClaim(
                channel=Channel.PHONE,
                from_number="+1 520-555-0333",
                dob=date(1971, 2, 9),
                knowledge_factor="1190",
            ),
        )
        r = self.agent.handle(m)
        self.assertIs(r.disposition, Disposition.CALLBACK_PROMISED)
        self.assertIn("Prior Auth", [task.queue for task in self.epic.tasks])

    def test_phi_invariant_across_all_contacts(self):
        """No contact that is unverified or clinical may release PHI."""
        for m in build_contacts():
            r = self.agent.handle(m)
            with self.subTest(contact=m.contact_id, disposition=r.disposition.value):
                if r.disposition in {
                    Disposition.REFUSED_PHI,
                    Disposition.ESCALATED_EMERGENCY,
                    Disposition.ESCALATED_CRISIS,
                    Disposition.ESCALATED_NURSE,
                }:
                    self.assertFalse(r.phi_released)


if __name__ == "__main__":
    unittest.main(verbosity=2)
