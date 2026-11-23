from datetime import date
from app.extensions import db
from app.models import Commitment, CommitmentOccurrence, Notification, User
from app.services import emi_schedule, generate_notifications


def test_add_forms_are_separate_pages(signed_in):
    for overview, form_url, title in [('/commitments','/commitments/add','Add commitment'),('/investments/','/investments/add','Add investment')]:
        page=signed_in.get(overview)
        assert page.status_code==200
        assert 'name="original_principal"' not in page.text
        assert 'name="amount_invested"' not in page.text
        form=signed_in.get(form_url)
        assert form.status_code==200
        assert title in form.text
        assert 'planning-form' in form.text
        invalid=signed_in.post(form_url,data={'name':'Test'})
        assert invalid.status_code==200
        assert 'planning-form' in invalid.text
        assert 'invalid-feedback' in invalid.text


def test_calendar_is_aligned_month_with_navigation(signed_in):
    page=signed_in.get('/calendar?month=2026-11')
    assert page.status_code==200
    assert 'month-calendar' in page.text
    assert '<th scope="col">Mon</th>' in page.text
    assert 'datetime="2026-10-26"' in page.text
    assert 'datetime="2026-11-01"' in page.text
    assert 'month=2026-12' in page.text
    assert 'month=2026-10' in page.text


def test_emi_schedule_reminders_owned_and_deduplicated(signed_in,app):
    with app.app_context():
        user=db.session.scalar(db.select(User))
        other=User(name='Other',email='other@example.com');other.set_password('long-test-password')
        db.session.add(other);db.session.flush()
        for uid,name,day in [(user.id,'Near EMI',31),(user.id,'Later EMI',date(2026,11,10).day),(other.id,'Private EMI',31)]:
            due=date(2026,10,31) if day==31 else date(2026,11,10)
            db.session.add(Commitment(user_id=uid,name=name,commitment_type='loan',amount_minor=10000,start_date=date(2026,10,1),next_due=due,frequency='monthly',reminder_days=3))
        db.session.commit()
        items=emi_schedule(user.id,date(2026,10,30))
        assert [i.due_date for i in items]==sorted(i.due_date for i in items)
        assert all(i.commitment.user_id==user.id for i in items)
        generate_notifications(user.id,date(2026,10,30));generate_notifications(user.id,date(2026,10,30))
        notices=db.session.scalars(db.select(Notification).where(Notification.kind=='emi_due')).all()
        assert len(notices)==1
        assert 'Near EMI' in notices[0].title
        # Reading no longer stores rows; paying stores the claimed date's row.
        from app.obligations import claim_occurrence
        item=items[0];claim_occurrence(item.commitment,item.due_date).status='paid';db.session.commit()
        assert (item.commitment_id,item.due_date) not in [(row.commitment_id,row.due_date) for row in emi_schedule(user.id,date(2026,10,30))]
    page=signed_in.get('/commitments?sort=name')
    assert page.status_code==200
    assert 'EMI payments' in page.text
    assert 'Private EMI' not in page.text


def test_calendar_shows_owned_emi_and_bill_links(signed_in, app):
    from app.models import CreditCardBill
    with app.app_context():
        user=db.session.scalar(db.select(User))
        loan=Commitment(user_id=user.id,name='Laptop instalment',commitment_type='loan',amount_minor=250000,start_date=date(2026,11,1),next_due=date(2026,11,25),frequency='monthly',reminder_days=3)
        bill=CreditCardBill(user_id=user.id,card_name='Study card',amount_minor=10000,due_date=date(2026,11,25))
        db.session.add_all([loan,bill]);db.session.commit()
    response=signed_in.get('/calendar?month=2026-11')
    assert response.status_code==200
    assert 'EMI · Laptop instalment' in response.text
    assert 'Study card' in response.text
    assert '/pay' in response.text
    assert 'INR 2,500.00' in response.text


def test_recurring_add_page_and_save(signed_in,app):
    from app.models import Category, RecurringExpense
    response=signed_in.get('/expenses/recurring')
    assert response.status_code==200
    assert '/expenses/recurring/add' in response.text
    assert 'name="amount"' not in response.text.split('<main',1)[1].split('</main>',1)[0]
    response=signed_in.get('/expenses/recurring/add')
    assert response.status_code==200
    assert 'planning-form' in response.text
    invalid=signed_in.post('/expenses/recurring/add',data={})
    assert invalid.status_code==200
    assert 'invalid-feedback' in invalid.text
    with app.app_context():
        category=db.session.scalar(db.select(Category))
        cid=category.id
    response=signed_in.post('/expenses/recurring/add',data={
        'category_id':cid,'account_id':0,'amount':'250.00','next_due':'2026-11-25',
        'frequency':'monthly','payment_method':'Cash','notes':'Subscription','active':'y'})
    assert response.status_code==302
    assert response.headers['Location'].endswith('/expenses/recurring')
    with app.app_context():
        assert db.session.scalar(db.select(RecurringExpense)).amount_minor==25000

