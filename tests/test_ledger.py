from datetime import date
from decimal import Decimal
import csv
import io
import pytest
from app.extensions import db
from app.models import Account, Budget, Category, Transaction, Transfer, User
from app.services import period_totals, budget_status
from conftest import category_id, transaction_data
from test_accounts import account_data, account_id


def seed(app, **extra):
    with app.app_context():
        user = db.session.scalar(db.select(User))
        category = db.session.scalar(db.select(Category).where(Category.user_id == user.id, Category.name == 'Food'))
        values = dict(user_id=user.id, category_id=category.id, kind='expense', amount_minor=100, base_amount_minor=8300, currency='USD', date=date(2026,10,15), payment_method='Cash', notes='Foreign lunch')
        values.update(extra)
        row = Transaction(**values)
        db.session.add(row)
        db.session.commit()
        return row.id, category.id


def test_inr_rollups_budgets_and_charts_use_snapshot(signed_in, app):
    _, cid = seed(app)
    with app.app_context():
        user = db.session.scalar(db.select(User))
        db.session.add(Budget(user_id=user.id,category_id=cid,month=date(2026,10,1),amount_minor=10000))
        db.session.commit()
        assert period_totals(user.id,date(2026,10,1),date(2026,11,1))['expense'] == 8300
        item = budget_status(user.id,date(2026,10,1))[0]
        assert item['spent'] == 8300
        assert item['remaining'] == 1700
        assert item['percent'] == 83
    payload = signed_in.get('/reports/chart-data').get_json()
    assert payload['expense'][-1] == 83
    assert payload['category_values'] == [83]
    assert payload['currency'] == 'INR'


@pytest.mark.parametrize('amount,destination,fee',[('100','99','0'),('1000','','1')])
def test_transfer_rejects_money_creation_and_fee_overdraft(signed_in, app, amount, destination, fee):
    signed_in.post('/accounts/',data=account_data())
    signed_in.post('/accounts/',data=account_data(name='Cash',opening_balance='0'))
    response = signed_in.post('/accounts/transfer',data=dict(source_account_id=account_id(app,'Main bank'),destination_account_id=account_id(app,'Cash'),amount=amount,destination_amount=destination,fee=fee,transfer_date='2026-10-30'))
    assert response.status_code == 200
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Transfer.id))) == 0


def test_transfer_legs_cannot_be_edited_or_deleted(signed_in,app):
    rid,cid = seed(app,entry_type='transfer')
    assert signed_in.post(f'/expenses/{rid}/edit',data=transaction_data(cid)).status_code == 409
    assert signed_in.post(f'/expenses/{rid}/delete',data={}).status_code == 409
    with app.app_context():
        assert db.session.get(Transaction,rid).amount_minor == 100


def test_split_conserves_native_and_inr_and_protects_linked_entries(signed_in,app):
    rid,cid = seed(app,amount_minor=101,base_amount_minor=8383)
    response = signed_in.post(f'/expenses/{rid}/split',json={'parts':[{'category_id':cid,'amount':'0.50'},{'category_id':cid,'amount':'0.51'}]})
    assert response.status_code == 201
    with app.app_context():
        parent = db.session.get(Transaction,rid)
        assert parent.entry_type == 'split_parent'
        children = db.session.scalars(db.select(Transaction).where(Transaction.parent_id == rid)).all()
        assert [r.amount_minor for r in children] == [50,51]
        assert sum(r.base_amount_minor for r in children) == 8383
        assert period_totals(parent.user_id,date(2026,10,1),date(2026,11,1))['expense'] == 8383
        child_id = children[0].id
    assert signed_in.post(f'/expenses/{rid}/delete',data={}).status_code == 409
    assert signed_in.post(f'/expenses/{child_id}/delete',data={}).status_code == 409


def test_split_rejects_nonconserving_parts(signed_in,app):
    rid,cid = seed(app)
    assert signed_in.post(f'/expenses/{rid}/split',json={'parts':[{'category_id':cid,'amount':'0.49'},{'category_id':cid,'amount':'0.50'}]}).status_code == 400
    with app.app_context():
        assert db.session.get(Transaction,rid).entry_type == 'normal'


