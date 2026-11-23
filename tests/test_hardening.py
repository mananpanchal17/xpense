from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier, local

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import Account, Commitment, CommitmentOccurrence, Investment, Transaction, User
from conftest import category_id
from test_accounts import account_data, account_id
from test_commitments import commitment_data
from test_investments import investment_data, investment_id


def bank(client, app, **overrides):
    client.post('/accounts/', data=account_data(**overrides))
    return account_id(app, overrides.get('name', 'Main bank'))


def test_zero_investment_value_is_preserved(signed_in, app):
    assert signed_in.post('/investments/', data=investment_data(current_value='0')).status_code == 302
    with app.app_context():
        assert db.session.get(Investment, investment_id(app)).current_value_minor == 0


def test_funding_requires_matching_currency(signed_in, app):
    aid = bank(signed_in, app)
    response = signed_in.post('/investments/', data=investment_data(account_id=str(aid), currency='USD'))
    assert response.status_code == 200
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Investment.id))) == 0
        assert db.session.scalar(db.select(db.func.count(Transaction.id))) == 0


@pytest.mark.parametrize('changes', [dict(amount_invested='10001'), dict(account_id='0'), dict(currency='USD'), dict(purchase_date='2026-10-29')])
def test_funded_investment_history_is_locked(signed_in, app, changes):
    aid = bank(signed_in, app)
    data = investment_data(account_id=str(aid))
    signed_in.post('/investments/', data=data)
    rid = investment_id(app)
    response = signed_in.post(f'/investments/{rid}/edit', data={**data, **changes})
    assert response.status_code == 200
    with app.app_context():
        row = db.session.get(Investment, rid)
        assert (row.account_id, row.currency, row.amount_invested_minor, row.purchase_date) == (aid, 'INR', 1000000, date(2026, 10, 30))


def test_unfunded_investment_cannot_silently_add_funding(signed_in, app):
    aid = bank(signed_in, app)
    signed_in.post('/investments/', data=investment_data())
    rid = investment_id(app)
    assert signed_in.post(f'/investments/{rid}/edit', data=investment_data(account_id=str(aid))).status_code == 200
    with app.app_context():
        assert db.session.get(Investment, rid).account_id is None


def test_funded_investment_can_update_valuation_and_name(signed_in, app):
    aid = bank(signed_in, app)
    data = investment_data(account_id=str(aid))
    signed_in.post('/investments/', data=data)
    rid = investment_id(app)
    assert signed_in.post(f'/investments/{rid}/edit', data={**data, 'current_value':'0', 'name':'Updated name'}).status_code == 302
    with app.app_context():
        row = db.session.get(Investment, rid)
        assert row.name == 'Updated name'
        assert row.current_value_minor == 0
        assert db.session.scalar(db.select(db.func.count(Transaction.id))) == 1


@pytest.mark.parametrize('field,value', [('tenure_months','abc'), ('tenure_months','-1'), ('tenure_months','0'), ('tenure_months','1201'), ('annual_rate','NaN'), ('annual_rate','Infinity'), ('annual_rate','-1'), ('annual_rate','1001')])
def test_invalid_loan_parameters_are_rejected(signed_in, app, field, value):
    response = signed_in.post('/commitments', data=commitment_data(category_id(app), **{field:value}))
    assert response.status_code == 200
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Commitment.id))) == 0


@pytest.mark.parametrize('rate', ['NaN', 'Infinity', '-101', '1001'])
def test_invalid_investment_rates_are_rejected(signed_in, app, rate):
    assert signed_in.post('/investments/', data=investment_data(expected_return_rate=rate)).status_code == 200
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Investment.id))) == 0


def test_zero_loan_rate_and_outstanding_are_preserved(signed_in, app):
    response = signed_in.post('/commitments', data=commitment_data(category_id(app), annual_rate='0', outstanding='0'))
    assert response.status_code == 302
    with app.app_context():
        row = db.session.scalar(db.select(Commitment))
        assert row.annual_rate == 0
        assert row.outstanding_minor == 0


def payment_fixture(client, app, **account_overrides):
    aid = bank(client, app, **account_overrides)
    client.post('/commitments', data=commitment_data(category_id(app), account_id=str(aid)))
    with app.app_context():
        row = db.session.scalar(db.select(Commitment))
        occurrence = CommitmentOccurrence(commitment_id=row.id, due_date=date(2026,11,5), expected_minor=50000)
        db.session.add(occurrence)
        db.session.commit()
        return row.id, occurrence.id


def test_missing_payment_rate_does_not_persist_payment(signed_in, app):
    rid, oid = payment_fixture(signed_in, app, currency='USD')
    response = signed_in.post(f'/commitments/{rid}/occurrence/{oid}/pay', data={'actual_amount':'500','paid_date':'2026-11-05','late_fee':'','notes':'Test'})
    assert response.status_code == 200
    with app.app_context():
        row = db.session.get(CommitmentOccurrence, oid)
        assert row.status == 'scheduled'
        assert row.actual_minor == 0
        assert row.transaction_id is None


def test_concurrent_commitment_payments_post_once(signed_in, app):
    rid, oid = payment_fixture(signed_in, app)
    cookie = signed_in.get_cookie('session').value
    barrier, seen = Barrier(2), local()
    with app.app_context():
        engine = db.engine

    def synchronize_reads(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT') and 'FROM commitment_occurrence' in statement and not getattr(seen, 'read', False):
            seen.read = True
            barrier.wait(timeout=10)

    def pay(_):
        with app.test_client() as client:
            client.set_cookie('session', cookie)
            return client.post(f'/commitments/{rid}/occurrence/{oid}/pay', data={'actual_amount':'500','paid_date':'2026-11-05','late_fee':'','notes':''}).status_code

    event.listen(engine, 'after_cursor_execute', synchronize_reads)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            assert list(workers.map(pay, range(2))) == [302,302]
    finally:
        event.remove(engine, 'after_cursor_execute', synchronize_reads)
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Transaction.id))) == 1
