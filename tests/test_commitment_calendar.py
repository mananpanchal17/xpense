from datetime import date
import pytest
from app.extensions import db
from app.models import Commitment, User
from app.obligations import due_items
from app.services import commitment_summary

@pytest.mark.parametrize('frequency,start,end,expected',[
 ('monthly',date(2026,1,31),date(2026,4,1),[date(2026,1,31),date(2026,2,28),date(2026,3,31)]),
 ('yearly',date(2024,2,29),date(2029,1,1),[date(2024,2,29),date(2025,2,28),date(2026,2,28),date(2027,2,28),date(2028,2,29)]),
 ('quarterly',date(2026,1,31),date(2026,8,1),[date(2026,1,31),date(2026,4,30),date(2026,7,31)])])
def test_commitment_calendar_preserves_anchor(signed_in,app,frequency,start,end,expected):
 with app.app_context():
  uid=db.session.scalar(db.select(User.id))
  row=Commitment(user_id=uid,name='Calendar rule',commitment_type='bill',amount_minor=100,start_date=start,next_due=start,frequency=frequency,payment_method='Cash',reminder_days=3)
  db.session.add(row);db.session.flush()
  assert [item.due_date for item in due_items(uid,start,end,reference=end)]==expected


def test_commitment_end_date_and_yearly_rollup(signed_in,app):
 with app.app_context():
  uid=db.session.scalar(db.select(User.id))
  row=Commitment(user_id=uid,name='Annual bill',commitment_type='bill',amount_minor=12000,start_date=date(2026,10,15),next_due=date(2026,10,15),end_date=date(2026,10,31),frequency='yearly',payment_method='Cash',reminder_days=3)
  db.session.add(row);db.session.flush()
  assert [item.due_date for item in due_items(uid,date(2026,10,1),date(2028,1,1),reference=date(2026,10,1))]==[date(2026,10,15)]
  assert commitment_summary(uid,date(2026,10,1))['annual_recurring']==12000
