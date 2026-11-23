from datetime import date
from decimal import Decimal

from app.extensions import db
from app.ledger import period_totals
from app.models import Account, ExchangeRate, Investment, Transaction, User
from app.services import account_balance, investment_summary
from test_accounts import account_data, account_id
from test_investments import investment_data, investment_id


def test_transfer_fee_is_spending_and_keeps_currency_snapshot(signed_in, app):
    signed_in.post('/accounts/', data=account_data(currency='USD'))
    signed_in.post('/accounts/', data=account_data(name='Other', currency='USD', opening_balance='0'))
    source, destination = account_id(app, 'Main bank'), account_id(app, 'Other')
    with app.app_context():
        db.session.add(ExchangeRate(base_currency='USD', quote_currency='INR', rate=Decimal('80'), provider='Test'))
        db.session.commit()
    response = signed_in.post('/accounts/transfer', data=dict(source_account_id=source,
        destination_account_id=destination, amount='100', fee='2', transfer_date='2026-10-30'))
    assert response.status_code == 302
    with app.app_context():
        uid = db.session.get(Account, source).user_id
        assert account_balance(db.session.get(Account, source)) == 89800
        assert account_balance(db.session.get(Account, destination)) == 10000
        assert period_totals(uid)['expense'] == 16000
        fee = db.session.scalar(db.select(Transaction).where(Transaction.entry_type == 'normal'))
        assert fee.amount_minor == 200 and fee.base_amount_minor == 16000
        assert fee.exchange_rate == 80 and fee.exchange_rate_source == 'Test'


def test_zero_balance_adjustment_and_form_constraints(signed_in, app):
    signed_in.post('/accounts/', data=account_data())
    aid = account_id(app, 'Main bank')
    response = signed_in.post('/accounts/adjust', data=dict(account_id=aid,
        actual_balance='0', adjustment_date='2026-10-30', reason='Reconciled'))
    assert response.status_code == 302
    with app.app_context():
        assert account_balance(db.session.get(Account, aid)) == 0
    import re
    for url, name in [('/accounts/adjust', 'actual_balance'), ('/accounts/transfer', 'fee'), ('/accounts/', 'opening_balance')]:
        html = signed_in.get(url).get_data(as_text=True)
        field = re.search(r'<input[^>]*name="' + name + r'"[^>]*>', html).group()
        assert 'min="0"' in field


def test_report_labels_describe_selected_period(signed_in):
    html = signed_in.get('/reports/monthly?year=2026&month=9').get_data(as_text=True)
    assert 'Lifetime income' not in html and 'September 2026 income' in html


def test_redemption_credits_once_and_excludes_closed_holdings(signed_in, app):
    signed_in.post('/accounts/', data=account_data(opening_balance='50000'))
    aid = account_id(app, 'Main bank')
    signed_in.post('/investments/', data=investment_data(account_id=str(aid)))
    iid = investment_id(app)
    payload = dict(amount='10500', redemption_date='2026-11-02', account_id=aid, notes='Maturity proceeds')
    response = signed_in.post(f'/investments/{iid}/redeem', data=payload)
    assert response.status_code == 302
    response = signed_in.post(f'/investments/{iid}/redeem', data=payload)
    assert response.status_code == 409
    with app.test_request_context():
        investment = db.session.get(Investment, iid)
        assert investment.status == 'redeemed'
        assert account_balance(db.session.get(Account, aid)) == 5050000
        assert investment_summary(investment.user_id)['totals']['value'] == 0
        assert investment_summary(investment.user_id)['closed_totals']['value'] == 1020000
        entries = db.session.scalars(db.select(Transaction).where(Transaction.entry_type == 'investment')).all()
        assert len(entries) == 2 and {row.kind for row in entries} == {'expense', 'income'}
        assert period_totals(investment.user_id) == dict(income=0, expense=0, balance=0)


