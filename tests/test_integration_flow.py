"""Exercise the real composer contract through CSRF, validation, ledger and UI.

These checks catch decimal scaling/rounding, writes on invalid input, tenant
leaks, and missing refund/split presentation without mocking the boundaries.
"""

from html.parser import HTMLParser
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import Category, ExchangeRate, Transaction, User
from conftest import category_id, transaction_data
from test_accounts import account_data, account_id
from test_commitments import commitment_data


class LedgerPage(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = {}
        self.rows = {}
        self.refunds = {}
        self._form = None
        self._row = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self._form = attrs.get("id")
            if self._form:
                self.forms[self._form] = dict(attrs)
        if tag == "input" and self._form and attrs.get("name") == "csrf_token":
            self.forms[self._form]["csrf_token"] = attrs["value"]
        if tag == "tr" and "data-transaction-id" in attrs:
            self._row = int(attrs["data-transaction-id"])
            self.rows[self._row] = ""
        if tag == "button" and "data-refund" in attrs:
            self.refunds[int(attrs["data-refund"])] = attrs

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        if tag == "tr":
            self._row = None

    def handle_data(self, text):
        if self._row is not None:
            self.rows[self._row] += text


@pytest.fixture
def csrf_client(signed_in, app):
    # Authentication setup uses the shared fixture; every tested write uses CSRF.
    app.config["WTF_CSRF_ENABLED"] = True
    return signed_in


def composer(client):
    response = client.get("/expenses/")
    assert response.status_code == 200
    form = LedgerPage(response.text).forms["compose-form"]
    assert form["csrf_token"]
    return form


def submit(client, form, cid, amount, **overrides):
    return client.post(
        form["action"],
        data=transaction_data(cid, amount=amount, csrf_token=form["csrf_token"], account_id="0", **overrides),
        headers={"Accept": "application/json"},
    )


def test_decimal_composer_reaches_integer_ledger_formatted_ui_and_charts(csrf_client, app):
    cid = category_id(app)
    form = composer(csrf_client)
    first = submit(csrf_client, form, cid, "0.10")
    second = submit(csrf_client, form, cid, "0.20")
    assert first.status_code == second.status_code == 201
    ids = [first.json["transaction"]["id"], second.json["transaction"]["id"]]
    assert first.json["transaction"]["amount_minor"] == 10
    assert second.json["transaction"]["amount_minor"] == 20
    with app.app_context():
        rows = db.session.scalars(db.select(Transaction).order_by(Transaction.id)).all()
        assert [row.amount_minor for row in rows] == [10, 20]
        assert [row.base_amount_minor for row in rows] == [10, 20]
        assert db.session.scalar(db.select(db.func.sum(Transaction.amount_minor))) == 30
    page = LedgerPage(csrf_client.get("/expenses/").text)
    assert "INR 0.10" in page.rows[ids[0]]
    assert "INR 0.20" in page.rows[ids[1]]
    dashboard = csrf_client.get("/")
    assert dashboard.status_code == 200
    assert "INR 0.30" in dashboard.text
    chart = csrf_client.get("/reports/chart-data").json
    assert chart["currency"] == "INR"
    assert chart["expense"][-1] == 0.3
    assert chart["category_labels"] == ["Food"]
    assert chart["category_values"] == [0.3]


@pytest.mark.parametrize("amount", ["abc", "NaN", "Infinity", "-Infinity", "0.001", "0", "-0.01"])
def test_composer_rejects_invalid_money_without_writes(csrf_client, app, amount):
    response = submit(csrf_client, composer(csrf_client), category_id(app), amount)
    assert response.status_code == 422
    assert "amount" in response.json["errors"]
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Transaction.id))) == 0


def test_composer_rejects_foreign_category_without_writes(csrf_client, app):
    with app.app_context():
        other = User(name="Other", email="other@example.com", password_hash="unused")
        db.session.add(other)
        db.session.flush()
        category = Category(user_id=other.id, name="Private category")
        db.session.add(category)
        db.session.commit()
        foreign_id = category.id
    form = composer(csrf_client)
    response = submit(csrf_client, form, foreign_id, "0.01")
    assert response.status_code == 422
    assert "category_id" in response.json["errors"]
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Transaction.id))) == 0


