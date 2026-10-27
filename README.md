# F1 Capstone Template

Starter repository for the Cadra F1 walking-skeleton capstone. Use OpenCode with the Cadra provider to build your solution, document your approach, and submit a public GitHub repo for evaluation.

## Prerequisites

- Python 3.11+
- [OpenCode](https://opencode.ai) ≥ 1.17.0
- A Cadra JWT (`CADRA_TOKEN`) from the F1 Setup page
- Your Cadra proxy URL (from the F1 Setup page)

## Setup

1. Clone this repo (or use it as a GitHub template).
2. Set the environment variables (both values come from the F1 Setup page):
   ```bash
   export CADRA_PROXY_URL=<your-proxy-url>   # e.g. https://your-proxy.example.com/v1
   export CADRA_TOKEN=<your-cadra-jwt>
   ```
   `opencode.json` reads both via `{env:…}` — no file edits needed.
3. Install OpenCode if not already installed (see [opencode.ai](https://opencode.ai)).
4. Run OpenCode in this directory:
   ```bash
   opencode
   ```
5. Complete `APPROACH.md` and implement your solution in `src/` (start with `src/solution.py`).
6. Push your work to a **public** GitHub repository.
7. Submit your repo URL on the F1 demo page.

## Running it

See [`EXECUTION.md`](EXECUTION.md) for how to run the agent and the test suite
(both are dependency-free).

## Python environment (optional)

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Project layout

```
.
├── opencode.json        # Cadra provider config (reads CADRA_PROXY_URL + CADRA_TOKEN from env)
├── APPROACH.md          # Your written approach (required for submission)
├── EXECUTION.md         # How to run the agent and the tests
├── diagram.svg          # Architecture diagram, linked from APPROACH.md
├── src/
│   └── solution.py      # Larkspur agent walking skeleton
├── tests/
│   └── test_solution.py # Test suite (standard library, no dependencies)
├── requirements.txt
└── README.md
```

## Tests

The test suite uses only the Python standard library, so it runs without
installing anything:

```bash
python3 -m unittest discover -s tests -v
```

It is also collectable by pytest (`python3 -m pytest tests -v`) if you have it.
It covers the safety gate, language/intent handling, the patient registry,
per-channel identity, the integration stubs, end-to-end handling of the five
sample contacts (A-E), and the safety/privacy controls.
