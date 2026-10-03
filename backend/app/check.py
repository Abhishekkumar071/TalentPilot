"""Setup checker. Run after filling backend/.env:

    python -m app.check            # checks keys/connections
    python -m app.check --email you@gmail.com   # also sends a test email
"""
import argparse
import sys

from pydantic import BaseModel

from . import llm, notify, rag
from .config import settings


class Ping(BaseModel):
    ok: bool
    language: str


def line(ok: bool | None, label: str, detail: str = "") -> bool:
    mark = {True: "OK  ", False: "FAIL", None: "SKIP"}[ok]
    print(f"[{mark}] {label}{' - ' + detail if detail else ''}")
    return ok is not False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", help="send a test email to this address")
    args = parser.parse_args()
    good = True
    print(f"\n{settings.APP_NAME} setup check (company: {settings.COMPANY_NAME})\n")

    # Database
    try:
        from sqlalchemy import text

        from .db import engine
        with engine.connect() as c:
            c.execute(text("select 1"))
        good &= line(True, "Database", "Postgres" if settings.is_postgres else "local SQLite (dev only)")
    except Exception as e:
        good &= line(False, "Database", str(e)[:200])

    # LLMs
    for name, key, provider in (("Groq LLM", settings.GROQ_API_KEY, "groq"), ("Gemini LLM", settings.GEMINI_API_KEY, "gemini")):
        if not key:
            line(None, name, "key not set")
            continue
        saved = (settings.GROQ_API_KEY, settings.GEMINI_API_KEY)
        settings.GROQ_API_KEY = key if provider == "groq" else ""
        settings.GEMINI_API_KEY = key if provider == "gemini" else ""
        try:
            r = llm.structured(Ping, "Reply with ok=true.", "Say which programming language FastAPI uses.", tier="fast")
            good &= line(r.ok, name, f"structured output works (answered '{r.language}')")
        except Exception as e:
            good &= line(False, name, str(e)[:300])
        finally:
            settings.GROQ_API_KEY, settings.GEMINI_API_KEY = saved
    if not (settings.GROQ_API_KEY or settings.GEMINI_API_KEY):
        good &= line(False, "LLM", "set at least GROQ_API_KEY or GEMINI_API_KEY")

    # Embeddings
    if rag.embeddings_enabled():
        v = rag.embed_one("backend engineer salary band")
        good &= line(bool(v), "Gemini embeddings", f"{len(v)} dims" if v else "failed (keyword search still works)")
    else:
        line(None, "Embeddings", "no GEMINI_API_KEY - using BM25 keyword retrieval")

    # Notifications
    line(None if settings.EMAIL_PROVIDER == "console" else True, "Email provider", settings.EMAIL_PROVIDER)
    if args.email:
        ok = notify.send_email(args.email, f"{settings.APP_NAME} test email", "<p>Your email setup works! 🎉</p>")
        good &= line(ok, "Test email", f"sent to {args.email}" if ok else "failed - check server log")
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_HR_CHAT_ID:
        good &= line(notify.telegram("✅ TalentPilot is connected to Telegram"), "Telegram HR chat")
    else:
        line(None, "Telegram", "not configured (optional)")
    if settings.ENV == "production" and settings.JWT_SECRET.startswith("change-me"):
        good &= line(False, "JWT_SECRET", "set a long random secret in production")

    print("\nAll good!\n" if good else "\nFix the FAIL items above.\n")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
