"""Ledger boundary: validated money, tenant-scoped queries and linked writes.

Money stays in integer hundredths, including historical JPY entries. Reporting
uses saved INR snapshots; only legacy INR rows fall back to their native amount.
Callers own commit/rollback so linked writes and audit events are atomic.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from flask import abort, request
from sqlalchemy import case, func, or_
from sqlalchemy.orm import joinedload
from .extensions import db
from .models import Transaction, Category, Budget, Account

REPORTING_CURRENCY = "INR"
MAX_MINOR = 99999999999


def minor(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or abs(amount) > Decimal("999999999.99") or amount != amount.quantize(Decimal(".01")):
            raise ValueError("Use a finite amount with at most two decimal places.")
        return int(amount * 100)
    except (InvalidOperation, TypeError):
        raise ValueError("Enter a valid amount.") from None


def base_value(row):
    if row.base_amount_minor is not None:
        return row.base_amount_minor
    if row.currency in (None, "", "INR"):
        return row.amount_minor
    raise ValueError("This foreign-currency entry has no INR snapshot.")


def signed_value(row):
    # Unknown conversions contribute nothing and must accompany unconverted_count.
    if row.base_amount_minor is None and row.currency not in (None, "", "INR"):
        return 0
    return -base_value(row) if row.entry_type == "refund" else base_value(row)


def unconverted_count(user_id, start=None, end=None):
    query = db.select(func.count(Transaction.id)).where(Transaction.user_id == user_id, reporting_entries(), Transaction.base_amount_minor.is_(None), Transaction.currency.not_in(("", "INR")))
    if start:
        query = query.where(Transaction.date >= start)
    if end:
        query = query.where(Transaction.date < end)
    return db.session.scalar(query) or 0


def reporting_amount():
    return func.coalesce(Transaction.base_amount_minor, case((or_(Transaction.currency.is_(None), Transaction.currency == "", Transaction.currency == "INR"), Transaction.amount_minor), else_=0)) * case((Transaction.entry_type == "refund", -1), else_=1)


def reporting_entries():
    return Transaction.entry_type.in_(("normal", "refund"))


def report_kind():
    return case((Transaction.entry_type == "refund", "expense"), else_=Transaction.kind)


def period_totals(user_id, start=None, end=None):
    query = db.select(report_kind(), func.sum(reporting_amount())).where(Transaction.user_id == user_id, reporting_entries())
    if start:
        query = query.where(Transaction.date >= start)
    if end:
        query = query.where(Transaction.date < end)
    values = dict(db.session.execute(query.group_by(report_kind())).all())
    income, expense = values.get("income", 0) or 0, values.get("expense", 0) or 0
    return {"income": income, "expense": expense, "balance": income - expense}


def category_rollup(user_id, start, end):
    return db.session.execute(db.select(Category.name, report_kind(), func.sum(reporting_amount())).join(Transaction, Transaction.category_id == Category.id).where(Transaction.user_id == user_id, reporting_entries(), Transaction.date >= start, Transaction.date < end).group_by(Category.id, Category.name, report_kind()).order_by(Category.name)).all()


def budget_status(user_id, month):
    from .services import month_end
    spent = dict(db.session.execute(db.select(Transaction.category_id, func.sum(reporting_amount())).where(Transaction.user_id == user_id, report_kind() == "expense", reporting_entries(), Transaction.date >= month, Transaction.date < month_end(month)).group_by(Transaction.category_id)).all())
    result = []
    for budget in db.session.scalars(db.select(Budget).options(joinedload(Budget.category)).where(Budget.user_id == user_id, Budget.month == month)):
        used = spent.get(budget.category_id, 0)
        percent = Decimal(used) * 100 / budget.amount_minor
        result.append({"budget": budget, "spent": used, "remaining": budget.amount_minor - used, "percent": percent, "level": "danger" if percent >= 100 else "warning" if percent >= 80 else "success"})
    return result


def assert_commitment_unlinked(row):
    from .models import CommitmentOccurrence, CommitmentPayment
    linked = db.session.scalar(db.select(CommitmentOccurrence.id).where(
        CommitmentOccurrence.transaction_id == row.id).limit(1)) or db.session.scalar(
        db.select(CommitmentPayment.id).where(CommitmentPayment.transaction_id == row.id).limit(1))
    if linked:
        abort(409, description="Commitment payments cannot be changed independently of their payment history.")


def assert_editable(row):
    assert_commitment_unlinked(row)
    linked = db.session.scalar(db.select(Transaction.id).where(or_(Transaction.parent_id == row.id, Transaction.refund_of_id == row.id)).limit(1))
    if row.entry_type != "normal" or row.transfer_id or row.parent_id or row.refund_of_id or linked:
        abort(409, description="Linked ledger entries cannot be changed independently.")


def save_transaction(row, form, user_id):
    from .services import exchange_rate, today
    if row.id:
        _lock_entry(row, user_id)
        assert_editable(row)
    category = db.session.get(Category, form.category_id.data)
    if category is None or category.user_id != user_id:
        raise ValueError("Choose your own category.")
    account = db.session.get(Account, form.account_id.data) if form.account_id.data else None
    if account is not None and (account.user_id != user_id or not account.active):
        raise ValueError("Choose your own active account.")
    if form.account_id.data and account is None:
        raise ValueError("Account not found.")
    amount = minor(form.amount.data)
    if amount <= 0:
        raise ValueError("Amount must be positive.")
    currency = account.currency if account else "INR"
    # Preserve an existing snapshot for edits that do not change money/account.
    preserve = row.id and row.amount_minor == amount and row.account_id == (account.id if account else None) and row.currency == currency and row.base_amount_minor is not None
    rate, source, rate_date = (row.exchange_rate, row.exchange_rate_source, row.exchange_rate_date) if preserve else exchange_rate(currency, "INR")
    base = row.base_amount_minor if preserve else int((Decimal(amount) * rate).quantize(Decimal("1")))
    if not 0 < base <= MAX_MINOR:
        raise ValueError("Converted INR amount is outside the supported range.")
    row.user_id, row.kind, row.amount_minor = user_id, form.kind.data, amount
    row.category_id, row.date = category.id, form.date.data
    row.payment_method, row.notes = form.payment_method.data, form.notes.data or ""
    row.account_id, row.currency = account.id if account else None, currency
    row.base_amount_minor, row.exchange_rate = base, rate
    row.exchange_rate_date, row.exchange_rate_source = rate_date, source


def _lock_entry(row, user_id):
    # A no-op UPDATE obtains the database row/write lock before reading links.
    # Unlike SELECT FOR UPDATE, this also serializes writers on SQLite.
    result = db.session.execute(db.update(Transaction).where(Transaction.id == row.id, Transaction.user_id == user_id).values(amount_minor=Transaction.amount_minor).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise ValueError("Entry not found.")
    db.session.refresh(row)


def split_transaction(row, parts, user_id):
    _lock_entry(row, user_id)
    assert_editable(row)
    if row.user_id != user_id or row.kind != "expense":
        raise ValueError("Only your own expenses can be split.")
    if not isinstance(parts, list) or not 2 <= len(parts) <= 50:
        raise ValueError("Provide between 2 and 50 split parts.")
    validated = []
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("Invalid split part.")
        try:
            cid = int(part.get("category_id", 0))
        except (ValueError, TypeError):
            raise ValueError("Choose your own category.") from None
        category = db.session.get(Category, cid)
        amount = minor(part.get("amount"))
        notes = part.get("notes", row.notes)
        if not category or category.user_id != user_id or amount <= 0 or not isinstance(notes, str) or len(notes) > 500:
            raise ValueError("Each split needs your category, positive amount and notes up to 500 characters.")
        validated.append((cid, amount, notes))
    if sum(part[1] for part in validated) != row.amount_minor:
        raise ValueError("Split amounts must equal the original amount exactly.")
    total_base = base_value(row)
    children, cumulative, allocated = [], 0, 0
    for cid, amount, notes in validated:
        cumulative += amount
        next_base = total_base * cumulative // row.amount_minor
        child = Transaction(user_id=user_id, category_id=cid, kind="expense", amount_minor=amount, base_amount_minor=next_base - allocated, currency=row.currency, date=row.date, payment_method=row.payment_method, notes=notes, account_id=row.account_id, parent_id=row.id, entry_type="normal", exchange_rate=row.exchange_rate, exchange_rate_date=row.exchange_rate_date, exchange_rate_source=row.exchange_rate_source)
        allocated = next_base
        children.append(child)
    row.entry_type = "split_parent"
    db.session.add_all(children)
    return children


def refund_transaction(row, amount, refund_date, notes, user_id):
    _lock_entry(row, user_id)
    assert_commitment_unlinked(row)
    if row.user_id != user_id or row.kind != "expense" or row.entry_type != "normal" or row.transfer_id or row.refund_of_id:
        raise ValueError("Only your expense entries can be refunded.")
    amount = minor(amount)
    try:
        refund_date = date.fromisoformat(refund_date) if isinstance(refund_date, str) else refund_date
        valid_date = isinstance(refund_date, date) and 1900 <= refund_date.year <= 9998 and refund_date >= row.date
    except ValueError:
        valid_date = False
    if not valid_date or not isinstance(notes, str) or len(notes) > 500:
        raise ValueError("Use a refund date on or after the expense and notes up to 500 characters.")
    refunded, refunded_base = db.session.execute(db.select(func.coalesce(func.sum(Transaction.amount_minor), 0), func.coalesce(func.sum(Transaction.base_amount_minor), 0)).where(Transaction.user_id == user_id, Transaction.refund_of_id == row.id)).one()
    if amount <= 0 or refunded + amount > row.amount_minor:
        raise ValueError("Refund exceeds the unrefunded expense amount.")
    # Cumulative allocation makes even several small refunds conserve the snapshot.
    base = base_value(row) * (refunded + amount) // row.amount_minor - refunded_base
    refund = Transaction(user_id=user_id, category_id=row.category_id, kind="income", amount_minor=amount, base_amount_minor=base, currency=row.currency, date=refund_date, payment_method=row.payment_method, notes=notes, account_id=row.account_id, refund_of_id=row.id, entry_type="refund", exchange_rate=row.exchange_rate, exchange_rate_date=row.exchange_rate_date, exchange_rate_source=row.exchange_rate_source)
    db.session.add(refund)
    return refund


def filtered_transactions(user_id, args=None):
    """Use explicit whitelists for sort columns; input never becomes SQL text."""
    from .services import owned
    query = db.select(Transaction).join(Category).options(joinedload(Transaction.category), joinedload(Transaction.account)).where(Transaction.user_id == user_id, Transaction.entry_type != "split_parent")
    args = request.args if args is None else args
    if args.get("q"):
        text = args["q"][:100]
        query = query.where(
            or_(
                Transaction.notes.contains(text, autoescape=True),
                Category.name.contains(text, autoescape=True),
            )
        )
    if args.get("account"):
        try:
            account_id = int(args["account"])
        except ValueError:
            abort(400)
        owned(Account, account_id)
        query = query.where(Transaction.account_id == account_id)
    try:
        start = date.fromisoformat(args["start"]) if args.get("start") else None
        end = date.fromisoformat(args["end"]) if args.get("end") else None
        if start and end and start > end:
            raise ValueError()
        if start:
            query = query.where(Transaction.date >= start)
        if end:
            query = query.where(Transaction.date <= end)
        if args.get("category"):
            category_id = int(args["category"])
            owned(Category, category_id)
            query = query.where(Transaction.category_id == category_id)
        if args.get("kind"):
            if args["kind"] not in ("income", "expense"):
                raise ValueError()
            query = query.where(Transaction.kind == args["kind"])
        amounts = {}
        for key in ["min_amount", "max_amount"]:
            if args.get(key):
                amount = Decimal(args[key])
                if (
                    not amount.is_finite()
                    or amount < 0
                    or amount > Decimal("999999999.99")
                    or amount != amount.quantize(Decimal(".01"))
                ):
                    raise ValueError()
                amounts[key] = minor(amount)
        if amounts.get("min_amount", 0) > amounts.get("max_amount", 99999999999):
            raise ValueError()
        if "min_amount" in amounts:
            query = query.where(Transaction.amount_minor >= amounts["min_amount"])
        if "max_amount" in amounts:
            query = query.where(Transaction.amount_minor <= amounts["max_amount"])
    except (ValueError, ArithmeticError):
        abort(400)
    sorting = {
        "date_desc": Transaction.date.desc(),
        "date_asc": Transaction.date.asc(),
        "amount_desc": Transaction.amount_minor.desc(),
        "amount_asc": Transaction.amount_minor.asc(),
        "category": Category.name.asc(),
    }
    if args.get("sort", "date_desc") not in sorting:
        abort(400)
    return query.order_by(sorting[args.get("sort", "date_desc")], Transaction.id.desc())



def csv_safe(value):
    value = str(value)
    if value.startswith("'") or value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def csv_plain(value):
    # Undo only the reversible spreadsheet escaping used by extended exports.
    if value.startswith("'") and csv_safe(value[1:]) == value:
        return value[1:]
    return value


def delete_transaction(row, user_id):
    """Delete a standalone entry while serializing against refunds and splits."""
    _lock_entry(row, user_id)
    assert_editable(row)
    db.session.delete(row)
