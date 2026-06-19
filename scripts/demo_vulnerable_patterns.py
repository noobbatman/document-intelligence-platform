"""
GuardianCI Phase 8 — Live Demo: Intentional Vulnerability Showcase

This file contains four deliberately vulnerable code patterns. It is designed
to be opened as a pull request against main so GuardianCI can demonstrate
detection, inline commenting, compliance citation, and auto-fix PR generation
against real vulnerabilities.

DO NOT MERGE this file into main. Close the demo PR after the review is posted.
"""

from __future__ import annotations

# ── Vulnerability 1: Hardcoded API key ───────────────────────────────────────
# GuardianCI should flag this as CRITICAL (hardcoded secret).
# PCI-DSS 6.4.3 | SOC 2 CC6.1 | GDPR Art. 32
GEMINI_API_KEY = "AIzaSyDEMO-FAKE-KEY-00000000000000000000"


def get_gemini_client():
    # Secret is embedded at module level above — not loaded from environment.
    import os

    return os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY


# ── Vulnerability 2: SQL injection via f-string ───────────────────────────────
# GuardianCI should flag this as CRITICAL (SQL injection).
# PCI-DSS 6.2.4 | SOC 2 CC6.1 | GDPR Art. 32
def get_document(db, document_id: str) -> dict:
    # Unsafe: user-controlled document_id is interpolated directly into SQL.
    query = f"SELECT * FROM documents WHERE id = '{document_id}'"
    return db.execute(query).fetchone()


# ── Vulnerability 3: JWT alg=none bypass ─────────────────────────────────────
# GuardianCI should flag this as CRITICAL (JWT algorithm confusion).
# SOC 2 CC6.1 | GDPR Art. 32
def decode_token(token: str) -> dict:
    import base64
    import json

    # Accepts alg=none — allows forged tokens with no signature.
    header_b64 = token.split(".")[0] + "=="
    header = json.loads(base64.b64decode(header_b64))
    algorithm = header.get("alg", "none")
    if algorithm == "none":
        # No signature verification — token is trusted unconditionally.
        payload_b64 = token.split(".")[1] + "=="
        return json.loads(base64.b64decode(payload_b64))
    raise ValueError(f"Unsupported algorithm: {algorithm}")


# ── Vulnerability 4: TLS certificate verification disabled ───────────────────
# GuardianCI should flag this as WARN (TLS bypass).
# SOC 2 CC6.7 | GDPR Art. 32
def fetch_external_resource(url: str) -> bytes:
    import requests

    # verify=False disables TLS certificate validation — MITM-able.
    response = requests.get(url, verify=False, timeout=10)
    response.raise_for_status()
    return response.content
