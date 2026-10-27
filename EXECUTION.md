# EXECUTION — how to run the project and its tests

Everything here runs with a plain Python interpreter. The agent
(`src/solution.py`) and its test suite (`tests/test_solution.py`) use **only the
standard library**, so there is nothing to install to run or verify the work.

- Design document: [`APPROACH.md`](APPROACH.md)
- Architecture diagram: [`diagram.svg`](diagram.svg)

---

## 1. Prerequisites

| Requirement | Notes |
|---|---|
| Python **3.11+** | Check with `python3 --version`. |
| A terminal | Any shell; commands below assume you are in the repository root. |

`requirements.txt` lists optional data-science packages (numpy, pandas, …). They
are **not** needed for the agent or the tests — the code is dependency-free.

> Run every command from the **repository root** (the folder containing
> `APPROACH.md` and `src/`). Running from inside `src/` will break the imports.

---

## 2. Run the agent (the demo)

```bash
python3 src/solution.py
```

This pushes the five sample contacts (A–E) through the full pipeline
(identity → safety gate → orchestration → integrations) and prints, for each
one: the disposition, the language, whether PHI was released, the reply, the
side effects, and the reasoning notes. It ends with a self-check line.

Expected output (abridged):

```
Larkspur Health agent - walking skeleton
Where the audio goes: encrypted SIP/TLS to telephony vendor under BAA
  ASR: HIPAA-eligible, US region, BAA, zero-retention, no vendor training
  raw audio persisted: False (opt-in retention: 30d)
--------------------------------------------------------------------------
Contact A  |  disposition: task_created
  ...
--------------------------------------------------------------------------
self-checks passed: B escalates, D refuses PHI, C answers in Spanish, A tasks, E books
```

Exit code `0` on success. A failed self-check raises `AssertionError` (non-zero
exit), so the command doubles as a smoke test.

### What each contact should resolve to

| # | Channel | Expected disposition | Why |
|---|---|---|---|
| A | Phone | `task_created` | Verified → eligibility check + refill task to the prescriber |
| B | Portal chat | `escalated_emergency` | Red flag caught before identity → on-call RN / 911, no PHI |
| C | Phone (ES) | `resolved` | Authorized representative verified → PA status from the payer |
| D | Text | `refused_phi` | Sender number not on file → nothing sent |
| E | Phone | `booked` | New-patient intake → booked, network check flagged |

---

## 3. Run the tests

Full suite:

```bash
python3 -m unittest discover -s tests -v
```

Expected result:

```
Ran 79 tests in ...s

OK
```

Run a single test module, or a single class:

```bash
# all tests in the module
python3 -m unittest tests.test_solution -v

# one group (e.g. the safety gate)
python3 -m unittest tests.test_solution.TestSafetyGate -v

# one individual test
python3 -m unittest tests.test_solution.TestSafetyGate.test_detects_crisis_english -v
```

`unittest` exits `0` when everything passes and non-zero when a test fails, so it
works directly in CI.

### Optional: run with pytest

If you prefer pytest, install it and point it at the folder:

```bash
python3 -m pip install pytest
python3 -m pytest tests -v
```

The suite is plain `unittest`, so pytest collects it unchanged. pytest is **not**
required.

---

## 4. Optional: virtual environment

Not needed for the agent or tests, but useful if you want the optional packages:

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python3 -m pip install -r requirements.txt
```

---

## 5. What the tests cover

| Test group | Focus |
|---|---|
| `TestNormalizePhone` | Phone normalisation edge cases |
| `TestSafetyGate` | Crisis, red-flag and clinical-question detection (English **and** Spanish) |
| `TestLanguageAndIntent` | Language detection and intent classification |
| `TestPatientRegistry` | Patient lookup and authorized representatives |
| `TestIdentityService` | Per-channel identity: portal, phone, text, proxy |
| `TestEpicGateway` / `TestPayerGateway` / `TestSmallGateways` | Integration stubs |
| `TestContactsEndToEnd` | Contacts A–E through the whole pipeline |
| `TestEdgeCases` | Safety/privacy controls (unverified PHI refusal, red-flag precedence, crisis routing, controlled substances) |

---

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'src'` | Run from the repository root, not from inside `src/` or `tests/`. |
| `No module named pytest` | You don't need it — use `python3 -m unittest discover -s tests -v`. |
| `python3: command not found` | Try `python` instead, or install Python 3.11+. |
| Test failure after editing `src/solution.py` | The tests pin the safety/privacy invariants on purpose; a failure usually means a control changed, not that the test is wrong. |

---

## 7. Repository map

```
.
├── APPROACH.md            # Design write-up (Sections A–E)
├── EXECUTION.md           # This file
├── diagram.svg            # Architecture diagram, linked from APPROACH.md
├── src/
│   └── solution.py        # The agent (walking skeleton)
├── tests/
│   └── test_solution.py   # Test suite
├── conftest.py            # Path shim so pytest can import src.solution
├── requirements.txt       # Optional packages (not required)
└── README.md
```
