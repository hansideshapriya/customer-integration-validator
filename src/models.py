"""
models.py
---------
Schema definitions for the integration.

SourceCustomerRecord  -> shape of data coming out of the source CRM.
DestinationCustomerRecord -> shape of data the destination system expects.

Using Pydantic gives us:
  * automatic type coercion/rejection
  * declarative field constraints (length, enum values, email format)
  * structured, machine-readable error messages instead of raw exceptions

We deliberately keep validation "syntactic" here (types, formats, required
fields). Business rules that depend on *combinations* of fields (e.g.
"enterprise accounts need a company name") live in src/business_rules.py,
not in the model, so the two concerns stay easy to reason about separately.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


class CustomerStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    TRIAL = "trial"
    CHURNED = "churned"


class CustomerType(str, Enum):
    STANDARD = "standard"
    ENTERPRISE = "enterprise"
    PARTNER = "partner"


class SourceCustomerRecord(BaseModel):
    """
    Raw record as it arrives from the source system's API.

    Field names intentionally mirror the (messy) source schema
    (`email_address`, `customer_status`) rather than our internal
    naming, since this model's job is to describe what we RECEIVE.
    """

    customer_id: str = Field(..., min_length=1, max_length=64)
    first_name: str = Field(..., min_length=1, max_length=120)
    last_name: str = Field(..., min_length=1, max_length=120)
    email_address: EmailStr
    phone: Optional[str] = Field(default="", max_length=40)
    company_name: Optional[str] = Field(default="", max_length=200)
    website: Optional[str] = Field(default="", max_length=500)
    customer_status: CustomerStatus
    customer_type: CustomerType
    region: str = Field(..., min_length=1, max_length=40)
    notes: Optional[str] = Field(default="", max_length=5000)
    created_at: Optional[date] = None

    @field_validator("first_name", "last_name", mode="before")
    @classmethod
    def strip_names(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("customer_id", mode="before")
    @classmethod
    def customer_id_not_blank(cls, v):
        if v is None or str(v).strip() == "":
            raise ValueError("customer_id must not be blank")
        return str(v).strip()


class DestinationCustomerRecord(BaseModel):
    """
    Record shape required by the destination system.

    This is intentionally a different, simpler schema than the source
    system -- that mismatch is the core problem this project solves.
    """

    id: str
    name: str
    email: str
    status: str
    account_type: str
    region: str
    company: Optional[str] = ""
    website: Optional[str] = ""
    notes: Optional[str] = ""
