from app.extensions import db
from app.models import Account, Investment, Transaction, User


def investment_data(**overrides):
    data = {
        "name": "Emergency FD",
        "investment_type": "fixed_deposit",
        "institution": "SBI",
        "account_id": "0",
        "currency": "INR",
        "purchase_date": "2026-10-30",
        "amount_invested": "10000",
        "current_value": "10200",
        "maturity_date": "2027-10-30",
        "expected_return_rate": "7.1",
        "interest_earned": "200",
        "dividend_income": "0",
        "reference": "FD-1234",
        "status": "active",
        "notes": "Personal estimate",
    }
    data.update(overrides)
    return data


def investment_id(app):
    with app.app_context():
        return db.session.scalar(
            db.select(Investment.id)
            .join(User)
            .where(User.email == "student@example.com")
        )


def test_investment_crud_and_dashboard(signed_in, app):
    response = signed_in.post("/investments/", data=investment_data())
    assert response.status_code == 302
    assert investment_id(app)
    assert signed_in.get("/investments/").status_code == 200
    assert signed_in.get("/").status_code == 200


def test_investment_funding_account_creates_ledger_entry(signed_in, app):
    signed_in.post(
        "/accounts/",
        data={
            "name": "Bank",
            "account_type": "bank",
            "provider": "SBI",
            "last_four": "1234",
            "opening_balance": "50000",
            "currency": "INR",
            "color": "teal",
            "icon": "bank",
            "include_in_total": "y",
            "active": "y",
            "notes": "",
        },
    )
    with app.app_context():
        account_id = db.session.scalar(db.select(Account.id))
    response = signed_in.post(
        "/investments/",
        data=investment_data(account_id=str(account_id), amount_invested="1000", current_value="1000"),
    )
    assert response.status_code == 302
    with app.app_context():
        row = db.session.scalar(
            db.select(Transaction).where(Transaction.entry_type == "investment")
        )
        assert row is not None
        assert row.amount_minor == 100000


def test_investment_ownership(signed_in, app, client):
    signed_in.post("/investments/", data=investment_data())
    record_id = investment_id(app)
    signed_in.post("/auth/logout", data={})
    client.post(
        "/auth/register",
        data={
            "name": "Other",
            "email": "other-investor@example.com",
            "password": "correct-horse-123",
            "confirm": "correct-horse-123",
        },
    )
    client.post(
        "/auth/login",
        data={"email": "other-investor@example.com", "password": "correct-horse-123"},
    )
    assert client.get(f"/investments/{record_id}/edit").status_code == 404
