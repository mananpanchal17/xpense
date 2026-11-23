"""Disposable local preview. Uses only .qa/preview.db, never the user's database."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from app.extensions import db
from app.models import User, Category, Transaction, Budget
from datetime import date, timedelta
root = Path(__file__).resolve().parents[1]
app = create_app({'SECRET_KEY': 'local-preview-only-not-for-deployment-' * 2,
 'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + str(root / '.qa' / 'preview-v2.db'),
 'XPENSE_SAMPLE_MODE': True, 'TEMPLATES_AUTO_RELOAD': True, 'RATELIMIT_ENABLED': False, 'PRODUCTION': False, 'MAIL_MODE': 'console'})
with app.app_context():
 db.create_all()
 user = db.session.scalar(db.select(User).where(User.email == 'preview@example.com'))
 if not user:
  user = User(name='Preview workspace', email='preview@example.com', currency='INR')
  user.set_password('preview-local-123')
  db.session.add(user); db.session.flush()
  categories = {}
  for name in ['Food','Travel','Rent','Shopping','Bills','Salary','Other']:
   category=Category(user_id=user.id,name=name);db.session.add(category);db.session.flush();categories[name]=category
  now=date.today()
  for months_ago in range(11,-1,-1):
   month=(now.replace(day=1)-timedelta(days=months_ago*28)).replace(day=1)
   for name,amount,day,notes in [('Salary',6500000,1,'Monthly salary'),('Rent',1500000,2,'Apartment rent'),('Food',354000+months_ago*3000,8,'Groceries & meals'),('Travel',180000,15,'Metro & transport'),('Bills',249900,17,'Utilities'),('Shopping',129900,22,'Home essentials')]:
    db.session.add(Transaction(user_id=user.id,category_id=categories[name].id,kind='income' if name=='Salary' else 'expense',amount_minor=amount,base_amount_minor=amount,currency='INR',date=month.replace(day=min(day,now.day)) if month==now.replace(day=1) else month.replace(day=day),payment_method='UPI',notes=notes))
  for name,amount in [('Food',600000),('Travel',250000),('Bills',300000),('Shopping',200000)]:
   db.session.add(Budget(user_id=user.id,category_id=categories[name].id,month=now.replace(day=1),amount_minor=amount))
  db.session.commit()
# This launcher is intentionally a synthetic, loopback-only preview, not a production entry point.
@app.before_request
def synthetic_preview_session():
 from flask_login import login_user
 login_user(db.session.scalar(db.select(User).where(User.email == 'preview@example.com')))

if __name__ == '__main__':
 app.run(host='127.0.0.1',port=int(os.environ.get('XPENSE_PREVIEW_PORT', '5055')),debug=False)
