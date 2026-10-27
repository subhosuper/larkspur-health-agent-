# APPROACH — Larkspur Health agent for Saguaro Family Medicine

_A design for one agent that answers phone, text and portal chat for Saguaro: what it decides alone, what it hands to a person, and where the audio goes. The judgement below is deliberately conservative on anything clinical._

---

## Section A — Questions

Ordered by how much the answer changes the design.

1. **Who is clinically accountable for triage, and will Saguaro's medical director sign written protocols with an RN accountable for every "you should be seen" decision?**
   → Decides whether the agent ever answers "do I need to come in," or only collects symptoms and routes. It also decides whether we are shipping an administrative agent or a regulated clinical-decision-support product.
2. **What is Saguaro's results-release policy — which results, on which channels, and does a clinician review before release?**
   → Decides contact D: whether the agent can ever speak a lab value, or only point to MyChart / an approved callback. Results release is a policy question before it is a technical one.
3. **What Epic access will we actually have in eight weeks — on FHIR R4 scopes, scheduling write (Cadence / Open Scheduling), In Basket task creation, MyChart?**
   → Decides read-vs-write, how much stays human-in-the-loop, and whether we need interoperability middleware to hit the date.
4. **Where do prior-authorization and eligibility statuses actually live — Epic, payer portals, or a clearinghouse?**
   → Decides whether contact C is an API lookup we can ship, or portal/RPA work that slips.
5. **What is acceptable identity proof per channel, and what is Saguaro's authorized-representative process?**
   → Decides the authentication steps, how we handle the spouse in C, and the hard refusal in D.
6. **After 5pm, who receives a handoff — Saguaro's nurse line, the on-call physician, or the existing answering service — with what hours and callback SLA?**
   → Decides the escalation target and whether we replace or sit in front of the answering service.
7. **What is Saguaro's audio and PHI retention policy, and will they accept transcribe-and-discard?**
   → The privacy officer's question. Decides whether we persist audio at all and which ASR deployment model is acceptable.
8. **What languages matter beyond Spanish, and do they have a language-line contract for human interpretation?**
   → Decides launch language scope, the human fallback path, and whether Spanish is mandatory at go-live (I will assume it is).
9. **Who is allowed to hear what — proxies, minors, and sensitive categories (behavioral health, HIV, substance use)?**
   → Decides scope guards beyond generic PHI; a spouse asking about a heart-failure diagnosis is exactly this case.
10. **What are the top call reasons and hourly volumes at the launch clinic, and the new-vs-established split?**
    → Decides the eight-week intent set and how many humans we need on the handoff queue.
11. **What scheduling rules constrain autonomy — new-patient visit lengths, templates, snowbird/out-of-area policy, self-pay?**
    → Decides how much the agent may book alone (contact E) versus route.
12. **What does success look like numerically, and who reviews transcripts and owns a bad outcome?**
    → Decides launch criteria, shadow-mode length, and what we monitor.

---

## Section B — Assumptions

Where I cannot wait for an answer.

- **We will not make clinical triage decisions.** _Why:_ "tell patients whether they need to come in" is a clinical act; without medical-director protocols and RN accountability it is unsafe and likely regulatory exposure (clinical decision support / practicing medicine). _If wrong_ (they insist anyway): scope becomes a clinician-owned, protocol-driven triage program with audit and insurance — later than eight weeks and out of the launch scope.
- **Saguaro is the covered entity; Larkspur is a Business Associate and every subprocessor signs a BAA.** _Why:_ the one privacy question tells us PHI handling is the gating concern. _If wrong_ (they want to run it in-house): we ship software into their environment and the deployment story changes.
- **Raw audio is transcribed and discarded by default; we persist only transcript + structured output, with clinic-controlled audio retention as an opt-in.** _Why:_ strongest answer to "where does the audio go" with the least breach surface. _If wrong_ (they require full recording): storage, consent, cost, and legal-discovery posture all change.
- **Spanish is required at launch for the top intents, with a human interpreter as fallback.** _Why:_ ~30% prefer Spanish and most clinics lack bilingual staff. _If wrong_ (English-only pilot): smaller scope, but we lose the clearest differentiator and contact C fails.
- **Epic integration in eight weeks is read-heavy plus a narrow write path (scheduling + In Basket tasks), likely via middleware rather than deep native API work.** _Why:_ Epic API enablement is the classic timeline killer. _If wrong_ (native APIs ready): we drop the middleware and gain richer writes — a bonus, not a plan.
- **Prior-auth status is not reliably in Epic and needs a payer-portal / clearinghouse integration.** _Why:_ PA status usually lives with the payer. _If wrong:_ contact C becomes trivial.
- **After-hours clinical escalation goes to a human, never the agent.** _Why:_ same reasoning as the first assumption. _If wrong:_ we would be owning telephone triage, which is a different product.
- **The launch is a single clinic; the other thirteen keep their current flows during the pilot.** _Why:_ the brief says one clinic in eight weeks. _If wrong_ (multi-site): number porting, routing, and change management balloon.
- **No PHI moves on an unverified channel, ever.** _Why:_ this is the whole point of contact D. _If wrong:_ it is not negotiable — this is a control, not a preference.
- **Outbound post-discharge calls are treatment communication under the care relationship, not marketing; we still honor opt-out and calling-time rules.** _Why:_ that is the usual regulatory reading for care follow-up. _If wrong_ (treated as marketing): TCPA consent and CMS Medicare Advantage communication rules reshape the outbound program.

