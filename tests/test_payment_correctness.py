"""Regression coverage for payment history, arrears, savings, and calendar counters."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
import re

from app.extensions import db
from app.models import (Account, Category, Commitment, CommitmentOccurrence, CreditCardBill,
    ExchangeRate, GoalContribution, RecurringExpense, RecurringOccurrence, SavingsGoal,
    Transaction, User)
from app.services import commitment_summary, emi_schedule


def loan_occurrence(app, currency='INR', legacy=False):
    with app.app_context():
        uid = db.session.scalar(db.select(User.id))
        cid = db.session.scalar(db.select(Category.id))
        account = Account(user_id=uid, name='Payment account', account_type='bank', currency=currency)
        db.session.add(account); db.session.flush()
        loan = Commitment(user_id=uid, category_id=cid, account_id=account.id,
            name='Loan', commitment_type='loan', amount_minor=10000,
            start_date=date(2026, 10, 1), next_due=date(2026, 10, 30), frequency='once')
        db.session.add(loan); db.session.flush()
        item = CommitmentOccurrence(commitment_id=loan.id, due_date=date(2026, 10, 30), expected_minor=10000)
        db.session.add(item)
        if currency == 'USD':
            db.session.add(ExchangeRate(base_currency='USD', quote_currency='INR', rate=Decimal('83.125'), provider='fixture'))
        if legacy:
            txn = Transaction(user_id=uid, category_id=cid, account_id=account.id,
                kind='expense', amount_minor=4050, base_amount_minor=4050, currency='INR',
                date=date(2026, 10, 29), payment_method='Cash')
            db.session.add(txn); db.session.flush()
            item.actual_minor=4000; item.late_fee_minor=50; item.transaction_id=txn.id
            item.paid_date=date(2026, 10, 29); item.status='partial'
        db.session.commit()
        return loan.id, item.id


def payment(amount, token, fee=''):
    return {'actual_amount':amount, 'late_fee':fee, 'paid_date':'2026-10-30', 'payment_token':token}


def test_partial_payments_accumulate_and_retries_debit_once(signed_in, app):
    lid, oid = loan_occurrence(app, 'USD')
    url=f'/commitments/{lid}/occurrence/{oid}/pay'
    signed_in.post(url, data=payment('40.01', 'first', '.03'))
    signed_in.post(url, data=payment('40.01', 'first', '.03'))
    page=signed_in.get(url)
    assert 'value="59.99"' in page.text
    signed_in.post(url, data=payment('59.99', 'second', '.02'))
    with app.app_context():
        row=db.session.get(CommitmentOccurrence, oid)
        assert (row.actual_minor,row.late_fee_minor,row.status)==(10000,5,'paid')
        entries=db.session.scalars(db.select(Transaction).order_by(Transaction.id)).all()
        assert [(r.amount_minor,r.base_amount_minor,r.currency) for r in entries]==[(4004,332832,'USD'),(6001,498833,'USD')]
        first_id=entries[0].id
        assert row.transaction_id==first_id
        from app.models import CommitmentPayment
        assert db.session.scalar(db.select(db.func.count(CommitmentPayment.id)))==2


def test_legacy_partial_payment_retained_when_remainder_paid(signed_in,app):
    lid,oid=loan_occurrence(app,legacy=True)
    signed_in.post(f'/commitments/{lid}/occurrence/{oid}/pay',data=payment('60','remaining','1'))
    with app.app_context():
        row=db.session.get(CommitmentOccurrence,oid)
        assert (row.actual_minor,row.late_fee_minor,row.status)==(10000,150,'paid')
        assert db.session.scalar(db.select(db.func.count(Transaction.id)))==2
        from app.models import CommitmentPayment
        records=db.session.scalars(db.select(CommitmentPayment)).all()
        assert sorted((p.amount_minor,p.late_fee_minor) for p in records)==[(4000,50),(6000,100)]


def test_simultaneous_payments_cannot_overpay(signed_in,app):
    lid,oid=loan_occurrence(app)
    cookie=signed_in.get_cookie('session')
    def submit(token):
        with app.test_client() as client:
            client.set_cookie('session',cookie.value)
            return client.post(f'/commitments/{lid}/occurrence/{oid}/pay',data=payment('60',token)).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit,['one','two']))==[200,302]
    with app.app_context():
        assert db.session.get(CommitmentOccurrence,oid).actual_minor==6000
        assert db.session.scalar(db.select(db.func.count(Transaction.id)))==1


def test_arrears_generated_from_original_anchor_and_partial_reduced(signed_in,app):
    with app.app_context():
        uid=db.session.scalar(db.select(User.id))
        loan=Commitment(user_id=uid,name='Old EMI',commitment_type='loan',amount_minor=10000,
            start_date=date(2026,7,1),next_due=date(2026,7,31),frequency='monthly')
        db.session.add(loan);db.session.commit()
        items=emi_schedule(uid,date(2026,10,30))
        assert [r.due_date for r in items[:3]]==[date(2026,7,31),date(2026,8,31),date(2026,9,30)]
        # Reading no longer stores rows; a partial payment is stored against the claimed date.
        from app.obligations import claim_occurrence
        july=claim_occurrence(items[0].commitment,items[0].due_date);july.actual_minor=4000;july.status='partial';db.session.commit()
        summary=commitment_summary(uid,date(2026,10,30))
        assert summary['overdue']==26000
        assert summary['remaining']==10000


def goal_history(app, amounts, target=10000):
    with app.app_context():
        goal=SavingsGoal(user_id=db.session.scalar(db.select(User.id)),name='Savings',target_minor=target,
            saved_minor=sum(amounts),target_date=date(2026,12,1),status='completed' if sum(amounts)>=target else 'active')
        db.session.add(goal);db.session.flush()
        records=[GoalContribution(goal_id=goal.id,amount_minor=a,contribution_date=date(2026,10,29)) for a in amounts]
        db.session.add_all(records);db.session.commit()
        return goal.id,[r.id for r in records]


def test_goal_delete_cannot_remove_spent_deposit(signed_in,app):
    gid,ids=goal_history(app,[10000,-8000])
    response=signed_in.post(f'/goals/{gid}/contributions/{ids[0]}/delete',follow_redirects=True)
    assert response.status_code==200
    with app.app_context():
        assert db.session.get(SavingsGoal,gid).saved_minor==2000
        assert db.session.get(GoalContribution,ids[0]) is not None


def test_goal_delete_withdrawal_restores_completion_and_target_edit_recalculates(signed_in,app):
    gid,ids=goal_history(app,[10000,-2000])
    signed_in.post(f'/goals/{gid}/contributions/{ids[1]}/delete')
    with app.app_context():
        assert db.session.get(SavingsGoal,gid).status=='completed'
    signed_in.post(f'/goals/{gid}/edit',data={'name':'Savings','target_amount':'200','target_date':'2026-12-01'})
    with app.app_context():
        assert db.session.get(SavingsGoal,gid).status=='active'


def test_calendar_counts_all_sources_independent_selected_month(signed_in,app,monkeypatch):
    monkeypatch.setattr('app.insights.routes.today',lambda:date(2026,10,30))
    with app.app_context():
        uid=db.session.scalar(db.select(User.id));cid=db.session.scalar(db.select(Category.id))
        db.session.add(CreditCardBill(user_id=uid,card_name='Old bill',amount_minor=100,due_date=date(2026,9,1)))
        db.session.add(Commitment(user_id=uid,name='Upcoming EMI',commitment_type='loan',amount_minor=100,
            start_date=date(2026,10,1),next_due=date(2026,11,2),frequency='once'))
        rule=RecurringExpense(user_id=uid,category_id=cid,amount_minor=100,next_due=date(2026,10,30),frequency='monthly',anchor_day=30,payment_method='Cash',active=True,status='active')
        db.session.add(rule);db.session.flush()
        db.session.add(RecurringOccurrence(recurring_id=rule.id,due_date=date(2026,10,30),status='scheduled'))
        db.session.commit()
    for month in ['2026-10','2026-11','2027-01']:
        page=signed_in.get('/calendar?month='+month)
        assert re.search(r'Due today\s*<strong>1</strong>',page.text)
        assert re.search(r'Next 7 days\s*<strong>1</strong>',page.text)
        assert re.search(r'Overdue\s*<strong>1</strong>',page.text)


def test_payment_ledger_entries_cannot_be_changed_independently(signed_in,app):
    lid,oid=loan_occurrence(app,legacy=True)
    signed_in.post(f'/commitments/{lid}/occurrence/{oid}/pay',data=payment('60','finish'))
    with app.app_context():
        ids=list(db.session.scalars(db.select(Transaction.id)).all())
    assert len(ids)==2
    for tid in ids:
        assert signed_in.get(f'/expenses/{tid}/edit').status_code==409
        assert signed_in.post(f'/expenses/{tid}/delete').status_code==409
        assert signed_in.post(f'/expenses/{tid}/refund',json={'amount':'1','date':'2026-10-30','notes':''}).status_code==409
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Transaction.id)))==2


def test_partial_summary_uses_saved_conversion_after_rates_change(signed_in,app):
    lid,oid=loan_occurrence(app,'USD')
    signed_in.post(f'/commitments/{lid}/occurrence/{oid}/pay',data=payment('40.01','first','.03'))
    with app.app_context():
        uid=db.session.scalar(db.select(User.id))
        db.session.scalar(db.select(ExchangeRate)).rate=Decimal('90')
        db.session.commit()
        summary=commitment_summary(uid,date(2026,10,30))
        assert summary['paid_month']==332583
        assert summary['remaining']==539910


def test_goal_concurrent_withdrawals_preserve_nonnegative_balance(signed_in,app):
    gid,_=goal_history(app,[10000])
    cookie=signed_in.get_cookie('session')
    def withdraw(_):
        with app.test_client() as client:
            client.set_cookie('session',cookie.value)
            return client.post(f'/goals/{gid}/contribute',data={
                'amount':'80','action':'withdraw','contribution_date':'2026-10-30'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(withdraw,range(2)))==[200,302]
    with app.app_context():
        assert db.session.get(SavingsGoal,gid).saved_minor==2000
        assert db.session.scalar(db.select(db.func.count(GoalContribution.id)))==2


def test_goal_cannot_be_completed_below_target(signed_in,app):
    gid,_=goal_history(app,[2000])
    assert signed_in.post(f'/goals/{gid}/complete').status_code==302
    with app.app_context():
        assert db.session.get(SavingsGoal,gid).status=='active'
