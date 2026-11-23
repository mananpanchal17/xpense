from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models import Commitment, CommitmentOccurrence
from app.services import (
    amortization_schedule,
    calculate_emi,
    commitment_summary,
    prepayment_simulation,
)
from conftest import category_id, register, login


def commitment_data(cid, **overrides):
    data = {
        "name": "Vehicle EMI",
        "commitment_type": "loan",
        "amount": "500",
        "start_date": "2026-10-30",
        "next_due": "2026-11-05",
        "frequency": "monthly",
        "category_id": str(cid),
        "payment_method": "Bank transfer",
        "reminder_days": "7",
        "original_principal": "10000",
        "outstanding": "10000",
        "annual_rate": "12",
        "tenure_months": "12",
        "processing_fee": "0",
        "lender": "Example lender",
    }
    data.update(overrides)
    return data


def test_emi_and_zero_interest_boundaries():
    assert calculate_emi(120000, Decimal("0"), 12) == 10000
    schedule = amortization_schedule(120000, Decimal("12"), 12)
    assert schedule[-1]["balance_minor"] == 0
    assert sum(item["principal_minor"] for item in schedule) == 120000
    simulation = prepayment_simulation(120000, Decimal("12"), 12, calculate_emi(120000, 12, 12), 20000)
    assert simulation["reduce_tenure"]["months_saved"] > 0
    assert simulation["reduce_emi"]["emi_minor"] < calculate_emi(120000, 12, 12)


def test_commitment_crud_occurrence_and_summary(signed_in, app):
    cid = category_id(app)
    response = signed_in.post("/commitments", data=commitment_data(cid))
    assert response.status_code == 302
    with app.app_context():
        commitment = db.session.scalar(db.select(Commitment))
        commitment_id = commitment.id
        summary = commitment_summary(commitment.user_id, date(2026, 11, 1))
        assert summary["remaining"] == 50000
        # Reading no longer stores rows; skipping by due date stores one.
        assert db.session.scalar(db.select(CommitmentOccurrence)) is None
        due = commitment.next_due
    assert signed_in.post(
        f"/commitments/{commitment_id}/due/{due.isoformat()}/skip"
    ).status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(CommitmentOccurrence)).status == "skipped"
    assert signed_in.get(f"/commitments/{commitment_id}/simulator").status_code == 200
    assert signed_in.post(f"/commitments/{commitment_id}/delete").status_code == 302


def test_commitment_cross_user_authorization(signed_in, app):
    cid = category_id(app)
    signed_in.post("/commitments", data=commitment_data(cid, commitment_type="subscription"))
    with app.app_context():
        record_id = db.session.scalar(db.select(Commitment.id))
    attacker = app.test_client()
    register(attacker, "commitment-attacker@example.com", "Attacker")
    login(attacker, "commitment-attacker@example.com")
    assert attacker.get(f"/commitments/{record_id}/edit").status_code == 404
    assert attacker.post(f"/commitments/{record_id}/delete").status_code == 404
