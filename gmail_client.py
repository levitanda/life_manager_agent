"""Gmail: fetch unread emails and send messages."""

import base64
import logging
from email.mime.text import MIMEText

from googleapiclient.discovery import build

import google_auth

logger = logging.getLogger(__name__)


def _decode_body(payload: dict) -> str:
    """Extract plain-text body from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        text = _decode_body(part)
        if text:
            return text
    return ""


def get_unread_emails(max_results: int = 15) -> list[dict]:
    """
    Return up to max_results unread inbox emails from the last 2 days.
    Each item: {from, subject, snippet, body_preview}
    """
    try:
        svc = build("gmail", "v1", credentials=google_auth.get_credentials())
        result = (
            svc.users()
            .messages()
            .list(userId="me", q="is:unread in:inbox newer_than:2d", maxResults=max_results)
            .execute()
        )
        refs = result.get("messages", [])
        if not refs:
            return []

        emails = []
        for ref in refs:
            try:
                msg = (
                    svc.users()
                    .messages()
                    .get(userId="me", id=ref["id"], format="full")
                    .execute()
                )
                headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
                body = _decode_body(msg["payload"])
                emails.append(
                    {
                        "from": headers.get("From", ""),
                        "subject": headers.get("Subject", "(без темы)"),
                        "snippet": msg.get("snippet", ""),
                        "body_preview": body[:400].strip(),
                    }
                )
            except Exception as e:
                logger.warning("Failed to fetch email %s: %s", ref["id"], e)

        return emails

    except Exception as e:
        logger.error("Gmail fetch failed: %s", e)
        return []


def send_email(to: str, subject: str, body: str) -> None:
    """Send an email from the user's Gmail account."""
    svc = build("gmail", "v1", credentials=google_auth.get_credentials())
    msg = MIMEText(body, "plain", "utf-8")
    msg["to"] = to
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()
