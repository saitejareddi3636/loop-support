"""Shared backend configuration."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
PLACEHOLDER_KEY = "replace-with-your-google-ai-studio-key"


def configured_api_key() -> str | None:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    return key if key and key != PLACEHOLDER_KEY else None


def validate_runtime_config():
    from urllib.parse import urlsplit, parse_qs

    environment = os.getenv("APP_ENV", "local")
    if environment not in ("local", "production"):
        raise RuntimeError("APP_ENV must be local or production")
    if environment != "production":
        return
    if not configured_api_key():
        raise RuntimeError("Production requires a configured Gemini API key")
    if len(os.getenv("SESSION_RATE_LIMIT_SECRET", "")) < 32:
        raise RuntimeError("Production requires a strong SESSION_RATE_LIMIT_SECRET")
    database = urlsplit(os.getenv("DATABASE_URL", ""))
    if (
        database.scheme not in ("postgresql", "postgres")
        or not database.password
        or database.password in ("loopp", "postgres", "changeme")
    ):
        raise RuntimeError("Production requires dedicated PostgreSQL credentials")
    if parse_qs(database.query).get("sslmode") != ["verify-full"]:
        raise RuntimeError("Production database connections require sslmode=verify-full")
    origins = [
        value.strip() for value in os.getenv("ALLOWED_ORIGINS", "").split(",") if value.strip()
    ]
    if not origins or any(
        urlsplit(value).scheme != "https" or not urlsplit(value).netloc or "*" in value
        for value in origins
    ):
        raise RuntimeError("Production requires explicit HTTPS ALLOWED_ORIGINS")