def test_redemption_validates_date_and_owned_same_currency_account(signed_in, app):
    signed_in.post('/accounts/', data=account_data(currency='USD'))
    aid = account_id(app, 'Main bank')
    signed_in.post('/investments/', data=investment_data())
    iid = investment_id(app)
    payload = dict(amount='100', redemption_date='2026-11-02', account_id=aid)
    assert b'currencies must match' in signed_in.post(f'/investments/{iid}/redeem', data=payload).data
    signed_in.post('/accounts/', data=account_data(name='INR'))
    payload.update(account_id=account_id(app, 'INR'), redemption_date='2026-10-01')
    assert b'on or after' in signed_in.post(f'/investments/{iid}/redeem', data=payload).data
    payload.update(account_id=999999, redemption_date='2026-11-02')
    assert signed_in.post(f'/investments/{iid}/redeem', data=payload).status_code == 200
    with app.app_context():
        assert db.session.get(Investment, iid).status == 'active'
        assert db.session.scalar(db.select(db.func.count(Transaction.id))) == 0


def test_status_only_closure_can_record_proceeds_explicitly(signed_in, app):
    signed_in.post('/accounts/', data=account_data())
    aid = account_id(app, 'Main bank')
    signed_in.post('/investments/', data=investment_data(status='redeemed'))
    iid = investment_id(app)
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Transaction.id))) == 0
    response = signed_in.post(f'/investments/{iid}/redeem', data=dict(amount='10500',
        redemption_date='2026-11-02', account_id=aid))
    assert response.status_code == 302


def test_fee_without_inr_rate_does_not_partially_transfer(signed_in, app):
    signed_in.post('/accounts/', data=account_data(currency='USD'))
    signed_in.post('/accounts/', data=account_data(name='Other', currency='USD', opening_balance='0'))
    source, destination = account_id(app, 'Main bank'), account_id(app, 'Other')
    response = signed_in.post('/accounts/transfer', data=dict(source_account_id=source,
        destination_account_id=destination, amount='100', fee='2', transfer_date='2026-10-30'))
    assert response.status_code == 200
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Transaction.id))) == 0
        assert account_balance(db.session.get(Account, source)) == 100000


def test_investment_cash_entries_cannot_be_edited_or_deleted(signed_in, app):
    signed_in.post('/accounts/', data=account_data(opening_balance='50000'))
    aid = account_id(app, 'Main bank')
    signed_in.post('/investments/', data=investment_data(account_id=str(aid)))
    iid = investment_id(app)
    signed_in.post(f'/investments/{iid}/redeem', data=dict(amount='10500', redemption_date='2026-11-02', account_id=aid))
    with app.app_context():
        ids = list(db.session.scalars(db.select(Transaction.id)))
    for tid in ids:
        assert signed_in.get(f'/expenses/{tid}/edit').status_code == 409
        assert signed_in.post(f'/expenses/{tid}/delete', data={}).status_code == 409
    response = signed_in.post(f'/investments/{iid}/edit', data=investment_data(account_id=str(aid), status='active'))
    assert b'cannot reopen' in response.data
    with app.app_context():
        assert db.session.get(Investment, iid).status == 'redeemed'
        assert db.session.scalar(db.select(db.func.count(Transaction.id))) == 2
        assert account_balance(db.session.get(Account, aid)) == 5050000


def test_concurrent_redemptions_credit_once(signed_in, app):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, local
    from sqlalchemy import event
    signed_in.post('/accounts/', data=account_data())
    aid = account_id(app, 'Main bank')
    signed_in.post('/investments/', data=investment_data())
    iid = investment_id(app)
    cookie = signed_in.get_cookie('session').value
    barrier, seen = Barrier(2), local()
    with app.app_context():
        engine = db.engine

    def synchronize_reads(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT') and 'FROM investment ' in statement and not getattr(seen, 'read', False):
            seen.read = True
            barrier.wait(timeout=10)

    def redeem(_):
        with app.test_client() as client:
            client.set_cookie('session', cookie)
            return client.post(f'/investments/{iid}/redeem', data=dict(amount='10500',
                redemption_date='2026-11-02', account_id=aid)).status_code

    event.listen(engine, 'after_cursor_execute', synchronize_reads)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            assert sorted(workers.map(redeem, range(2))) == [302, 409]
    finally:
        event.remove(engine, 'after_cursor_execute', synchronize_reads)
    with app.app_context():
        assert account_balance(db.session.get(Account, aid)) == 1150000
        assert db.session.scalar(db.select(db.func.count(Transaction.id))) == 1
