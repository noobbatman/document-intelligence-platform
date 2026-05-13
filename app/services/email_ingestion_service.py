"""Email ingestion service — IMAP polling for document attachments.

Polls a configured IMAP mailbox, saves PDF/image attachments as documents,
and enqueues them for processing. Supports Gmail and any IMAP server.

Configuration (add to Settings):
    EMAIL_IMAP_HOST, EMAIL_IMAP_PORT, EMAIL_ADDRESS, EMAIL_PASSWORD
    EMAIL_POLL_INTERVAL_SECONDS, EMAIL_FOLDER, EMAIL_MAX_ATTACHMENTS_PER_RUN
"""

from __future__ import annotations

import email
import imaplib
import uuid
from datetime import UTC, datetime
from email.header import decode_header
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/tiff",
    "image/webp",
}
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}


def _decode_header_str(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


class EmailIngestionService:
    """Poll an IMAP mailbox and extract document attachments."""

    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def _config(self) -> dict[str, Any]:
        return {
            "host": getattr(self.settings, "email_imap_host", ""),
            "port": getattr(self.settings, "email_imap_port", 993),
            "address": getattr(self.settings, "email_address", ""),
            "password": getattr(self.settings, "email_password", ""),
            "folder": getattr(self.settings, "email_folder", "INBOX"),
            "max_per_run": getattr(self.settings, "email_max_attachments_per_run", 50),
        }

    def is_configured(self) -> bool:
        cfg = self._config
        return bool(cfg["host"] and cfg["address"] and cfg["password"])

    def poll(self) -> list[dict[str, Any]]:
        """Poll the mailbox. Returns list of saved attachment metadata dicts."""
        if not self.is_configured():
            logger.warning("email_ingestion_not_configured")
            return []

        cfg = self._config
        results: list[dict[str, Any]] = []

        try:
            conn = imaplib.IMAP4_SSL(cfg["host"], cfg["port"])
            conn.login(cfg["address"], cfg["password"])
            conn.select(cfg["folder"])

            # Fetch only UNSEEN messages with attachments
            _, msg_nums = conn.search(None, "UNSEEN")
            num_list = msg_nums[0].split() if msg_nums[0] else []

            processed = 0
            for num in num_list[-cfg["max_per_run"] :]:  # newest first, capped
                _, raw = conn.fetch(num, "(RFC822)")
                if not raw or not raw[0]:
                    continue
                msg = email.message_from_bytes(raw[0][1])
                subject = _decode_header_str(msg.get("Subject", ""))
                from_addr = _decode_header_str(msg.get("From", ""))

                attachments = self._extract_attachments(msg)
                for att in attachments:
                    saved = self._save_attachment(att, subject=subject, sender=from_addr)
                    if saved:
                        results.append(saved)
                        processed += 1
                        if processed >= cfg["max_per_run"]:
                            break

                # Mark as seen
                conn.store(num, "+FLAGS", "\\Seen")
                if processed >= cfg["max_per_run"]:
                    break

            conn.logout()
        except Exception as exc:
            logger.error("email_poll_failed", extra={"error": str(exc)})

        logger.info("email_poll_complete", extra={"attachments_found": len(results)})
        return results

    def _extract_attachments(self, msg: email.message.Message) -> list[dict]:
        attachments = []
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get("Content-Disposition") is None:
                continue
            mime = (part.get_content_type() or "").lower()
            fname = _decode_header_str(part.get_filename() or "")
            ext = Path(fname).suffix.lower()
            if mime not in ALLOWED_MIME_TYPES and ext not in ALLOWED_EXTENSIONS:
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            attachments.append(
                {
                    "filename": fname or f"email_attachment{ext}",
                    "mime": mime or f"application/{ext.lstrip('.')}",
                    "data": payload,
                }
            )
        return attachments

    def _save_attachment(self, att: dict, subject: str, sender: str) -> dict | None:
        settings = get_settings()
        try:
            uid = str(uuid.uuid4())
            ext = Path(att["filename"]).suffix or ".pdf"
            filename = f"{uid}{ext}"
            dest = Path(settings.upload_dir) / filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(att["data"])
            return {
                "stored_path": str(dest),
                "original_filename": att["filename"],
                "content_type": att["mime"],
                "source": "email",
                "sender": sender,
                "subject": subject,
                "ingested_at": datetime.now(UTC).isoformat(),
            }
        except Exception as exc:
            logger.error(
                "save_attachment_failed", extra={"error": str(exc), "filename": att["filename"]}
            )
            return None
