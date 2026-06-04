"""Task 2.3 — minimal SMTP mail sending (stdlib only).

Local dev points at Mailpit (docker-compose, SMTP :1025, UI :8025) so
mails are caught, not delivered. Prod swaps SMTP_HOST/PORT/FROM.

Tests monkeypatch ``send_invitation_email`` so pytest never opens a
socket — see conftest. The real delivery is proven by a live smoke
against the Mailpit API.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.config import Settings


def send_invitation_email(
    to: str, token: str, role: str, settings: Settings
) -> None:
    link = f"{settings.app_base_url}/login?invite={token}"
    msg = EmailMessage()
    msg["Subject"] = "Du wurdest zu Decyra eingeladen"
    msg["From"] = settings.mail_from
    msg["To"] = to
    msg.set_content(
        "Hallo,\n\n"
        f"du wurdest als '{role}' zu einer Organisation auf Decyra "
        "eingeladen.\n\n"
        f"Registriere dich mit DIESER Email-Adresse ({to}), um beizutreten:\n"
        f"{link}\n\n"
        "Die Einladung ist an deine Email-Adresse gebunden.\n"
    )
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
        smtp.send_message(msg)
