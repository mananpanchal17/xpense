"""Small, labelled sample ledger for a new workspace only."""
from .extensions import db
from .models import Category, Transaction, User


def load_sample_data(user_id, reference):
    # Serialize the emptiness check with other loaders in SQLite.
    db.session.execute(db.update(User).where(User.id == user_id).values(name=User.name))
    if db.session.scalar(db.select(Transaction.id).where(Transaction.user_id == user_id).limit(1)):
        raise ValueError('Sample data is available only when your ledger is empty.')
    categories = {row.name:row for row in db.session.scalars(db.select(Category).where(Category.user_id == user_id))}
    for name, kind, amount, label in [('Salary','income',5000000,'Sample · monthly salary'),('Food','expense',345050,'Sample · groceries'),('Travel','expense',120000,'Sample · transport'),('Bills','expense',249900,'Sample · utilities')]:
        category=categories.get(name)
        if category is None:
            category=Category(user_id=user_id,name=name);db.session.add(category);db.session.flush();categories[name]=category
        db.session.add(Transaction(user_id=user_id,category_id=category.id,kind=kind,amount_minor=amount,base_amount_minor=amount,currency='INR',date=reference,payment_method='Other',notes=label))
