"""
mock_apis/source_api.py
------------------------
A minimal stand-in for a SaaS CRM's "list customers" endpoint.

Behavior it deliberately reproduces, because api_client.py has to handle
all of it:
  * Bearer-token auth (401 if missing/wrong -- SOURCE_API_KEY)
  * Pagination via ?page=&page_size=, with a `has_more` flag
  * Occasional 5xx/429 (toggle-able) so retry logic has something to
    retry against
  * Serves sample_data/customers.json, unmodified, as the "real" data

Run standalone:
    uvicorn mock_apis.source_api:app --port 8001 --reload
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock Source CRM API")

DATA_PATH = Path(__file__).resolve().parent.parent / "sample_data" / "customers.json"

# Set to a value like "0.1" via env var to make ~10% of requests fail with
# a 500, so you can watch api_client.py's retry/backoff behavior for real
# instead of just reading the code. Off by default so the demo is stable.
FAILURE_RATE = float(os.getenv("MOCK_SOURCE_FAILURE_RATE", "0"))

# The token the mock API will actually accept. In a real integration this
# would live in the CRM vendor's dashboard, not in code -- here it just
# needs to match whatever SOURCE_API_KEY is set to in .env.
EXPECTED_TOKEN = os.getenv("SOURCE_API_KEY", "demo-source-key")


def _load_records() -> list[dict]:
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@app.get("/customers")
def list_customers(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    authorization: str | None = Header(default=None),
):
    if authorization != f"Bearer {EXPECTED_TOKEN}":
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")

    if FAILURE_RATE and random.random() < FAILURE_RATE:
        raise HTTPException(status_code=503, detail="Simulated upstream outage.")

    records = _load_records()
    start = (page - 1) * page_size
    end = start + page_size
    page_records = records[start:end]
    has_more = end < len(records)

    return JSONResponse({
        "records": page_records,
        "page": page,
        "page_size": page_size,
        "total_records": len(records),
        "has_more": has_more,
    })


@app.get("/health")
def health():
    return {"status": "ok"}