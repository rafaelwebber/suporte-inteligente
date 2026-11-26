import os
from pathlib import Path
from dotenv import load_dotenv
from knowledge_search.config import Settings
from knowledge_search.generation import GenerationSettings
from knowledge_search.openai_provider import OpenAISettings

load_dotenv()

DEFAULT_SECRET_KEY = "dev-secret-change-me"


def _csv_env(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


class Config:
    KNOWLEDGE_LLM_PROVIDER = os.getenv("KNOWLEDGE_LLM_PROVIDER", "gemini").strip().lower()
    GEMINI_SETTINGS = GenerationSettings.from_env()
    OPENAI_SETTINGS = OpenAISettings.from_env()
    KNOWLEDGE_SEARCH_SETTINGS = Settings.from_env()
    KNOWLEDGE_CONVERSATION_DATABASE = Path(os.getenv(
        "KNOWLEDGE_CONVERSATION_DATABASE",
        str(KNOWLEDGE_SEARCH_SETTINGS.index.parent / "conversations.sqlite3"),
    ))
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", DEFAULT_SECRET_KEY)
    DEBUG = os.getenv("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_UPLOAD_MB", "10")) * 1024 * 1024

    # Origens extras liberadas no CORS (o front servido pelo próprio Flask não precisa).
    CORS_ORIGINS = _csv_env("CORS_ORIGINS")
    # Quantos proxies reversos (nginx etc.) ficam na frente da API; 0 = ignora X-Forwarded-For.
    TRUSTED_PROXIES = int(os.getenv("TRUSTED_PROXIES", "0"))

    FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS", "")
    FIREBASE_WEB_API_KEY = os.getenv("FIREBASE_WEB_API_KEY", "")

    LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
    LOGIN_MAX_ATTEMPTS_PER_IP = int(os.getenv("LOGIN_MAX_ATTEMPTS_PER_IP", "20"))
    LOGIN_LOCK_MINUTES = int(os.getenv("LOGIN_LOCK_MINUTES", "15"))
    PASSWORD_MIN_LENGTH = int(os.getenv("PASSWORD_MIN_LENGTH", "8"))

    # Ticket resolvido conta como "com êxito" se não foi reaberto e, quando há nota, ela é >= este valor (1-5).
    TICKET_SUCCESS_MIN_CSAT = int(os.getenv("TICKET_SUCCESS_MIN_CSAT", "4"))
    SUGGESTIONS_LIMIT = int(os.getenv("SUGGESTIONS_LIMIT", "5"))