def test_refund_reduces_refund_month_and_prevents_overrefund(signed_in,app):
    rid,cid = seed(app,amount_minor=300,base_amount_minor=24900)
    response = signed_in.post(f'/expenses/{rid}/refund',json={'amount':'1.00','date':'2026-11-05','notes':'Returned item'})
    assert response.status_code == 201
    with app.app_context():
        original = db.session.get(Transaction,rid)
        assert period_totals(original.user_id,date(2026,10,1),date(2026,11,1))['expense'] == 24900
        assert period_totals(original.user_id,date(2026,11,1),date(2026,12,1)) == {'income':0,'expense':-8300,'balance':8300}
    assert signed_in.post(f'/expenses/{rid}/refund',json={'amount':'2.01','date':'2026-11-06'}).status_code == 400
    assert signed_in.post(f'/expenses/{rid}/delete',data={}).status_code == 409


def test_csv_keeps_currency_and_snapshot_on_roundtrip(signed_in,app):
    seed(app)
    body = signed_in.get('/export/csv').data.decode('utf-8-sig')
    row = next(csv.DictReader(io.StringIO(body)))
    assert row['currency'] == 'USD'
    assert row['base_amount_minor'] == '8300'
    response = signed_in.post('/expenses/import',data={'file':(io.BytesIO(body.encode()),'roundtrip.csv')},content_type='multipart/form-data')
    assert response.status_code == 302
    with app.app_context():
        rows = db.session.scalars(db.select(Transaction)).all()
        assert len(rows) == 2
        assert {(r.currency,r.base_amount_minor) for r in rows} == {('USD',8300)}

def test_profile_cannot_reinterpret_existing_ledger(signed_in, app):
    seed(app)
    response = signed_in.post('/auth/profile',data={'name':'Student','email':'student@example.com','currency':'USD','current_password':'correct-horse-123'})
    assert response.status_code == 200
    with app.app_context():
        assert db.session.scalar(db.select(User)).currency == 'INR'


def test_account_currency_cannot_relabel_posted_history(signed_in,app):
    signed_in.post('/accounts/',data=account_data())
    aid = account_id(app,'Main bank')
    seed(app,account_id=aid,currency='INR',base_amount_minor=100)
    response = signed_in.post(f'/accounts/{aid}/edit',data=account_data(currency='USD'))
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(Account,aid).currency == 'INR'


def test_missing_foreign_snapshot_is_visible_in_reporting(signed_in,app):
    seed(app,base_amount_minor=None)
    response = signed_in.get('/reports/chart-data')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['unconverted_count'] == 1
    assert payload['expense'][-1] == 0


def test_split_and_refund_reject_other_users_categories_and_entries(signed_in,app):
    rid,cid = seed(app)
    with app.app_context():
        other = User(name='Other',email='other@example.com',password_hash='unused')
        db.session.add(other)
        db.session.flush()
        foreign = Category(user_id=other.id,name='Private')
        db.session.add(foreign)
        db.session.commit()
        foreign_id = foreign.id
        other_id = other.id
    response = signed_in.post(f'/expenses/{rid}/split',json={'parts':[{'category_id':cid,'amount':'0.50'},{'category_id':foreign_id,'amount':'0.50'}]})
    assert response.status_code == 400
    with app.app_context():
        db.session.get(Transaction,rid).user_id = other_id
        db.session.commit()
    assert signed_in.post(f'/expenses/{rid}/refund',json={'amount':'0.10','date':'2026-10-30'}).status_code == 404

def test_csv_roundtrip_preserves_formula_like_notes(signed_in,app):
    cid = category_id(app)
    signed_in.post('/expenses/add',data=transaction_data(cid,notes='=SUM(A1:A2)'))
    body = signed_in.get('/export/csv').data
    response = signed_in.post('/expenses/import',data={'file':(io.BytesIO(body),'roundtrip.csv')},content_type='multipart/form-data')
    assert response.status_code == 302
    with app.app_context():
        assert {r.notes for r in db.session.scalars(db.select(Transaction))} == {'=SUM(A1:A2)'}

