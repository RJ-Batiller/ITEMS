"""Application configuration loaded from environment variables."""

import os

from dotenv import load_dotenv


load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    DEBUG = os.getenv("APP_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}
    APP_HTTPS = os.getenv("APP_HTTPS", "false").strip().lower() in {"1", "true", "yes", "on"}
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # HTTPS must never send an authentication cookie over plain HTTP.
    SESSION_COOKIE_SECURE = (
        os.getenv("SESSION_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}
        or APP_HTTPS
    )
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(4 * 1024 * 1024)))

    DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT = int(os.getenv("DB_PORT", "3306"))
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_NAME = os.getenv("DB_NAME", "items_db")

    APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
    APP_PORT = int(os.getenv("APP_PORT", "5000"))
    APP_PUBLIC_URL = os.getenv("APP_PUBLIC_URL", "").rstrip("/")


def require_secret_key():
    if not Config.SECRET_KEY:
        raise RuntimeError("SECRET_KEY must be set in the environment or .env file.")
    return Config.SECRET_KEY
