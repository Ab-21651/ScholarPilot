"""Sending approved email drafts.

Only the /api/drafts/{id}/approve endpoint calls this, after the student has
clicked approve. The agent itself can only create drafts.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from urllib.parse import quote

from .config import settings
from .models import EmailDraft


def smtp_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def mailto_link(draft: EmailDraft) -> str:
    to = draft.to_address or ""
    return f"mailto:{to}?subject={quote(draft.subject)}&body={quote(draft.body)}"


def send_draft(draft: EmailDraft, reply_to: str) -> None:
    if not draft.to_address:
        raise ValueError("The draft has no recipient address.")
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = draft.to_address
    msg["Reply-To"] = reply_to
    msg["Subject"] = draft.subject
    msg.set_content(draft.body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)
