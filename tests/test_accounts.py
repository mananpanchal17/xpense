from decimal import Decimal

from app.extensions import db
from app.models import Account, ExchangeRate, Transaction, Transfer, User
from app.services import refresh_exchange_rates


def account_data(**overrides):
    data = {
        "name": "Main bank",
        "account_type": "bank",
        "provider": "HDFC Bank",
        "last_four": "1234",
        "opening_balance": "1000.00",
        "currency": "INR",
        "color": "teal",
        "icon": "bank",
        "include_in_total": "y",
        "active": "y",
        "notes": "",
    }
    data.update(overrides)
    return data


def account_id(app, name):
    with app.app_context():
        return db.session.scalar(
            db.select(Account.id)
            .join(User)
            .where(User.email == "student@example.com", Account.name == name)
        )


def test_account_crud_and_archive(signed_in, app):
    response = signed_in.post("/accounts/", data=account_data())
    assert response.status_code == 302
    aid = account_id(app, "Main bank")
    assert aid
    response = signed_in.post(f"/accounts/{aid}/archive", data={})
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Account, aid).active is False


def test_same_currency_transfer_is_linked_and_not_normal_income(signed_in, app):
    signed_in.post("/accounts/", data=account_data())
    signed_in.post(
        "/accounts/",
        data=account_data(
            name="Cash wallet",
            account_type="cash",
            provider="",
            last_four="",
            opening_balance="0",
            icon="cash",
        ),
    )
    source = account_id(app, "Main bank")
    destination = account_id(app, "Cash wallet")
    response = signed_in.post(
        "/accounts/transfer",
        data={
            "source_account_id": source,
            "destination_account_id": destination,
            "amount": "125.50",
            "destination_amount": "",
            "fee": "0",
            "transfer_date": "2026-10-30",
            "notes": "ATM cash",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        transfer = db.session.scalar(db.select(Transfer))
        rows = db.session.scalars(db.select(Transaction).where(Transaction.transfer_id == transfer.id)).all()
        assert len(rows) == 2
        assert {row.entry_type for row in rows} == {"transfer"}
        assert db.session.get(Account, source).transactions[0].amount_minor == 12550


def test_cross_user_account_access_is_rejected(signed_in, app, client):
    signed_in.post("/accounts/", data=account_data())
    aid = account_id(app, "Main bank")
    client.post("/auth/logout", data={})
    client.post(
        "/auth/register",
        data={
            "name": "Other",
            "email": "other@example.com",
            "password": "correct-horse-123",
            "confirm": "correct-horse-123",
        },
    )
    client.post("/auth/login", data={"email": "other@example.com", "password": "correct-horse-123"})
    assert client.get(f"/accounts/{aid}/edit").status_code == 404


def test_balance_adjustment_preserves_history(signed_in, app):
    signed_in.post("/accounts/", data=account_data())
    aid = account_id(app, "Main bank")
    response = signed_in.post(
        "/accounts/adjust",
        data={
            "account_id": aid,
            "actual_balance": "1100",
            "adjustment_date": "2026-10-30",
            "reason": "Bank reconciliation",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        row = db.session.scalar(
            db.select(Transaction).where(Transaction.account_id == aid)
        )
        assert row.entry_type == "adjustment"
        assert row.amount_minor == 10000


def test_exchange_rate_refresh_validates_and_caches(app, monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"rates":{"USD":0.012}}'

    monkeypatch.setattr("app.services.urllib.request.urlopen", lambda *args, **kwargs: Response())
    with app.app_context():
        count = refresh_exchange_rates("INR", ["USD"])
        row = db.session.scalar(
            db.select(ExchangeRate).where(
                ExchangeRate.base_currency == "INR",
                ExchangeRate.quote_currency == "USD",
            )
        )
        assert count == 1
        assert Decimal(str(row.rate)) == Decimal("0.012")


def test_exchange_rate_invalid_response_is_rejected(app, monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"unexpected":{}}'

    monkeypatch.setattr("app.services.urllib.request.urlopen", lambda *args, **kwargs: Response())
    with app.app_context():
        try:
            refresh_exchange_rates("INR", ["USD"])
        except ValueError as error:
            assert "invalid response" in str(error)
        else:
            raise AssertionError("Invalid provider response was accepted")


def test_dashboard_renders_with_account(signed_in, app):
    signed_in.post("/accounts/", data=account_data())
    response = signed_in.get("/")
    assert response.status_code == 200
