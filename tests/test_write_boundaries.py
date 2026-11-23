import pytest
from conftest import category_id, transaction_data
from test_accounts import account_data, account_id
from app.extensions import db
from app.models import Transaction, Transfer

@pytest.mark.parametrize('action',['split','refund'])
def test_linked_endpoint_rejects_json_array(signed_in,app,action):
    signed_in.post('/expenses/add',data=transaction_data(category_id(app)))
    with app.app_context():
        rid=db.session.scalar(db.select(Transaction.id))
    response=signed_in.post(f'/expenses/{rid}/{action}',json=[{'amount':'1'}])
    assert response.status_code == 400
    assert response.json['error']


def test_transfer_fee_cannot_exceed_supported_combined_amount(signed_in,app):
    signed_in.post('/accounts/',data=account_data(name='Source',opening_balance='999999999.99'))
    signed_in.post('/accounts/',data=account_data(name='Destination',opening_balance='0'))
    # Add income so the balance check would otherwise permit the oversized debit.
    signed_in.post('/expenses/add',data=transaction_data(category_id(app),kind='income',amount='100',account_id=account_id(app,'Source')))
    response=signed_in.post('/accounts/transfer',data={'source_account_id':account_id(app,'Source'),'destination_account_id':account_id(app,'Destination'),'amount':'999999999.99','fee':'0.01','destination_amount':'','transfer_date':'2026-10-30','notes':''})
    assert response.status_code == 200
    assert 'supported' in response.text.lower()
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Transfer.id))) == 0


def test_json_composer_rejects_missing_real_csrf(signed_in,app):
    app.config['WTF_CSRF_ENABLED']=True
    response=signed_in.post('/expenses/add',data=transaction_data(category_id(app)),headers={'Accept':'application/json'})
    assert response.status_code == 400
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Transaction.id))) == 0