def test_inverse_cached_rates_can_save_foreign_expenses(signed_in,app):
    from app.models import ExchangeRate
    with app.app_context():
        db.session.add(ExchangeRate(base_currency='INR',quote_currency='USD',rate=Decimal('0.0125'),provider='fixture'))
        db.session.commit()
    signed_in.post('/accounts/',data=account_data(currency='USD'))
    response = signed_in.post('/expenses/add',data=transaction_data(category_id(app),account_id=account_id(app,'Main bank'),amount='1.25'))
    assert response.status_code == 302
    with app.app_context():
        row = db.session.scalar(db.select(Transaction))
        assert row.base_amount_minor == 10000
        assert row.exchange_rate == Decimal('80')
        assert row.exchange_rate_source == 'fixture (inverse)'


def test_pdf_subtotals_exclude_transfer_and_adjustment_legs(signed_in,app):
    import re
    import zlib
    import base64
    seed(app)
    seed(app,entry_type='transfer',currency='INR',base_amount_minor=100)
    seed(app,entry_type='adjustment',currency='INR',base_amount_minor=200)
    response = signed_in.get('/export/pdf')
    streams = []
    for stream in re.findall(rb'stream\r?\n(.*?)endstream',response.data,re.S):
        try:
            streams.append(zlib.decompress(base64.a85decode(stream.strip(),adobe=True)))
        except (ValueError,zlib.error):
            pass
    assert b'Expenses: 83.00' in b' '.join(streams)


def test_forecast_converts_known_foreign_recurring_payments(signed_in,app):
    from app.models import ExchangeRate, RecurringExpense
    from app.services import financial_forecast
    signed_in.post('/accounts/',data=account_data(currency='USD'))
    with app.app_context():
        user = db.session.scalar(db.select(User))
        cid = db.session.scalar(db.select(Category.id).where(Category.user_id==user.id,Category.name=='Food'))
        aid = db.session.scalar(db.select(Account.id))
        db.session.add(ExchangeRate(base_currency='INR',quote_currency='USD',rate=Decimal('0.0125'),provider='fixture'))
        db.session.add(RecurringExpense(user_id=user.id,category_id=cid,account_id=aid,amount_minor=100,payment_method='Cash',notes='',frequency='monthly',next_due=date(2026,10,31),anchor_day=31,active=True))
        db.session.commit()
        assert financial_forecast(user.id,date(2026,10,30))['recurring_total'] == 8000


def test_concurrent_refunds_cannot_exceed_original(signed_in,app):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.ledger import refund_transaction
    rid,_ = seed(app,amount_minor=300,base_amount_minor=24900)
    barrier = Barrier(2)
    def submit():
        with app.app_context():
            row = db.session.get(Transaction,rid)
            uid = row.user_id
            barrier.wait(timeout=10)
            try:
                refund_transaction(row,'2.00','2026-10-30','',uid)
                db.session.commit()
                return 'saved'
            except ValueError:
                db.session.rollback()
                return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(),range(2)))
    assert sorted(results) == ['rejected','saved']
    with app.app_context():
        assert db.session.scalar(db.select(db.func.sum(Transaction.amount_minor)).where(Transaction.refund_of_id==rid)) == 200

def test_concurrent_splits_create_only_one_child_set(signed_in,app):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from werkzeug.exceptions import Conflict
    from app.ledger import split_transaction
    rid,cid = seed(app)
    barrier = Barrier(2)
    def submit():
        with app.app_context():
            row = db.session.get(Transaction,rid)
            uid = row.user_id
            barrier.wait(timeout=10)
            try:
                split_transaction(row,[{'category_id':cid,'amount':'0.50'},{'category_id':cid,'amount':'0.50'}],uid)
                db.session.commit()
                return 'saved'
            except (ValueError,Conflict):
                db.session.rollback()
                return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(),range(2)))
    assert sorted(results) == ['rejected','saved']
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Transaction.id)).where(Transaction.parent_id==rid)) == 2
