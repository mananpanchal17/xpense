"""The Obligations module: one read-only answer to "what is due between two dates"."""
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import (Account, Category, Commitment, CommitmentOccurrence, CreditCardBill,
    ExchangeRate, RecurringExpense, RecurringOccurrence, User)
from app.obligations import annual_cost, claim_occurrence, due_items, step

REF = date(2026, 10, 30)


@pytest.fixture
def uid(signed_in, app):
    with app.app_context():
        return db.session.scalar(db.select(User.id))


def add(app, *rows):
    with app.app_context():
        db.session.add_all(rows)
        db.session.commit()
        return [row.id for row in rows]


def loan(uid, **kw):
    values = dict(user_id=uid, name='Loan', commitment_type='loan', amount_minor=10000,
        start_date=date(2026, 1, 1), next_due=date(2026, 11, 5), frequency='monthly', reminder_days=3)
    values.update(kw)
    return Commitment(**values)


def rule(app, uid, **kw):
    with app.app_context():
        cid = db.session.scalar(db.select(Category.id).where(Category.user_id == uid))
    values = dict(user_id=uid, category_id=cid, amount_minor=500, payment_method='Cash',
        frequency='weekly', next_due=date(2026, 11, 2), anchor_day=2, active=True, status='active')
    values.update(kw)
    return RecurringExpense(**values)


@pytest.mark.parametrize('frequency,due,anchor,expected', [
    ('monthly', date(2026, 1, 31), 31, date(2026, 2, 28)),
    ('monthly', date(2026, 2, 28), 31, date(2026, 3, 31)),
    ('quarterly', date(2026, 1, 31), 31, date(2026, 4, 30)),
    ('yearly', date(2024, 2, 29), 29, date(2025, 2, 28)),
    ('yearly', date(2027, 2, 28), 29, date(2028, 2, 29)),
    ('weekly', date(2026, 12, 29), 29, date(2027, 1, 5)),
    ('once', date(2026, 1, 1), 1, None),
])
def test_one_stepper_keeps_month_end_anchor(frequency, due, anchor, expected):
    assert step(frequency, due, anchor) == expected


def test_reading_never_writes(app, uid):
    add(app, loan(uid, next_due=date(2026, 8, 5)), rule(app, uid))
    with app.app_context():
        items = due_items(uid, date(2026, 8, 1), date(2027, 1, 1), reference=REF)
        assert items
        assert not db.session.new and not db.session.dirty
        assert db.session.scalar(db.select(db.func.count(CommitmentOccurrence.id))) == 0


def test_all_three_sources_in_date_order(app, uid):
    add(app, loan(uid), rule(app, uid, frequency='monthly', next_due=date(2026, 11, 3), anchor_day=3),
        CreditCardBill(user_id=uid, card_name='Card', amount_minor=700, due_date=date(2026, 11, 1)))
    with app.app_context():
        items = due_items(uid, date(2026, 11, 1), date(2026, 12, 1), reference=REF)
        assert [(i.kind, i.due_date) for i in items] == [
            ('bill', date(2026, 11, 1)), ('recurring', date(2026, 11, 3)), ('commitment', date(2026, 11, 5))]
        assert all(i.status == 'upcoming' for i in items)


def test_weekly_recurring_expands_every_week(app, uid):
    add(app, rule(app, uid))
    with app.app_context():
        dates = [i.due_date for i in due_items(uid, date(2026, 11, 1), date(2026, 12, 1), reference=REF)]
        assert dates == [date(2026, 11, d) for d in (2, 9, 16, 23, 30)]


def test_stored_rows_win_and_carry_status_and_remaining(app, uid):
    lid, = add(app, loan(uid, next_due=date(2026, 9, 5)))
    add(app, CommitmentOccurrence(commitment_id=lid, due_date=date(2026, 9, 5), expected_minor=10000,
        actual_minor=4000, status='partial'),
        CommitmentOccurrence(commitment_id=lid, due_date=date(2026, 10, 5), expected_minor=10000,
        actual_minor=10000, status='paid'))
    with app.app_context():
        items = {i.due_date: i for i in due_items(uid, date(2026, 9, 1), date(2026, 12, 1), reference=REF)}
        assert (items[date(2026, 9, 5)].status, items[date(2026, 9, 5)].remaining_minor) == ('partial', 6000)
        assert items[date(2026, 9, 5)].id is not None
        assert (items[date(2026, 10, 5)].status, items[date(2026, 10, 5)].remaining_minor) == ('paid', 0)
        assert (items[date(2026, 11, 5)].status, items[date(2026, 11, 5)].id) == ('upcoming', None)
        assert items[date(2026, 9, 5)].is_overdue and not items[date(2026, 10, 5)].outstanding


def test_arrears_mode_reaches_back_to_next_due(app, uid):
    add(app, loan(uid, next_due=date(2026, 7, 31)),
        CreditCardBill(user_id=uid, card_name='Old', amount_minor=100, due_date=date(2025, 1, 1)))
    with app.app_context():
        items = due_items(uid, None, REF, reference=REF)
        assert [i.due_date for i in items] == [date(2025, 1, 1), date(2026, 7, 31), date(2026, 8, 31),
            date(2026, 9, 30)]
        assert all(i.status == 'overdue' for i in items)