---

## Section C — Solution design

### Design principles

1. **Administrative by default, clinical by exception.** The agent completes admin work; any clinical question leaves through a human.
2. **Verify before you speak.** No PHI on an unverified channel, on any channel.
3. **Deterministic where it counts.** Safety, identity, and eligibility are rule-based and auditable; the LLM handles language and summarization, not decisions.
4. **One patient, four doors.** Same identity, memory and policy across phone, text, chat and outbound.
5. **Escalate loudly.** When in doubt, hand to a human with full context — never reassure.

### Where the audio goes (the privacy officer's answer)

- Call audio travels over encrypted SIP/TLS to our telephony provider under a BAA (**Twilio** — HIPAA-eligible, BAA, number porting, redundancy).
- It is streamed to speech recognition that runs **HIPAA-eligible in a US region under a BAA with zero-retention and no training on our data** (e.g. AWS Transcribe Medical or Deepgram). A self-hosted option (NVIDIA Riva / Whisper) exists for teams who want audio to never leave their own VPC.
- **Default: transcribe-and-discard.** Raw audio is not persisted; only the transcript, structured slots, and the clinical summary are stored and written into Epic.
- **Opt-in:** if Saguaro wants recording for QA or liability, audio lands in Saguaro-controlled, encrypted storage with a set retention (e.g. 30 days) and audit logs — still never in a vendor training set.
- Every subprocessor appears in a **BAA registry the privacy officer can read**; minimum necessary and full audit logging throughout.

### Architecture

Layered pipeline: **channels → ingress & media → identity / consent / language → safety gate → orchestration → integrations → actions → human layer**, with a PHI store and audit trail alongside.

**→ Diagram:** `[diagram.svg](diagram.svg)` — how one contact moves through the system, including the audio/PHI path and the emergency branch.

### Channels, identity, consent

| Channel     | Identity                                                     | Notes                                                           |
| ----------- | ------------------------------------------------------------ | --------------------------------------------------------------- |
| Phone       | Caller ID + DOB + one knowledge factor                       | Recording notice; warm transfer; callback to the number on file |
| Text        | Number must be **on file**; OTP to that number               | No PHI in outbound SMS; register the number for 10DLC           |
| Portal chat | MyChart session = authenticated; proxy access set by Saguaro | Strongest identity; best channel for results                    |
| Outbound    | Consent on file + opt-out + calling windows                  | Treatment communication                                         |

### Safety and escalation

- **Red-flag / crisis classifier** (shortness of breath, chest pain, stroke signs, bleeding, heart-failure decompensation, self-harm → 988) sends the contact straight to the emergency path with **no advice given**.
- **Scope guard:** the agent never diagnoses, never triages, never interprets a result, never adjusts a dose.
- **Human path:** warm transfer to the on-call RN / nurse line with a context packet (who, why, identity verified, transcript); an In Basket task when no human is live; a paging fallback when nobody answers.
- Any scripted clinical content requires **medical-director and RN sign-off** before it ships.

### Systems integration

