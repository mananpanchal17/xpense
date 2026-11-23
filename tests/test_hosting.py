"""Settings used when the real app runs on Vercel with hosted Postgres."""
from config import database_url, engine_options
from app import create_app


def test_postgres_urls_use_psycopg_driver():
    assert database_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert database_url("postgresql://u:p@h/db?sslmode=require") == "postgresql+psycopg://u:p@h/db?sslmode=require"
    assert database_url("sqlite:///x.db") == "sqlite:///x.db"
    assert "connect_args" not in engine_options("postgresql+psycopg://h/db")
    assert engine_options("sqlite:///x.db") == {"connect_args": {"timeout": 30}}


def test_production_allows_disabled_mail_and_forgot_password_says_so(tmp_path):
    app = create_app({"SECRET_KEY": "s" * 40, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path/'p.db'}",
        "PRODUCTION": True, "SESSION_COOKIE_SECURE": True, "MAIL_MODE": "disabled",
        "RATELIMIT_STORAGE_URI": "redis://localhost:6379", "RATELIMIT_ENABLED": False,
        "APP_BASE_URL": "https://example.vercel.app"})
    from app.extensions import db
    with app.app_context():
        db.create_all()
    response = app.test_client().get("/auth/forgot-password", follow_redirects=True, base_url="https://localhost")
    assert "not set up on this site" in response.text


def test_proxy_hops_use_forwarded_visitor_ip(tmp_path):
    app = create_app({"SECRET_KEY": "s" * 40, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path/'p.db'}",
        "TRUST_PROXY_HOPS": 1, "TESTING": True})

    @app.get("/_ip")
    def _ip():
        from flask import request
        return request.remote_addr

    assert app.test_client().get("/_ip", headers={"X-Forwarded-For": "203.0.113.9"}).text == "203.0.113.9"
