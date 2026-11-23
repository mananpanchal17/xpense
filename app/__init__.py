"""Application factory: creates isolated apps for development and tests."""

from pathlib import Path
import click
from flask import Flask, render_template, request, jsonify
from flask_login import current_user
from flask_wtf.csrf import CSRFError
from config import Config
from .extensions import db, login_manager, csrf, limiter


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    if len(app.config["SECRET_KEY"]) < 32:
        raise RuntimeError("Set SECRET_KEY to a random string of at least 32 characters in .env.")
    if app.config["PRODUCTION"]:
        if not app.config["SESSION_COOKIE_SECURE"] or app.config["MAIL_MODE"] not in ("smtp", "disabled"):
            raise RuntimeError("Production needs COOKIE_SECURE=true and MAIL_MODE=smtp (or disabled).")
        if app.config["RATELIMIT_STORAGE_URI"].startswith("memory:"):
            raise RuntimeError("Production needs a shared rate-limit backend, e.g. Redis.")
        if not app.config["APP_BASE_URL"].startswith("https://"):
            raise RuntimeError("Production APP_BASE_URL must use HTTPS.")
    try:
        Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # read-only filesystem (e.g. Vercel); a hosted DATABASE_URL is used there
    if app.config.get("TRUST_PROXY_HOPS"):
        from werkzeug.middleware.proxy_fix import ProxyFix

        hops = app.config["TRUST_PROXY_HOPS"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.refresh_view = "auth.login"
    login_manager.needs_refresh_message = (
        "Please log in again to change sensitive account settings."
    )
    csrf.init_app(app)
    limiter.init_app(app)

    @app.before_request
    def validate_json_object():
        if request.is_json and not isinstance(request.get_json(), dict):
            return jsonify(error="Send a JSON object with named fields."), 400

    from .models import User

    @login_manager.user_loader
    def load_user(token):
        return db.session.scalar(db.select(User).where(User.session_token == token))

    from .auth.routes import bp as auth_bp
    from .expenses.routes import bp as expenses_bp
    from .reports.routes import bp as reports_bp
    from .insights.routes import bp as insights_bp
    from .accounts.routes import bp as accounts_bp
    from .investments.routes import bp as investments_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(expenses_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(insights_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(investments_bp)
    from .backup import bp as backup_bp
    app.register_blueprint(backup_bp)

    from .forms import BaseForm

    @app.context_processor
    def common_context():
        from .services import today, category_choices, account_choices
        from .models import Notification, Account, Transaction
        from .ledger import unconverted_count

        # This display-only form must not re-read an invalid/oversized POST body.
        unread_notifications = 0
        recent_notifications = []
        if current_user.is_authenticated:
            unread_notifications = db.session.scalar(
                db.select(db.func.count(Notification.id)).where(
                    Notification.user_id == current_user.id, Notification.read_at.is_(None)
                )
            ) or 0
            recent_notifications = db.session.scalars(
                db.select(Notification)
                .where(Notification.user_id == current_user.id)
                .order_by(Notification.created_at.desc())
                .limit(5)
            ).all()
        return {
            "unconverted_count": unconverted_count(current_user.id) if current_user.is_authenticated else 0,
            "reporting_currency": "INR",
            "composer_categories": category_choices(current_user.id) if current_user.is_authenticated else [],
            "composer_accounts": [(a.id, f"{a.name} · {a.currency}") for a in db.session.scalars(db.select(Account).where(Account.user_id == current_user.id, Account.active.is_(True)).order_by(Account.name))] if current_user.is_authenticated else [],
            "refund_totals": dict(db.session.execute(db.select(Transaction.refund_of_id, db.func.sum(Transaction.amount_minor)).where(Transaction.user_id == current_user.id, Transaction.refund_of_id.is_not(None)).group_by(Transaction.refund_of_id)).all()) if current_user.is_authenticated else {},
            "action_form": BaseForm(formdata=None),
            "today": today(),
            "unread_notifications": unread_notifications,
            "recent_notifications": recent_notifications,
        }

    @app.template_filter("money")
    def money(value):
        from decimal import Decimal

        currency = "INR"
        return f"{currency} {Decimal(value or 0) / 100:,.2f}"

    @app.template_filter("money_currency")
    def money_currency(value, currency):
        from decimal import Decimal

        return f"{currency} {Decimal(value or 0) / 100:,.2f}"

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; font-src 'self' https://cdn.jsdelivr.net; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        if current_user.is_authenticated or "/reset/" in __import__("flask").request.path:
            response.headers["Cache-Control"] = "no-store"
        if app.config["SESSION_COOKIE_SECURE"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    def error_page(code, message):
        return render_template("errors/error.html", code=code, message=message), code

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        return error_page(
            400, "The form expired or its security token is missing. Reload the page and try again."
        )

    for code, message in [
        (400, "Invalid request. Check your filters or form data."),
        (404, "We could not find that page or record."),
        (413, "That upload is too large. The maximum is 2 MB."),
        (429, "Too many requests. Please wait before trying again."),
    ]:
        app.register_error_handler(code, lambda error, c=code, m=message: error_page(c, m))

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        return error_page(500, "Something went wrong. Please try again later.")

    @app.cli.command("init-db")
    def init_db():
        """Create missing tables without altering existing tables."""
        db.create_all()
        additive_schema_upgrade()
        click.echo("Database tables created.")

    @app.cli.command("upgrade-db")
    def upgrade_db():
        """Apply additive feature tables; existing tables and rows are preserved."""
        db.create_all()
        additive_schema_upgrade()
        click.echo(
            "Additive schema upgrade complete. Existing tables and records were not replaced."
        )

    def additive_schema_upgrade():
        """Add nullable ledger fields without rebuilding SQLite tables."""
        from sqlalchemy import inspect, text

        columns = {column["name"] for column in inspect(db.engine).get_columns("recurring_expense")}
        if "status" not in columns:
            with db.engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE recurring_expense ADD COLUMN status VARCHAR(12) "
                        "NOT NULL DEFAULT 'active'"
                    )
                )
        transaction_columns = {
            column["name"] for column in inspect(db.engine).get_columns("transaction")
        }
        additions = {
            "account_id": "INTEGER",
            "parent_id": "INTEGER",
            "refund_of_id": "INTEGER",
            "entry_type": "VARCHAR(12) NOT NULL DEFAULT 'normal'",
            "transfer_id": "INTEGER",
            "currency": "VARCHAR(3)",
            "base_amount_minor": "BIGINT",
            "exchange_rate": "NUMERIC(20, 10)",
            "exchange_rate_date": "DATE",
            "exchange_rate_source": "VARCHAR(80)",
        }
        with db.engine.begin() as connection:
            for name, definition in additions.items():
                if name not in transaction_columns:
                    connection.execute(
                        text(f'ALTER TABLE "transaction" ADD COLUMN {name} {definition}')
                    )
            recurring_columns = {
                column["name"] for column in inspect(db.engine).get_columns("recurring_expense")
            }
            if "account_id" not in recurring_columns:
                connection.execute(
                    text("ALTER TABLE recurring_expense ADD COLUMN account_id INTEGER")
                )
            commitment_columns = {
                column["name"] for column in inspect(db.engine).get_columns("commitment")
            }
            if "account_id" not in commitment_columns:
                connection.execute(
                    text("ALTER TABLE commitment ADD COLUMN account_id INTEGER")
                )

    @app.cli.command("generate-recurring")
    def generate_recurring():
        """Generate all due recurring transactions; safe to run again."""
        from .services import process_recurring

        count = process_recurring()
        click.echo(f"Created {count} recurring transaction(s).")

    @app.cli.command("refresh-rates")
    def refresh_rates_command():
        """Refresh cached rates for currencies used by accounts."""
        from .models import Account, User
        from .services import refresh_exchange_rates

        updated = 0
        for user in db.session.scalars(db.select(User)):
            currencies = [
                account.currency
                for account in db.session.scalars(
                    db.select(Account).where(Account.user_id == user.id)
                )
            ]
            try:
                updated += refresh_exchange_rates(user.currency, currencies)
            except (OSError, ValueError):
                db.session.rollback()
                click.echo(f"Skipped rates for user {user.id}: provider unavailable.", err=True)
        click.echo(f"Updated {updated} exchange rate(s).")

    return app
