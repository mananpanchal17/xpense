from datetime import date
from decimal import Decimal
from app.extensions import db
from app.models import Investment, ExchangeRate, User
from app.services import investment_summary


def test_investment_totals_convert_native_currency_to_inr(signed_in,app):
    with app.app_context():
        user=db.session.scalar(db.select(User))
        db.session.add_all([Investment(user_id=user.id,name='USD holding',investment_type='stocks',currency='USD',purchase_date=date(2026,10,1),amount_invested_minor=10000,current_value_minor=12000,interest_earned_minor=100,dividend_income_minor=50,status='active'),ExchangeRate(base_currency='USD',quote_currency='INR',rate=Decimal('83'),provider='fixture')])
        db.session.commit()
        with app.test_request_context('/investments/'):
            totals=investment_summary(user.id)['totals']
        assert totals['invested']==830000
        assert totals['value']==996000
        assert totals['gain']==166000
        assert totals['allocation']['stocks']==996000


def test_unknown_investment_conversion_is_explicitly_excluded(signed_in,app):
    with app.app_context():
        user=db.session.scalar(db.select(User))
        db.session.add(Investment(user_id=user.id,name='USD holding',investment_type='stocks',currency='USD',purchase_date=date(2026,10,1),amount_invested_minor=10000,current_value_minor=12000,status='active'))
        db.session.commit()
        with app.test_request_context('/investments/'):
            totals=investment_summary(user.id)['totals']
        assert totals['invested']==0
        assert totals['unconverted_count']==1
