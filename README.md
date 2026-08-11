# Customer Integration Validator

**Live demo:** _[add your Streamlit Community Cloud URL here once deployed]_
**Repo:** _[this repo's GitHub URL]_

A working implementation-review tool that validates, cleans, and transforms customer
records before they're imported from a source SaaS system into a destination system —
the kind of tool an implementation/solutions engineer builds during a customer onboarding.

---

## 1. Business problem

Every SaaS implementation eventually hits the same wall: customer data arriving from
one system doesn't match what the destination system expects, and it isn't clean.
Emails have stray whitespace and inconsistent casing. The same customer shows up twice
under two different IDs. A required field is missing depending on account type. A
free-text notes field has a pasted API key in it, or worse, a script tag.

None of this is exotic — it's the normal, unglamorous reality of moving customer data
between systems. Get it wrong and you find out at go-live, in front of the customer.
This tool exists to catch it before that point.

## 2. Solution

The app pulls customer records from a source system, runs them through a pipeline of
independent checks (security scanning, schema validation, business rules, duplicate
detection), transforms the records that pass into the destination system's schema, and
optionally submits them — then produces a report an implementation specialist can use
to answer one question: **can this dataset safely go live?**

## 3. Architecture
SOURCE SYSTEM (mock CRM API)
↓
API / DATA INGESTION src/api_client.py
↓
SECURITY CHECKS src/security.py
↓
SCHEMA VALIDATION src/validators.py + src/models.py
↓
NORMALIZATION src/normalizer.py
↓
DUPLICATE DETECTION src/duplicates.py
↓
BUSINESS-RULE VALIDATION src/business_rules.py
↓
TRANSFORMATION src/transformer.py
↓
DESTINATION SYSTEM (mock API) src/destination_client.py
↓
INTEGRATION REPORT src/reporting.py
`src/pipeline.py` orchestrates all of the above into a single `run_pipeline()` call.
`app.py` is a thin Streamlit UI on top of it — it contains no business logic; every
validation/security/business-rule decision happens in `src/`, not in the UI layer.

For a portfolio deploy, the source and destination systems are two small FastAPI mock
APIs (`mock_apis/`), started in-process alongside the Streamlit app itself (see
**Architecture decisions** below). Pointed at a real CRM and a real destination system,
the same pipeline code runs unchanged.

## 4. Technical implementation

- **Python** throughout; typed where it clarifies intent, plain functions where a class
  would just add ceremony.
- **REST APIs**: `requests`-based clients for both source (GET, paginated) and
  destination (POST, per-record), with distinct retry/error semantics for each — a
  failed GET means "we have less data than expected"; a failed POST means "this
  specific record didn't make it."
- **Pydantic** schema validation (`src/models.py`) — type coercion, field constraints,
  and structured, serializable errors instead of raw exceptions.
- **Security checks** (`src/security.py`): script/markup injection, SQL-injection-shaped
  strings, unsafe URL schemes, private-IP/SSRF risk, leaked-credential patterns, and
  oversized fields — flagged with a severity, not silently sanitized.
- **Error handling**: typed exceptions per failure mode (`SourceAuthError`,
  `SourceUnavailableError`, etc.), so callers can react differently to "credentials are
  wrong" versus "the network is down" without parsing strings.
- **Logging**: structured logging via `src/logging_config.py`, with a `RedactingFilter`
  as a second layer of defense on top of call-site redaction — credentials and PII
  never reach a log file.
- **Testing**: `pytest`, covering pure functions, HTTP-mocked clients, and full
  pipeline runs.
- **UI**: Streamlit — five tabs (overview, rejected records, security warnings,
  transformed preview, integration report), each reading directly off one
  `PipelineRun` object.

## 5. Example scenario

`sample_data/customers.json` bundles 20 records deliberately built to exercise every
part of the pipeline in one run: a clean record, a record with leading/trailing
whitespace and mixed-case email, an exact-ID duplicate, a same-email/different-ID
potential duplicate, an XSS payload in a name field, a `javascript:` URL, a private-IP
URL, a SQL-injection-shaped note, a leaked API key in a notes field, a missing
customer_id, a null status, an enterprise account missing required fields, an
unsupported region, an inactive/churned status, and an oversized free-text field.

Running validation against this dataset produces a report showing roughly half the
records accepted and half rejected, with every rejection traceable to a specific,
human-readable reason — exactly the artifact an implementation specialist would take
into a go-live conversation.

## 6. Security considerations

**What this application does:**
- Never hard-codes credentials; reads `SOURCE_API_KEY` / `DESTINATION_API_TOKEN` from
  environment variables only (see `.env.example`).
- Flags script/markup injection, SQL-injection-shaped strings, unsafe URL schemes,
  private-IP/SSRF-risk URLs, likely leaked credentials, and oversized fields in
  incoming data.
- Redacts credentials and truncates PII (email, phone, notes) before anything is
  written to a log file, at two independent layers (call-site redaction +
  logging-level filter).
- Returns typed, user-safe error messages — no stack traces, tokens, or raw customer
  data are ever surfaced to the UI.

**What this application does NOT do** (stated explicitly, not glossed over):
- It does not sanitize or rewrite flagged data — it flags it. Escaping/sanitization
  decisions belong to whatever system ultimately renders or stores the data, since the
  correct approach is context-dependent.
- It is not a general-purpose security scanner. The pattern libraries in
  `src/security.py` are intentionally scoped to the risk categories relevant to a data
  import (stored XSS, SSRF, credential leakage) — not a substitute for a real
  secrets-scanning tool or a WAF.
- Its own bundled "destination system" is a mock. A real destination integration would
  need its own security review independent of this tool.

## 7. Running locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env

streamlit run app.py
```

That's it — with `USE_BUNDLED_MOCK_APIS=true` (the default in `.env.example`), the app
starts its own mock source/destination APIs in-process; no separate servers needed.

To point this at a real source/destination system instead, set
`USE_BUNDLED_MOCK_APIS=false` and fill in real `SOURCE_API_URL`,
`DESTINATION_API_URL`, and their credentials.

## 8. Testing

```bash
pytest
```

Covers pure functions (validators, normalizer, transformer, business rules,
duplicates, security), HTTP-mocked client behavior (retries, timeouts, auth failures,
rate limits, malformed responses), and full pipeline runs against representative
record sets.

## 9. Architecture decisions

- **Security scanning runs on raw records, independent of schema validation.** A
  record can fail validation and still be scanned for security issues — they're
  orthogonal concerns, and a record that's syntactically invalid can still leak a
  credential.
- **Only high-severity security warnings block transmission**; medium/low warnings
  (e.g. a private-IP URL) are surfaced but not auto-rejected, since they're often
  legitimate. This threshold is a judgment call an implementation would tune per
  customer, not a fixed rule.
- **Exact-ID duplicates: first occurrence kept, later ones rejected. Potential
  (email-based) duplicates are flagged, not auto-rejected** — they need a human to
  confirm whether it's really the same customer.
- **Source and destination HTTP clients are separate classes**, not a shared base
  class, because their failure semantics genuinely differ: GETs are safe to retry
  unconditionally; POSTs need idempotency-aware retry logic.
- **The mock APIs run in-process via background threads** (`mock_apis/runner.py`) so
  the portfolio deploy is a single free-tier process. This is a demo-deployment
  convenience, not a production pattern — a real source/destination system would never
  be bundled into your own process. The `USE_BUNDLED_MOCK_APIS` flag is what proves the
  rest of the pipeline doesn't depend on that trick.
- **Known limitation**: `reporting.py` recomputes validation/business-rule counts by
  string-matching prefixes on human-readable reason strings from `pipeline.py`, rather
  than `RecordOutcome` carrying structured `(category, detail)` data. Simpler, but
  fragile — a rewording in `pipeline.py` would silently break the report's counts. A
  more robust version would use structured failure objects throughout.

## 10. Future improvements

- OAuth2 for source/destination authentication instead of static bearer tokens
- Webhook-based ingestion instead of polling
- Asynchronous/batched processing for larger record volumes
- Fuzzy/probabilistic matching for duplicate detection (beyond exact ID / exact email)
- Additional destination connectors (the transformer's mapping table is built to
  support more than one destination schema)
- Persistent audit history across runs, not just the current session
- A human-approval workflow for potential (non-exact) duplicates before rejection
- AI-assisted field mapping when a new source system's schema doesn't match existing
  mapping logic
- AI-assisted remediation suggestions for rejected records (e.g. drafting the exact
  fix needed for a missing-field rejection)