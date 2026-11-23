"""Environment-based settings. Real secrets belong in .env, never in Git."""

import os
from datetime import timedelta
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def database_url(value):
    """Hosted Postgres providers give postgres:// URLs; SQLAlchemy needs the psycopg driver name."""
    for prefix in ("postgres://", "postgresql://"):
        if value.startswith(prefix):
            return "postgresql+psycopg://" + value[len(prefix):]
    return value


def engine_options(url):
    if url.startswith("sqlite"):
        return {"connect_args": {"timeout": 30}}
    # Serverless hosts freeze idle workers; check connections before reuse.
    return {"pool_pre_ping": True, "pool_recycle": 300}


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "")
    SQLALCHEMY_DATABASE_URI = database_url(
        os.getenv("DATABASE_URL", f'sqlite:///{BASE_DIR / "instance" / "expenses.db"}')
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = engine_options(SQLALCHEMY_DATABASE_URI)
    # Number of trusted proxies in front of the app (1 on Vercel) so rate limits see the real visitor IP.
    TRUST_PROXY_HOPS = int(os.getenv("TRUST_PROXY_HOPS", "0"))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    # Upstash (Vercel Marketplace) provides XPENSE_REDIS_URL; fall back to in-memory locally.
    RATELIMIT_STORAGE_URI = (
        os.getenv("RATELIMIT_STORAGE_URI") or os.getenv("XPENSE_REDIS_URL") or "memory://"
    )
    RATELIMIT_HEADERS_ENABLED = True
    RESET_TOKEN_MAX_AGE = 1800  # 30 minutes
    APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
    APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Kolkata")
    MAIL_MODE = os.getenv("MAIL_MODE", "console")
    MAIL_SERVER = os.getenv("MAIL_SERVER", "")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_FROM = os.getenv("MAIL_FROM", "noreply@example.com")
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
    PRODUCTION = os.getenv("APP_ENV", "development") == "production"
    EXCHANGE_RATE_API_URL = os.getenv(
        "EXCHANGE_RATE_API_URL", "https://api.frankfurter.app/latest"
    )
    EXCHANGE_RATE_API_KEY = os.getenv("EXCHANGE_RATE_API_KEY", "")
    EXCHANGE_RATE_TIMEOUT = float(os.getenv("EXCHANGE_RATE_TIMEOUT", "5"))