def test_composer_requires_csrf_before_writing(csrf_client, app):
    form = composer(csrf_client)
    form["csrf_token"] = "invalid-token"
    assert submit(csrf_client, form, category_id(app), "0.01").status_code == 400
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Transaction.id))) == 0


def test_split_and_refund_keep_cent_values_and_remaining_controls(csrf_client, app):
    cid = category_id(app)
    form = composer(csrf_client)
    original = submit(csrf_client, form, cid, "0.03")
    assert original.status_code == 201
    parent_id = original.json["transaction"]["id"]
    headers = {"X-CSRFToken": form["csrf_token"]}
    split = csrf_client.post(f"/expenses/{parent_id}/split", json={"parts": [
        {"category_id": cid, "amount": "0.01"},
        {"category_id": cid, "amount": "0.02"},
    ]}, headers=headers)
    assert split.status_code == 201
    penny_id, two_cent_id = split.json["ids"]
    page = LedgerPage(csrf_client.get("/expenses/").text)
    assert parent_id not in page.rows
    assert "INR 0.01" in page.rows[penny_id]
    assert "Linked entry" in page.rows[penny_id]
    assert "INR 0.02" in page.rows[two_cent_id]
    assert page.refunds[two_cent_id]["data-amount"] == "0.02"
    assert "data-refund-only" in page.refunds[two_cent_id]
    refund = csrf_client.post(f"/expenses/{two_cent_id}/refund", json={
        "amount": "0.01", "date": "2026-10-30", "notes": "One cent returned",
    }, headers=headers)
    assert refund.status_code == 201
    refund_id = refund.json["id"]
    page = LedgerPage(csrf_client.get("/expenses/").text)
    assert "INR 0.01" in page.rows[refund_id]
    assert "Refund" in page.rows[refund_id]
    assert "Linked entry" in page.rows[refund_id]
    assert refund_id not in page.refunds
    assert page.refunds[two_cent_id]["data-amount"] == "0.01"
    assert page.refunds[two_cent_id]["data-currency"] == "INR"
    assert "data-refund-only" in page.refunds[two_cent_id]
    with app.app_context():
        assert db.session.get(Transaction, parent_id).entry_type == "split_parent"
        assert db.session.get(Transaction, penny_id).amount_minor == 1
        assert db.session.get(Transaction, two_cent_id).amount_minor == 2
        saved_refund = db.session.get(Transaction, refund_id)
        assert saved_refund.amount_minor == 1
        assert saved_refund.refund_of_id == two_cent_id
    chart = csrf_client.get("/reports/chart-data").json
    assert chart["expense"][-1] == 0.02
    assert chart["category_values"] == [0.02]
    final = csrf_client.post(f"/expenses/{two_cent_id}/refund", json={
        "amount": "0.01", "date": "2026-10-30",
    }, headers=headers)
    assert final.status_code == 201
    assert two_cent_id not in LedgerPage(csrf_client.get("/expenses/").text).refunds


@pytest.mark.parametrize("path,amounts", [
    ("/expenses/recurring", ["USD 100.00"]),
    ("/calendar?month=2026-10", ["USD 100.00", "USD 200.00"]),
    ("/commitments", ["USD 200.00"]),
])
def test_native_planned_amounts_keep_account_currency(csrf_client, app, path, amounts):
    token = composer(csrf_client)["csrf_token"]
    assert csrf_client.post("/accounts/", data=account_data(
        currency="USD", csrf_token=token,
    )).status_code == 302
    aid = account_id(app, "Main bank")
    cid = category_id(app)
    with app.app_context():
        db.session.add(ExchangeRate(base_currency="USD", quote_currency="INR",
            rate=Decimal("83"), provider="test fixture"))
        db.session.commit()
    assert csrf_client.post("/expenses/recurring", data={
        "category_id": cid, "account_id": aid, "amount": "100.00",
        "next_due": "2026-10-30", "frequency": "monthly",
        "payment_method": "UPI", "active": "y", "csrf_token": token,
    }).status_code == 302
    assert csrf_client.post("/expenses/recurring/generate", data={
        "csrf_token": token,
    }).status_code == 302
    assert csrf_client.post("/commitments", data=commitment_data(
        cid, account_id=aid, amount="200.00", next_due="2026-10-30",
        csrf_token=token,
    )).status_code == 302
    response = csrf_client.get(path)
    assert response.status_code == 200
    for amount in amounts:
        assert amount in response.text
