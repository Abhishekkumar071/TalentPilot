"""Outbound messages: email (Brevo HTTP API / SMTP / console), Telegram, SMS.

Every function returns True/False and never raises - a failed email must not
crash an agent halfway through the hiring workflow.
"""
import html
import logging
import smtplib
from email.message import EmailMessage

import httpx

from .config import settings

log = logging.getLogger(__name__)


def _wrap_html(title: str, body_html: str, cta: tuple[str, str] | None = None) -> str:
    button = ""
    if cta:
        label, url = cta
        button = (f'<p style="margin:28px 0"><a href="{html.escape(url)}" style="background:#4f46e5;color:#fff;'
                  f'padding:12px 22px;border-radius:8px;text-decoration:none;font-weight:600">{html.escape(label)}</a></p>'
                  f'<p style="color:#64748b;font-size:12px">Or open: {html.escape(url)}</p>')
    return f"""<div style="font-family:Segoe UI,Arial,sans-serif;max-width:560px;margin:auto;color:#0f172a">
<h2 style="color:#4f46e5;margin-bottom:4px">{html.escape(settings.COMPANY_NAME)}</h2>
<h3 style="margin-top:0">{html.escape(title)}</h3>
{body_html}{button}
<hr style="border:none;border-top:1px solid #e2e8f0;margin-top:32px">
<p style="color:#94a3b8;font-size:12px">Sent by {html.escape(settings.APP_NAME)} on behalf of {html.escape(settings.COMPANY_NAME)} Talent Team.</p>
</div>"""


def send_email(to: str, subject: str, body_html: str, *, title: str | None = None,
               cta: tuple[str, str] | None = None) -> bool:
    content = _wrap_html(title or subject, body_html, cta)
    provider = settings.EMAIL_PROVIDER.lower()
    try:
        if provider == "brevo" and settings.BREVO_API_KEY:
            resp = httpx.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": settings.BREVO_API_KEY, "accept": "application/json"},
                json={"sender": {"email": settings.EMAIL_FROM, "name": settings.EMAIL_FROM_NAME},
                      "to": [{"email": to}], "subject": subject, "htmlContent": content},
                timeout=20,
            )
            resp.raise_for_status()
            return True
        if provider == "smtp" and settings.SMTP_USER:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM or settings.SMTP_USER}>"
            msg["To"] = to
            msg.set_content("Please view this email in an HTML-capable client.")
            msg.add_alternative(content, subtype="html")
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as s:
                s.starttls()
                s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                s.send_message(msg)
            return True
        log.info("[EMAIL:console] to=%s subject=%s cta=%s", to, subject, cta[1] if cta else "-")
        return True
    except Exception as exc:
        log.error("Email to %s failed: %s", to, exc)
        return False


def telegram(text: str, chat_id: str | None = None) -> bool:
    chat = chat_id or settings.TELEGRAM_HR_CHAT_ID
    if not (settings.TELEGRAM_BOT_TOKEN and chat):
        return False
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat, "text": text[:4000], "parse_mode": "HTML",
                  "disable_web_page_preview": False},
            timeout=20,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        log.error("Telegram send failed: %s", exc)
        return False


def sms(to: str, text: str) -> bool:
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_FROM_NUMBER and to):
        return False
    try:
        resp = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json",
            auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
            data={"From": settings.TWILIO_FROM_NUMBER, "To": to, "Body": text[:1500]},
            timeout=20,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        log.error("SMS to %s failed: %s", to, exc)
        return False


def notify_hr(text: str, link: str | None = None) -> None:
    """Alert HR on every configured channel (Telegram + email)."""
    url = f"{settings.PUBLIC_URL}{link}" if link else None
    telegram(f"🤖 <b>{html.escape(settings.APP_NAME)}</b>\n{html.escape(text)}" + (f"\n{url}" if url else ""))
    if settings.HR_NOTIFY_EMAIL:
        send_email(settings.HR_NOTIFY_EMAIL, f"[{settings.APP_NAME}] {text[:80]}",
                   f"<p>{html.escape(text)}</p>", cta=("Open dashboard", url) if url else None)


def notify_candidate(to_email: str, phone: str, subject: str, body_html: str,
                     cta: tuple[str, str] | None = None, sms_text: str | None = None) -> dict:
    ok_email = send_email(to_email, subject, body_html, cta=cta)
    ok_sms = sms(phone, sms_text) if sms_text and phone else False
    return {"email": ok_email, "sms": ok_sms}
