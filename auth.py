"""Explicit demo mode or provisioned bearer credentials. Never infer identity from chat."""

import hashlib
import json
import os
import secrets
from dataclasses import dataclass

from fastapi import Header, HTTPException


@dataclass(frozen=True)
class Principal:
    subject: str
    role: str
    customer_id: int | None = None


def demo_mode():
    return os.environ.get("DEMO_MODE", "false").lower() == "true"


def credentials():
    """Configuration contains SHA-256 digests, never raw access tokens."""
    try:
        rows = json.loads(os.environ.get("SUPPORT_CREDENTIALS", "[]"))
        if not isinstance(rows, list):
            raise ValueError()
        for row in rows:
            if (not isinstance(row, dict)
                    or row.get("role") not in ("customer", "reviewer")
                    or not isinstance(row.get("subject"), str) or not row["subject"]
                    or not isinstance(row.get("sha256"), str)
                    or len(row["sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in row["sha256"])
                    or (row["role"] == "customer" and
                        (type(row.get("customer_id")) is not int or row["customer_id"] < 1))):
                raise ValueError()
        if len({r["sha256"] for r in rows}) != len(rows):
            raise ValueError()
        return rows
    except (ValueError, TypeError, KeyError):
        raise HTTPException(503, "Invalid authentication configuration") from None


def principal(authorization: str | None = Header(default=None)) -> Principal:
    if demo_mode():
        return Principal("local-demo", "demo")
    rows = credentials()
    if not rows:
        raise HTTPException(503, "Configure SUPPORT_CREDENTIALS or explicitly enable DEMO_MODE")
    scheme, _, token = (authorization or "").partition(" ")
    digest = hashlib.sha256(token.encode()).hexdigest()
    for row in rows:
        if scheme.lower() == "bearer" and token and secrets.compare_digest(digest, row["sha256"]):
            return Principal(row["subject"], row["role"], row.get("customer_id"))
    raise HTTPException(401, "Invalid or missing access token", headers={"WWW-Authenticate": "Bearer"})


def require_role(user: Principal, role: str):
    if user.role not in (role, "demo"):
        raise HTTPException(403, "This operation requires the " + role + " role")
