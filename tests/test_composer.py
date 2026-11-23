from conftest import category_id, transaction_data
from app.extensions import db
from app.models import Transaction


def test_composer_saves_validated_expense_as_json(signed_in, app):
    cid=category_id(app)
    response=signed_in.post('/expenses/add',data=transaction_data(cid,amount='0.30'),headers={'Accept':'application/json'})
    assert response.status_code == 201
    assert response.json['transaction']['amount_minor'] == 30
    assert response.json['transaction']['currency'] == 'INR'


def test_composer_returns_field_errors_without_writing(signed_in, app):
    cid=category_id(app)
    response=signed_in.post('/expenses/add',data=transaction_data(cid,amount='NaN'),headers={'Accept':'application/json'})
    assert response.status_code == 422
    assert 'amount' in response.json['errors']
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Transaction.id))) == 0


def test_sample_data_is_limited_to_empty_ledger(signed_in, app):
    response=signed_in.post('/expenses/sample-data',data={})
    assert response.status_code == 302
    with app.app_context():
        count=db.session.scalar(db.select(db.func.count(Transaction.id)))
        assert count > 0
    response=signed_in.post('/expenses/sample-data',data={})
    assert response.status_code == 409
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Transaction.id))) == count


def test_global_composer_has_labels_csrf_and_keyboard_hint(signed_in, app):
    app.config["WTF_CSRF_ENABLED"] = True
    html=signed_in.get('/expenses/').text
    assert 'id="expense-composer"' in html
    assert 'id="compose-amount"' in html
    assert 'name="csrf_token"' in html
    assert 'aria-keyshortcuts="c Meta+k Control+k"' in html
