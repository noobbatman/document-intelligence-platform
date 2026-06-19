"""
Intentional security vulnerabilities for GuardianCI v0.1.0 upgrade demo.
DO NOT ship this file — it exists solely to validate the AI review pipeline.
"""

import sqlite3
import subprocess

import requests

# ── CRITICAL: hardcoded credentials ────────────────────────────────────────
DATABASE_URL = "postgresql://admin:Sup3rS3cret!@prod-db.internal:5432/docintel"
OPENAI_API_KEY = "sk-proj-aabbccddeeff00112233445566778899aabbccddeeff0011"
JWT_SECRET = "my-totally-secret-jwt-key-do-not-share"

# ── CRITICAL: SQL injection ─────────────────────────────────────────────────
def get_document(conn: sqlite3.Connection, document_id: str) -> dict:
    query = f"SELECT * FROM documents WHERE id = '{document_id}'"
    cursor = conn.execute(query)
    return dict(cursor.fetchone())


# ── CRITICAL: command injection ─────────────────────────────────────────────
def convert_pdf(filepath: str) -> str:
    output = subprocess.check_output(f"pdftotext {filepath} -", shell=True)
    return output.decode()


# ── CRITICAL: JWT algorithm confusion (alg=none) ────────────────────────────
def decode_token(token: str) -> dict:
    import base64
    import json

    header_b64 = token.split(".")[0]
    header = json.loads(base64.b64decode(header_b64 + "=="))
    if header.get("alg", "").lower() in ("none", ""):
        # Accept unsigned tokens — auth bypass
        payload_b64 = token.split(".")[1]
        return json.loads(base64.b64decode(payload_b64 + "=="))
    raise ValueError("Unexpected alg")


# ── WARN: disabled TLS verification ────────────────────────────────────────
def fetch_external_data(url: str) -> dict:
    response = requests.get(url, verify=False, timeout=30)
    response.raise_for_status()
    return response.json()


# ── WARN: overly permissive CORS ────────────────────────────────────────────
CORS_ORIGINS = ["*"]


# ── WARN: debug logging with sensitive data ─────────────────────────────────
def process_user_request(user_id: str, password: str) -> None:
    print(f"[DEBUG] Processing request for user={user_id} password={password}")
