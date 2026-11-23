import pytest
from app.extensions import db
from app.models import Transaction
from conftest import category_id, transaction_data, register, login


def test_local_snapshot_is_tenant_scoped_and_formats_native_amount(signed_in,app):
    cid=category_id(app)
    signed_in.post('/expenses/add',data=transaction_data(cid,amount='12.34',notes='<script>alert(1)</script>'))
    response=signed_in.get('/expenses/data')
    assert response.status_code==200
    assert response.json['complete'] is True
    row=response.json['rows'][0]
    assert row['amount_minor']==1234
    assert 'INR 12.34' in response.json['html']
    assert '<script>alert(1)</script>' not in response.json['html']
    assert '&lt;script&gt;' in response.json['html']
    assert response.headers['Cache-Control']=='no-store'
    other=app.test_client();register(other,'other@example.com');login(other,'other@example.com')
    assert other.get('/expenses/data').json['rows']==[]


def test_incomplete_snapshot_never_activates_local_filtering(signed_in,app,monkeypatch):
    import app.expenses.routes as routes
    monkeypatch.setattr(routes,'LOCAL_SNAPSHOT_LIMIT',1,raising=False)
    cid=category_id(app)
    for amount in ['1','2']:
        signed_in.post('/expenses/add',data=transaction_data(cid,amount=amount))
    result=signed_in.get('/expenses/data').json
    assert result['complete'] is False
    assert result['rows']==[] and result['html']==''


def test_snapshot_requires_authentication(client):
    response=client.get('/expenses/data')
    assert response.status_code==302
    assert '/auth/login' in response.location


def test_async_delete_confirms_id_and_preserves_other_entries(signed_in,app):
    cid=category_id(app)
    signed_in.post('/expenses/add',data=transaction_data(cid))
    with app.app_context(): rid=db.session.scalar(db.select(Transaction.id))
    response=signed_in.post(f'/expenses/{rid}/delete',headers={'Accept':'application/json'})
    assert response.status_code==200 and response.json['id']==rid
    assert signed_in.get('/expenses/data').json['rows']==[]