- **Epic (read):** on FHIR — patient, coverage, appointments, medications, service requests / documents. **(write):** appointment (Cadence / Open Scheduling) and In Basket tasks. **Redox** as middleware to hit eight weeks if native enablement slips.
- **Payer:** eligibility (270/271) and prior-auth status — **pVerify** or **Availity**.
- **Pharmacy:** formulary check against the patient's plan.
- **Language:** a language line for human interpretation when recognition or intent is uncertain.
- All under BAA, minimum necessary, fully audited.

### Contacts A–E

| #     | Path through the agent                                                                                                                                                                                                                                              | Autonomy                   | Handoff                             |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------- | ----------------------------------- |
| **A** | Authenticate → refill intent → check controlled / visit-needed / formulary against the new Humana plan → run eligibility → create prescriber In Basket task; tell the patient the request is with their care team, they may need a visit, and to update their card. | Capture and route only.    | Prescriber's In Basket queue.       |
| **B** | Heart-failure discharge + worsening breathlessness + swelling at 11:40pm → **red-flag path** → no reassurance → advise 911 if severe now, else warm transfer to the on-call RN 24/7 plus a task to the care team; document verbatim.                                | Escalate only.             | On-call RN, immediately.            |
| **C** | Spanish → identify caller and proxy authorization → look up PA status (Epic + payer) → if approved and proxy verified, say so and give the next step; otherwise create a task with a promised callback; interpreter fallback if wording is unclear.                 | Status lookup and routing. | Scheduler / prior-auth team.        |
| **D** | Number not on file + self-asserted DOB → refuse to send anything; do not confirm or deny the patient exists; offer MyChart or a callback to the number on file with verification.                                                                                   | None.                      | Human, only after verification.     |
| **E** | New-patient intake → verify plan network for Arizona and establish-care / self-pay policy → book a same-week new-patient visit if covered; screen cough red flags → routine booking if none, nurse/urgent if any.                                                   | Book within policy.        | Nurse if red flags, else scheduler. |

### Vendors that matter (and why)

- **Twilio** — telephony and SMS: BAA, number porting, redundancy.
- **ASR** — AWS Transcribe Medical or Deepgram under BAA (or self-hosted) so we can answer "where does the audio go" precisely.
- **LLM** — BAA-covered endpoint only (Azure OpenAI / Bedrock / Anthropic via BAA); no PHI to non-BAA APIs.
- **Redox** — health-system interoperability middleware, to avoid waiting on native Epic enablement.
- **pVerify / Availity** — eligibility and prior-auth lookups.
- **A language line** — human interpretation fallback for Spanish and beyond.

---

## Section D — Eight-week scope

**Ship at one clinic:**

- **After-hours and overflow phone answering** (5pm–8am and weekends) at a single clinic — the highest pain with the most contained risk, and no need to displace the front desk during the day.
- **A narrow intent set, English and Spanish:** book / reschedule for established patients, routine (non-controlled) refill requests, hours / location / "do you take my plan" eligibility, and a hard red-flag → human path.
- **The safety gate is the product, not a feature:** identity, consent, recording notice, and a designed escalation path from day one.
- **Read-only Epic plus a narrow write path** (scheduling and In Basket tasks) via middleware.
- **Human handoff** to the existing on-call / nurse line with full context; shadow-mode daytime overflow with 100% transcript review in weeks 1–2, then canary and ramp.
- **Guardrails:** kill switch, transcript sampling, red-flag recall audit, error dashboard.

**Leave for later:**

- Clinical triage / "do you need to come in" — a clinician-owned protocol set with RN accountability, piloted after launch, never in the first release.
- Results release (contact D beyond routing) — needs the results-release policy and clinician review first.
- Automated prior-auth status (contact C) unless a payer API already exists; otherwise RPA / portal work.
- Insurance-change auto-processing, the full fourteen-site rollout, portal-chat PHI workflows, and the outbound post-discharge program (regulatory plus clinical risk — pilot with nurse ownership).
- Spanish beyond the top intents; additional languages.

**Launch criteria:** red-flag recall at target on audit, zero PHI on unverified channels, escalation SLA met, containment on target intents, and clinician sign-off on every script.

---

## Section E — Next step

Before building any integration, I would run one week of shadow-mode transcripts from the launch clinic through the red-flag and identity classifiers to size the safety gate and measure how often the agent would genuinely have to hand off.
