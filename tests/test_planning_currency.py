"""Planning totals report INR while occurrences retain their native amounts."""
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import Account, Commitment, CommitmentOccurrence, ExchangeRate, User
from app.services import commitment_summary


def planned_usd(app, rate_pair=None, include_inr=False):
    with app.app_context():
        user = db.session.scalar(db.select(User))
        account = Account(user_id=user.id, name="USD cash", account_type="cash", currency="USD")
        db.session.add(account)
        db.session.flush()
        commitment = Commitment(user_id=user.id, account_id=account.id,
            name="USD plan", commitment_type="custom", amount_minor=10000,
            start_date=date(2026, 10, 1), next_due=date(2026, 10, 15), frequency="monthly")
        db.session.add(commitment)
        db.session.flush()
        for day, status, actual in [(5, "overdue", 0), (15, "paid", 2500), (30, "scheduled", 0)]:
            db.session.add(CommitmentOccurrence(commitment_id=commitment.id,
                due_date=date(2026, 10, day), expected_minor=10000, actual_minor=actual, status=status))
        if rate_pair:
            base, quote, rate = rate_pair
            db.session.add(ExchangeRate(base_currency=base, quote_currency=quote,
                rate=Decimal(rate), provider="test fixture"))
        if include_inr:
            db.session.add(Commitment(user_id=user.id, name="INR plan", commitment_type="custom",
                amount_minor=10000, start_date=date(2026, 10, 1), next_due=date(2026, 10, 30), frequency="monthly"))
        db.session.commit()
        return user.id


@pytest.mark.parametrize("pair,expected", [
    (("USD", "INR", "83"), {"due_month": 2490000, "paid_month": 207500,
        "remaining": 830000, "overdue": 830000, "monthly_fixed": 830000, "annual_recurring": 9960000}),
    (("INR", "USD", "0.0125"), {"due_month": 2400000, "paid_month": 200000,
        "remaining": 800000, "overdue": 800000, "monthly_fixed": 800000, "annual_recurring": 9600000}),
])
def test_planning_aggregates_convert_cached_direct_or_inverse_rate(signed_in, app, pair, expected):
    uid = planned_usd(app, pair)
    with app.app_context():
        summary = commitment_summary(uid, date(2026, 10, 30))
        for key, amount in expected.items():
            assert summary[key] == amount
        assert summary["unconverted_count"] == 0
        assert summary["currency"] == "INR"
        assert [row.expected_minor for row in summary["occurrences"]] == [10000, 10000, 10000]
        assert summary["next"].expected_minor == 10000


def test_planning_combines_inr_and_converted_foreign_amounts(signed_in, app):
    uid = planned_usd(app, ("USD", "INR", "83"), include_inr=True)
    with app.app_context():
        summary = commitment_summary(uid, date(2026, 10, 30))
        assert summary["due_month"] == 2500000
        assert summary["remaining"] == 840000
        assert summary["monthly_fixed"] == 840000
        assert summary["annual_recurring"] == 10080000
        assert summary["unconverted_count"] == 0


def test_missing_planning_rate_excludes_one_commitment_and_warns(signed_in, app):
    uid = planned_usd(app, include_inr=True)
    with app.app_context():
        summary = commitment_summary(uid, date(2026, 10, 30))
        assert summary["due_month"] == 10000
        assert summary["paid_month"] == 0
        assert summary["remaining"] == 10000
        assert summary["overdue"] == 0
        assert summary["monthly_fixed"] == 10000
        assert summary["annual_recurring"] == 120000
        assert summary["unconverted_count"] == 1
        assert len(summary["occurrences"]) == 4
    for path in ["/commitments", "/"]:
        response = signed_in.get(path)
        assert response.status_code == 200
        assert "1 commitments excluded from INR totals: no cached conversion rate." in response.text
