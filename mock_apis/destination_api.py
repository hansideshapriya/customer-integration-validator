"""
mock_apis/destination_api.py
------------------------------
A minimal stand-in for the destination system's "create customer"
endpoint.

Deliberately returns a realistic mix of outcomes per request, driven
off the payload itself where possible (not just randomly) so a demo
run produces a believable, explainable integration report rather than
random noise:

  * 201  -- normal success
  * 401  -- missing/wrong bearer token (DESTINATION_API_TOKEN)
  * 409  -- id already "exists" (simulated via a small in-memory set,
            so re-running the same batch twice shows conflict handling)
  * 422  -- payload fails the destination's OWN validation (e.g. empty
            name/email) -- this is deliberately a *different* rule set
            than our source-side validation, because a real downstream
            system's validation is never guaranteed to match yours
  * 429  -- rate limited, roughly 1 in N requests, with a Retry-After
            header so destination_client.py's backoff has something
            real to respond to
  * 500  -- occasional simulated server error

Run standalone:
    uvicorn mock_apis.destination_api:app --port 8002 --reload
"""
from __future__ import annotations

import os
import random

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock Destination System API")

EXPECTED_TOKEN = os.getenv("DESTINATION_API_TOKEN", "demo-destination-token")

# Simulated "already imported" records, so a second run of the same batch
# demonstrates 409 handling without any extra setup.
_seen_ids: set[str] = set()

RATE_LIMIT_EVERY_N = 7  # every 7th request gets a 429
_request_count = 0


@app.post("/customers")
async def create_customer(request: Request, authorization: str | None = Header(default=None)):
    global _request_count

    if authorization != f"Bearer {EXPECTED_TOKEN}":
        raise HTTPException(status_code=401, detail="Invalid or missing API token.")

    _request_count += 1
    if _request_count % RATE_LIMIT_EVERY_N == 0:
        return JSONResponse(
            status_code=429,
            content={"error": "Rate limit exceeded. Slow down and retry."},
            headers={"Retry-After": "1"},
        )

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON body.")

    record_id = payload.get("id")
    name = (payload.get("name") or "").strip()
    email = (payload.get("email") or "").strip()

    # Destination-side validation -- intentionally simple and NOT the same
    # rules as our source-side Pydantic model, to demonstrate that
    # "passed our validation" and "accepted by the destination" are two
    # different guarantees.
    if not name or not email or "@" not in email:
        return JSONResponse(
            status_code=422,
            content={"error": "name and a valid email are required by the destination system."},
        )

    if record_id in _seen_ids:
        return JSONResponse(
            status_code=409,
            content={"error": f"Customer '{record_id}' already exists in the destination system."},
        )

    # Small simulated flakiness independent of rate limiting.
    if random.random() < 0.03:
        raise HTTPException(status_code=500, detail="Simulated internal server error.")

    _seen_ids.add(record_id)
    return JSONResponse(status_code=201, content={"id": record_id, "status": "created"})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/_reset")
def reset_state():
    """Test/demo helper only -- clears the simulated 'already imported' set."""
    _seen_ids.clear()
    global _request_count
    _request_count = 0
    return {"status": "reset"}