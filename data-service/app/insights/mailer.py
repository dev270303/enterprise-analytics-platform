"""SMTP delivery (works with Gmail app passwords, Outlook, Brevo, Mailgun SMTP, ...)."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import get_settings

log = logging.getLogger(__name__)


def send(subject: str, html: str, payload: dict) -> tuple[str | None, str]:
    s = get_settings()
    if not s.smtp_enabled:
        return None, "SKIPPED (SMTP not configured)"
    recipients = [r.strip() for r in (s.report_recipients or "").split(",") if r.strip()]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = s.report_from or s.smtp_user
    msg["To"] = ", ".join(recipients)
    msg.set_content("\n".join(["Daily Business Intelligence Report", "", *[f"- {b}" for b in payload["bullets"]],
                               "", s.public_app_url]))
    msg.add_alternative(html, subtype="html")
    try:
        context = ssl.create_default_context()
        if s.smtp_port == 465:
            with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=context, timeout=30) as smtp:
                if s.smtp_user:
                    smtp.login(s.smtp_user, s.smtp_password or "")
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
                smtp.starttls(context=context)
                if s.smtp_user:
                    smtp.login(s.smtp_user, s.smtp_password or "")
                smtp.send_message(msg)
        return ", ".join(recipients), "SENT"
    except Exception as exc:  # report, don't crash the job
        log.error("e-mail failed: %s", exc)
        return ", ".join(recipients), f"FAILED: {type(exc).__name__}: {str(exc)[:150]}"
