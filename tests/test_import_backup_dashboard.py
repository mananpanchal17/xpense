import io
import json
from datetime import date
from app.extensions import db
from app.models import Transaction, Category, User


def test_csv_preview_confirmation_and_retry(signed_in, app):
    payload=b'kind,amount,category,date,payment_method,notes\nexpense,12.34,Food,2026-10-20,Cash,Tea\nexpense,12.34,Food,2026-10-20,Cash,Tea\n'
    r=signed_in.post('/expenses/import/preview',data={'file':(io.BytesIO(payload),'rows.csv')})
    assert r.status_code==200
    assert 'Possible duplicate' in r.text
    with app.app_context():
        from app.models import ImportBatch
        assert db.session.scalar(db.select(db.func.count(Transaction.id)))==0
        token=db.session.scalar(db.select(ImportBatch.token))
    assert signed_in.post('/expenses/import/confirm/'+token).status_code==302
    assert signed_in.post('/expenses/import/confirm/'+token).status_code==302
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Transaction.id)))==1


def test_financial_backup_restore_owned_roundtrip(signed_in,app):
    with app.app_context():
        user=db.session.scalar(db.select(User))
        cat=db.session.scalar(db.select(Category).where(Category.name=='Food'))
        parent=Transaction(user_id=user.id,category_id=cat.id,kind='expense',amount_minor=10000,base_amount_minor=10000,currency='INR',date=date(2026,10,20),payment_method='Cash',notes='Purchase')
        db.session.add(parent);db.session.flush()
        db.session.add(Transaction(user_id=user.id,category_id=cat.id,kind='income',amount_minor=2000,base_amount_minor=2000,currency='INR',date=date(2026,10,21),payment_method='Cash',notes='Refund',entry_type='refund',refund_of_id=parent.id))
        db.session.commit()
    result=signed_in.get('/backup/download')
    assert result.status_code==200
    data=result.get_json()
    assert 'password_hash' not in result.text
    assert len(data['tables']['Transaction'])==2
    signed_in.post('/auth/logout')
    signed_in.post('/auth/register',data={'name':'Restore','email':'restore@example.com','password':'correct-horse-123','confirm':'correct-horse-123'})
    signed_in.post('/auth/login',data={'email':'restore@example.com','password':'correct-horse-123'})
    response=signed_in.post('/backup/',data={'backup':(io.BytesIO(json.dumps(data).encode()),'backup.json')})
    assert response.status_code==302
    with app.app_context():
        uid=db.session.scalar(db.select(User.id).where(User.email=='restore@example.com'))
        rows=db.session.scalars(db.select(Transaction).where(Transaction.user_id==uid)).all()
        assert len(rows)==2
        refund=next(r for r in rows if r.entry_type=='refund')
        assert refund.refund_of_id==next(r.id for r in rows if r.entry_type=='normal')
    again=signed_in.post('/backup/',data={'backup':(io.BytesIO(json.dumps(data).encode()),'backup.json')})
    assert 'empty financial ledger' in again.text


def test_month_picker_updates_summary_and_chart(signed_in,app):
    page=signed_in.get('/?month=2026-09')
    assert page.status_code==200
    assert '2026-09' in page.text
    chart=signed_in.get('/reports/chart-data?month=2026-09').get_json()
    assert chart['labels'][-1]=='2026-09'
    assert signed_in.get('/?month=invalid').status_code==400
