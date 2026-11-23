from datetime import date

from app.extensions import db
from app.models import (
    CreditCardBill,
    Notification,
    SavingsGoal,
    Transaction,
    User,
)
from app.services import (
    financial_forecast,
    financial_health,
    financial_insights,
    generate_notifications,
)
from conftest import category_id, login, register, transaction_data


def test_empty_intelligence_is_explainable(signed_in, app):
    with app.app_context():
        user_id = db.session.scalar(db.select(User.id))
        assert financial_insights(user_id) == []
        assert financial_forecast(user_id)["expected_expense"] == 0
        health = financial_health(user_id)
        assert 0 <= health["score"] <= 100
    assert signed_in.get("/insights").status_code == 200
    assert signed_in.get("/goals").status_code == 200
    assert signed_in.get("/notifications").status_code == 200
    assert signed_in.get("/calendar").status_code == 200
    assert signed_in.get("/expenses/bills").status_code == 200


def test_insights_forecast_and_health_use_real_transactions(signed_in, app):
    cid = category_id(app)
    signed_in.post(
        "/expenses/add",
        data=transaction_data(cid, amount="100", date="2026-10-10"),
    )
    signed_in.post(
        "/expenses/add",
        data=transaction_data(cid, amount="500", kind="income", date="2026-10-11"),
    )
    with app.app_context():
        user_id = db.session.scalar(db.select(Transaction.user_id))
        insights = financial_insights(user_id, date(2026, 10, 30))
        forecast = financial_forecast(user_id, date(2026, 10, 30))
        health = financial_health(user_id, date(2026, 10, 30))
        assert insights
        assert forecast["expected_expense"] >= 10000
        assert forecast["method"]
        assert health["status"] in {"Excellent", "Good", "Needs Attention", "Critical"}


def test_goal_crud_contribution_and_withdrawal(signed_in, app):
    response = signed_in.post(
        "/goals",
        data={
            "name": "Emergency fund",
            "target_amount": "1000",
            "target_date": "2027-10-30",
            "description": "A buffer",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        goal = db.session.scalar(db.select(SavingsGoal))
        goal_id = goal.id
    assert signed_in.post(
        f"/goals/{goal_id}/contribute",
        data={
            "amount": "250",
            "action": "add",
            "contribution_date": "2026-10-30",
            "note": "First deposit",
        },
    ).status_code == 302
    assert signed_in.post(
        f"/goals/{goal_id}/contribute",
        data={
            "amount": "50",
            "action": "withdraw",
            "contribution_date": "2026-10-30",
        },
    ).status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(SavingsGoal.saved_minor)) == 20000
    assert signed_in.post(f"/goals/{goal_id}/delete").status_code == 302


def test_notifications_are_deduplicated_and_owner_scoped(signed_in, app):
    with app.app_context():
        user_id = db.session.scalar(db.select(User.id))
        bill = CreditCardBill(
            user_id=user_id,
            card_name="Card",
            amount_minor=10000,
            due_date=date(2026, 10, 30),
        )
        db.session.add(bill)
        db.session.commit()
        assert generate_notifications(user_id, date(2026, 10, 30)) == 1
        assert generate_notifications(user_id, date(2026, 10, 30)) == 0
        assert db.session.scalar(db.select(db.func.count(Notification.id))) == 1
        notification_id = db.session.scalar(db.select(Notification.id))
    attacker = app.test_client()
    register(attacker, "another@example.com", "Other")
    login(attacker, "another@example.com")
    assert attacker.post(f"/notifications/{notification_id}/read").status_code == 404


def test_goal_and_notification_routes_require_owner(signed_in, app):
    with app.app_context():
        user_id = db.session.scalar(db.select(User.id))
        goal = SavingsGoal(
            user_id=user_id,
            name="Goal",
            target_minor=10000,
            target_date=date(2027, 1, 1),
        )
        db.session.add(goal)
        db.session.commit()
        goal_id = goal.id
    attacker = app.test_client()
    register(attacker, "third@example.com", "Third")
    login(attacker, "third@example.com")
    assert attacker.get(f"/goals/{goal_id}/edit").status_code == 404
    assert attacker.post(f"/goals/{goal_id}/delete").status_code == 404
