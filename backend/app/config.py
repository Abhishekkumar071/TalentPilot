"""Central settings. Every value can be overridden with an environment variable
(or a line in backend/.env). See .env.example for what each one means."""
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
# Also export .env to os.environ so libraries like LangSmith (LANGSMITH_*) see it.
load_dotenv(BACKEND_DIR / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # --- App ---
    APP_NAME: str = "TalentPilot"
    COMPANY_NAME: str = "Nimbus Technologies"
    PUBLIC_URL: str = "http://localhost:5173"  # where candidates open links
    ENV: str = "development"

    # --- HR login (single admin account) ---
    ADMIN_EMAIL: str = "hr@example.com"
    ADMIN_PASSWORD: str = "admin123"
    JWT_SECRET: str = "change-me-in-production"
    JWT_EXPIRE_HOURS: int = 24

    # --- Database: empty = local SQLite file. Paste Neon/Supabase URL in prod ---
    DATABASE_URL: str = ""
    DATA_DIR: str = str(BACKEND_DIR)  # where local SQLite files live

    # --- LLMs (free tiers) ---
    LLM_PROVIDER: str = "groq"  # groq | gemini | fake (fake = offline test mode)
    GROQ_API_KEY: str = ""
    GROQ_MODEL_SMART: str = "llama-3.3-70b-versatile"
    GROQ_MODEL_FAST: str = "llama-3.1-8b-instant"
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    EMBEDDING_MODEL: str = "gemini-embedding-001"  # used only if GEMINI_API_KEY set

    # --- Email: console | brevo | smtp ---
    EMAIL_PROVIDER: str = "console"
    EMAIL_FROM: str = ""
    EMAIL_FROM_NAME: str = "TalentPilot Recruiting"
    BREVO_API_KEY: str = ""
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    HR_NOTIFY_EMAIL: str = ""

    # --- Telegram (free): HR alerts + job channel posting ---
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_HR_CHAT_ID: str = ""
    TELEGRAM_CHANNEL_ID: str = ""

    # --- SMS (optional, Twilio trial) ---
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_NUMBER: str = ""

    # --- Cron endpoint protection ---
    CRON_SECRET: str = "change-me-cron"

    # --- Hiring policy knobs ---
    MAX_REPLANS: int = 2
    SCREEN_PASS_SCORE: float = 55.0
    ASSESSMENT_PASS_SCORE: float = 50.0
    ASSESSMENT_VALID_HOURS: int = 72
    INTERVIEW_VALID_HOURS: int = 72
    OFFER_VALID_HOURS: int = 120
    INTERVIEW_QUESTIONS: int = 6
    MAX_RESUME_MB: int = 5

    @property
    def is_postgres(self) -> bool:
        return self.DATABASE_URL.startswith(("postgres://", "postgresql"))

    @property
    def sqlalchemy_url(self) -> str:
        if not self.is_postgres:
            return f"sqlite:///{(Path(self.DATA_DIR) / 'talentpilot.db').as_posix()}"
        url = self.DATABASE_URL
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url

    @property
    def checkpoint_url(self) -> str:
        """Plain libpq URL for the LangGraph Postgres checkpointer."""
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return url.replace("postgresql+psycopg://", "postgresql://")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
