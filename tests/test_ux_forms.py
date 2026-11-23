from app.extensions import db
from app.models import SavingsGoal, User
from datetime import date


def test_failed_creation_reopens_dialog_with_saved_values(signed_in):
    for route, dialog, payload, value in [
        ('/expenses/bills', 'bill-dialog', {'card_name': 'Study card', 'amount':'0', 'due_date':'2026-11-24'}, 'Study card'),
        ('/goals', 'goal-dialog', {'name':'Study fund', 'target_amount':'0', 'target_date':'2026-11-24'}, 'Study fund'),
        ('/accounts/', 'account-dialog', {'name':'Study bank', 'account_type':'bank', 'currency':'INR', 'last_four':'12345'}, 'Study bank'),
    ]:
        response = signed_in.post(route, data=payload)
        assert response.status_code == 200
        assert f'id="{dialog}" data-has-errors="true"' in response.text
        assert value in response.text
        assert 'aria-invalid="true"' in response.text


def test_failed_contribution_keeps_details_and_creation_action(signed_in, app):
    with app.app_context():
        user = db.session.scalar(db.select(User))
        goal = SavingsGoal(user_id=user.id, name='Study fund', target_minor=100000, saved_minor=500, target_date=date(2027,1,1))
        db.session.add(goal); db.session.commit(); goal_id = goal.id
    response = signed_in.post(f'/goals/{goal_id}/contribute', data={
        'amount':'10.00', 'action':'withdraw', 'contribution_date':'2026-11-20', 'note':'Keep this note',
    })
    assert response.status_code == 200
    assert 'This withdrawal is larger than the amount saved.' in response.text
    assert 'Keep this note' in response.text
    assert 'name="contribution_date" value="2026-11-20"' in response.text
    assert 'class="goal-contribution mb-3" open' in response.text
    assert 'action="/goals"' in response.text
    assert f'for="contribution-date-{goal_id}"' in response.text