def test_inactive_sources_are_left_out(app, uid):
    add(app, loan(uid, status='paused'), loan(uid, status='cancelled'),
        loan(uid, name='Paid off', outstanding_minor=0),
        rule(app, uid, status='paused'), rule(app, uid, active=False))
    with app.app_context():
        assert due_items(uid, date(2026, 11, 1), date(2027, 1, 1), reference=REF) == []


def test_range_is_capped_at_400_days_ahead(app, uid):
    add(app, rule(app, uid))
    with app.app_context():
        items = due_items(uid, REF, date(2040, 1, 1), reference=REF)
        assert items[-1].due_date < date(2027, 12, 5)
        assert len(items) <= 58


def test_foreign_amounts_convert_to_inr_or_flag_unconverted(app, uid):
    with app.app_context():
        usd = Account(user_id=uid, name='USD', account_type='bank', currency='USD')
        eur = Account(user_id=uid, name='EUR', account_type='bank', currency='EUR')
        db.session.add_all([usd, eur, ExchangeRate(base_currency='USD', quote_currency='INR',
            rate=Decimal('83.125'), provider='fixture')])
        db.session.flush()
        db.session.add_all([loan(uid, name='USD', account_id=usd.id), loan(uid, name='EUR', account_id=eur.id)])
        db.session.commit()
        items = {i.name: i for i in due_items(uid, date(2026, 11, 1), date(2026, 12, 1), reference=REF)}
        assert (items['USD'].currency, items['USD'].inr_minor) == ('USD', 831250)
        assert (items['EUR'].currency, items['EUR'].inr_minor) == ('EUR', None)


def test_annual_cost_uses_52_weeks_and_inr(app, uid):
    add(app, rule(app, uid, amount_minor=100), loan(uid, amount_minor=1000, frequency='quarterly'),
        loan(uid, amount_minor=999, frequency='once'), rule(app, uid, amount_minor=7, status='paused'))
    with app.app_context():
        cost = annual_cost(uid)
        assert cost['annual'] == 100 * 52 + 1000 * 4
        assert cost['monthly'] == round((100 * 52 + 1000 * 4) / 12)
        assert cost['unconverted_count'] == 0


def test_claim_creates_one_row_only_for_a_real_due_date(app, uid):
    lid, = add(app, loan(uid))
    with app.app_context():
        commitment = db.session.get(Commitment, lid)
        first = claim_occurrence(commitment, date(2026, 12, 5))
        again = claim_occurrence(commitment, date(2026, 12, 5))
        db.session.commit()
        assert first.id == again.id and first.expected_minor == 10000
        assert claim_occurrence(commitment, date(2026, 12, 6)) is None
        assert db.session.scalar(db.select(db.func.count(CommitmentOccurrence.id))) == 1


def test_pay_by_date_link_records_payment(signed_in, app, uid):
    with app.app_context():
        account = Account(user_id=uid, name='Bank', account_type='bank', currency='INR')
        db.session.add(account); db.session.flush()
        row = loan(uid, account_id=account.id)
        db.session.add(row); db.session.commit()
        lid = row.id
    url = f'/commitments/{lid}/due/2026-11-05/pay'
    page = signed_in.get(url)
    assert page.status_code == 200 and 'value="100.00"' in page.text
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(CommitmentOccurrence.id))) == 0
    response = signed_in.post(url, data={'actual_amount': '100', 'paid_date': '2026-10-30', 'payment_token': 't'})
    assert response.status_code == 302
    with app.app_context():
        stored = db.session.scalar(db.select(CommitmentOccurrence))
        assert (stored.due_date, stored.status, stored.actual_minor) == (date(2026, 11, 5), 'paid', 10000)
    assert signed_in.get(f'/commitments/{lid}/due/2026-11-06/pay').status_code == 404
    assert signed_in.post(f'/commitments/{lid}/due/2026-12-05/skip').status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(CommitmentOccurrence.status).where(
            CommitmentOccurrence.due_date == date(2026, 12, 5))) == 'skipped'


def test_recurring_occurrence_rows_and_rule_dates_merge(app, uid):
    with app.app_context():
        row = rule(app, uid, frequency='monthly', next_due=date(2026, 11, 2), anchor_day=2)
        db.session.add(row); db.session.flush()
        db.session.add(RecurringOccurrence(recurring_id=row.id, due_date=date(2026, 10, 2), status='paid'))
        db.session.commit()
        items = due_items(uid, date(2026, 10, 1), date(2026, 12, 1), reference=REF)
        assert [(i.due_date, i.status) for i in items] == [(date(2026, 10, 2), 'paid'), (date(2026, 11, 2), 'upcoming')]


def test_forecast_counts_unpaid_obligations_left_this_month(app, uid):
    from app.services import financial_forecast
    add(app, CreditCardBill(user_id=uid, card_name='Card', amount_minor=700, due_date=date(2026, 10, 31)),
        CreditCardBill(user_id=uid, card_name='Paid', amount_minor=900, due_date=date(2026, 10, 31), paid=True),
        loan(uid, next_due=date(2026, 10, 10)))
    with app.app_context():
        assert financial_forecast(uid, REF)['recurring_total'] == 700 + 10000
