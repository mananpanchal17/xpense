# Complete Phase 6–8 components and regression checks

These are complete files, not excerpts. Executable source is in app/, tests/ and tools/. Flask/Jinja/WTForms and native JavaScript are retained.

## app/expenses/routes.py

```python
"""Transactions, categories, budgets, recurring rules, bills, and CSV import."""

import csv
import io
from datetime import date
from decimal import Decimal
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError
from werkzeug.datastructures import MultiDict
from ..ledger import save_transaction as ledger_save, assert_editable, split_transaction, refund_transaction, csv_plain, delete_transaction
from ..extensions import db
from ..models import Transaction, Category, Budget, RecurringExpense, CreditCardBill, Account
from ..forms import (
    TransactionForm,
    CategoryForm,
    BudgetForm,
    RecurringForm,
    CreditCardBillForm,
    ImportForm,
    BaseForm,
    CURRENCIES,
)
from ..services import (
    owned,
    minor,
    category_choices,
    today,
    log_event,
    filtered_transactions,
    budget_status,
    warn_budgets,
    process_recurring,
    account_choices,
    exchange_rate,
)

bp = Blueprint("expenses", __name__, url_prefix="/expenses")
LOCAL_SNAPSHOT_LIMIT = 2000


def form_categories(form):
    # WTForms rejects IDs absent from these user-specific choices.
    form.category_id.choices = category_choices(current_user.id)
    if hasattr(form, "account_id"):
        form.account_id.choices = [(0, "Unassigned")] + account_choices(current_user.id)


def save_transaction(row, form):
    ledger_save(row, form, current_user.id)


@bp.get("/")
@login_required
def index():
    page = db.paginate(filtered_transactions(current_user.id), per_page=20, max_per_page=20)
    return render_template(
        "expenses/index.html",
        title="Transactions",
        page=page,
        categories=category_choices(current_user.id),
        accounts=account_choices(current_user.id, True),
    )


@bp.get("/data")
@login_required
def local_data():
    """A bounded, private in-memory snapshot; partial ledgers use server filtering."""
    rows = db.session.scalars(filtered_transactions(current_user.id, {}).limit(LOCAL_SNAPSHOT_LIMIT + 1)).all()
    if len(rows) > LOCAL_SNAPSHOT_LIMIT:
        return jsonify(complete=False, rows=[], html="")
    html = render_template("expenses/snapshot.html", rows=rows)
    if len(html.encode("utf-8")) > 2 * 1024 * 1024:
        return jsonify(complete=False, rows=[], html="")
    return jsonify(complete=True, today=today().isoformat(), html=html, rows=[{
        "id": row.id, "date": row.date.isoformat(), "category_id": row.category_id,
        "category": row.category.name, "account_id": row.account_id, "kind": row.kind,
        "amount_minor": row.amount_minor, "notes": row.notes,
    } for row in rows])


@bp.route("/add", methods=["GET", "POST"])
@login_required
def add():
    form = TransactionForm()
    form_categories(form)
    if request.method == "GET":
        form.date.data = today()
    if form.validate_on_submit():
        row = Transaction(user_id=current_user.id)
        try:
            save_transaction(row, form)
        except ValueError as error:
            form.account_id.errors.append(str(error))
        else:
            db.session.add(row)
            log_event(current_user.id, "Transaction added")
            db.session.commit()
            if request.accept_mimetypes.best == "application/json":
                return jsonify(transaction={"id": row.id, "amount_minor": row.amount_minor,
                    "currency": row.currency, "kind": row.kind, "date": row.date.isoformat(),
                    "category": row.category.name, "notes": row.notes}, redirect=url_for("expenses.index")), 201
            flash("Transaction added.", "success")
            warn_budgets(current_user.id, row.date)
            return redirect(url_for("expenses.index"))
    if request.method == "POST" and request.accept_mimetypes.best == "application/json":
        return jsonify(errors=form.errors), 422
    return render_template("expenses/form.html", form=form, title="Add transaction")


@bp.route("/<int:record_id>/edit", methods=["GET", "POST"])
@login_required
def edit(record_id):
    row = owned(Transaction, record_id)
    assert_editable(row)
    form = TransactionForm(obj=row)
    form_categories(form)
    if request.method == "GET":
        form.amount.data = Decimal(row.amount_minor) / 100
    if form.validate_on_submit():
        try:
            save_transaction(row, form)
        except ValueError as error:
            form.account_id.errors.append(str(error))
        else:
            log_event(current_user.id, "Transaction edited")
            db.session.commit()
            flash("Transaction updated.", "success")
            warn_budgets(current_user.id, row.date)
            return redirect(url_for("expenses.index"))
    return render_template("expenses/form.html", form=form, title="Edit transaction")


@bp.post("/<int:record_id>/delete")
@login_required
def delete(record_id):
    row = owned(Transaction, record_id)
    if BaseForm().validate_on_submit():
        try:
            delete_transaction(row, current_user.id)
        except ValueError:
            abort(404)
        log_event(current_user.id, "Transaction deleted")
        db.session.commit()
        if request.accept_mimetypes.best == "application/json":
            return jsonify(id=record_id)
        flash("Transaction deleted.", "success")
    return redirect(url_for("expenses.index"))


@bp.route("/categories", methods=["GET", "POST"])
@login_required
def categories():
    form = CategoryForm()
    if form.validate_on_submit():
        db.session.add(Category(user_id=current_user.id, name=form.name.data))
        log_event(current_user.id, "Category added")
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            form.name.errors.append("You already have a category with that name.")
        else:
            flash("Category added.", "success")
            return redirect(url_for("expenses.categories"))
    return render_template(
        "expenses/categories.html",
        title="Categories",
        form=form,
        categories=category_choices(current_user.id),
    )


@bp.route("/categories/<int:record_id>/edit", methods=["GET", "POST"])
@login_required
def edit_category(record_id):
    row = owned(Category, record_id)
    form = CategoryForm(obj=row)
    if form.validate_on_submit():
        row.name = form.name.data
        log_event(current_user.id, "Category renamed")
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            form.name.errors.append("You already have a category with that name.")
        else:
            flash("Category updated.", "success")
            return redirect(url_for("expenses.categories"))
    return render_template("expenses/form.html", form=form, title="Edit category")


@bp.post("/categories/<int:record_id>/delete")
@login_required
def delete_category(record_id):
    row = owned(Category, record_id)
    if BaseForm().validate_on_submit():
        in_use = any(
            db.session.scalar(db.select(model.id).where(model.category_id == row.id).limit(1))
            for model in [Transaction, Budget, RecurringExpense]
        )
        if in_use:
            flash(
                "Category is in use. Reassign or delete its transactions, budgets and recurring rules first.",
                "warning",
            )
        else:
            db.session.delete(row)
            log_event(current_user.id, "Category deleted")
            db.session.commit()
            flash("Category deleted.", "success")
    return redirect(url_for("expenses.categories"))


@bp.route("/budgets", methods=["GET", "POST"])
@login_required
def budgets():
    form = BudgetForm()
    form_categories(form)
    try:
        selected_month = date.fromisoformat(
            request.args.get("month", today().strftime("%Y-%m")) + "-01"
        )
        if not 1900 <= selected_month.year <= 9998:
            raise ValueError()
    except ValueError:
        abort(400)
    if request.method == "GET":
        form.month.data = selected_month.strftime("%Y-%m")
    if form.validate_on_submit():
        month = date.fromisoformat(form.month.data + "-01")
        row = db.session.scalar(
            db.select(Budget).where(
                Budget.user_id == current_user.id,
                Budget.category_id == form.category_id.data,
                Budget.month == month,
            )
        )
        if not row:
            row = Budget(user_id=current_user.id, category_id=form.category_id.data, month=month)
            db.session.add(row)
        row.amount_minor = minor(form.amount.data)
        log_event(current_user.id, "Monthly budget saved")
        db.session.commit()
        flash("Budget saved. Saving the same category and month updates its limit.", "success")
        return redirect(url_for("expenses.budgets", month=form.month.data))
    return render_template(
        "expenses/budgets.html",
        title="Monthly budgets",
        form=form,
        items=budget_status(current_user.id, selected_month),
        month=selected_month,
    )


@bp.post("/budgets/<int:record_id>/delete")
@login_required
def delete_budget(record_id):
    row = owned(Budget, record_id)
    if BaseForm().validate_on_submit():
        db.session.delete(row)
        log_event(current_user.id, "Budget deleted")
        db.session.commit()
        flash("Budget deleted.", "success")
    return redirect(url_for("expenses.budgets"))


@bp.route("/bills", methods=["GET", "POST"])
@login_required
def bills():
    form = CreditCardBillForm()
    if request.method == "GET":
        form.due_date.data = today()
    if form.validate_on_submit():
        row = CreditCardBill(user_id=current_user.id)
        save_bill(row, form)
        db.session.add(row)
        log_event(current_user.id, "Credit card bill added")
        db.session.commit()
        flash("Credit card bill saved.", "success")
        return redirect(url_for("expenses.bills"))
    items = db.session.scalars(
        db.select(CreditCardBill)
        .where(CreditCardBill.user_id == current_user.id)
        .order_by(CreditCardBill.paid, CreditCardBill.due_date)
    )
    return render_template("expenses/bills.html", title="Credit card bills", form=form, bills=items)


def save_bill(row, form):
    row.card_name = form.card_name.data
    row.amount_minor = minor(form.amount.data)
    row.due_date = form.due_date.data
    row.notes = form.notes.data or ""


@bp.route("/bills/<int:record_id>/edit", methods=["GET", "POST"])
@login_required
def edit_bill(record_id):
    row = owned(CreditCardBill, record_id)
    form = CreditCardBillForm(obj=row)
    if request.method == "GET":
        form.amount.data = Decimal(row.amount_minor) / 100
    if form.validate_on_submit():
        save_bill(row, form)
        log_event(current_user.id, "Credit card bill updated")
        db.session.commit()
        flash("Credit card bill updated.", "success")
        return redirect(url_for("expenses.bills"))
    return render_template("expenses/form.html", form=form, title="Edit credit card bill")


@bp.post("/bills/<int:record_id>/paid")
@login_required
def mark_bill_paid(record_id):
    row = owned(CreditCardBill, record_id)
    if BaseForm().validate_on_submit():
        row.paid = True
        log_event(current_user.id, "Credit card bill marked paid")
        db.session.commit()
        flash("Credit card bill marked as paid.", "success")
    return redirect(url_for("expenses.bills"))


@bp.post("/bills/<int:record_id>/delete")
@login_required
def delete_bill(record_id):
    row = owned(CreditCardBill, record_id)
    if BaseForm().validate_on_submit():
        db.session.delete(row)
        log_event(current_user.id, "Credit card bill deleted")
        db.session.commit()
        flash("Credit card bill deleted.", "success")
    return redirect(url_for("expenses.bills"))


@bp.route("/recurring", methods=["GET", "POST"])
@login_required
def recurring():
    form = RecurringForm()
    form_categories(form)
    if request.method == "GET":
        form.next_due.data = today()
    if form.validate_on_submit():
        row = RecurringExpense(user_id=current_user.id)
        save_rule(row, form)
        db.session.add(row)
        log_event(current_user.id, "Recurring expense added")
        db.session.commit()
        flash(
            "Recurring rule saved. Use Generate due expenses, or schedule the CLI command.",
            "success",
        )
        return redirect(url_for("expenses.recurring"))
    rules = db.session.scalars(
        db.select(RecurringExpense)
        .where(RecurringExpense.user_id == current_user.id)
        .order_by(RecurringExpense.next_due)
    ).all()
    return render_template(
        "expenses/recurring.html", title="Recurring expenses", form=form, rules=rules
    )


def save_rule(row, form):
    row.category_id = form.category_id.data
    row.amount_minor = minor(form.amount.data)
    if row.next_due != form.next_due.data:
        row.anchor_day = form.next_due.data.day
    row.next_due = form.next_due.data
    row.frequency = form.frequency.data
    row.payment_method = form.payment_method.data
    row.notes = form.notes.data or ""
    row.active = form.active.data
    row.status = "active" if row.active else "paused"
    row.account_id = form.account_id.data or None


@bp.route("/recurring/<int:record_id>/edit", methods=["GET", "POST"])
@login_required
def edit_recurring(record_id):
    row = owned(RecurringExpense, record_id)
    form = RecurringForm(obj=row)
    form_categories(form)
    if request.method == "GET":
        form.amount.data = Decimal(row.amount_minor) / 100
    if form.validate_on_submit():
        # Rewinding a rule can regenerate deleted history, so require a new rule instead.
        if form.next_due.data < row.next_due:
            form.next_due.errors.append(
                "The next date cannot move backwards. Create a new rule for historical entries."
            )
        else:
            save_rule(row, form)
            log_event(current_user.id, "Recurring expense updated")
            db.session.commit()
            flash("Recurring rule updated. Existing transactions are unchanged.", "success")
            return redirect(url_for("expenses.recurring"))
    return render_template("expenses/form.html", form=form, title="Edit recurring expense")


@bp.post("/recurring/<int:record_id>/delete")
@login_required
def delete_recurring(record_id):
    row = owned(RecurringExpense, record_id)
    if BaseForm().validate_on_submit():
        db.session.execute(
            db.update(Transaction)
            .where(Transaction.user_id == current_user.id, Transaction.recurring_id == row.id)
            .values(recurring_id=None)
        )
        db.session.delete(row)
        log_event(current_user.id, "Recurring rule deleted (transactions retained)")
        db.session.commit()
        flash("Rule deleted. Previously generated transactions were kept.", "success")
    return redirect(url_for("expenses.recurring"))


@bp.post("/recurring/<int:record_id>/cancel")
@login_required
def cancel_recurring(record_id):
    row = owned(RecurringExpense, record_id)
    if BaseForm().validate_on_submit():
        row.active = False
        row.status = "cancelled"
        log_event(current_user.id, "Recurring expense cancelled")
        db.session.commit()
        flash("Recurring rule cancelled. Existing transactions were kept.", "success")
    return redirect(url_for("expenses.recurring"))


@bp.post("/recurring/generate")
@login_required
def generate_recurring():
    if BaseForm().validate_on_submit():
        count = process_recurring(current_user.id)
        flash(f"Generated {count} due transaction(s).", "success")
        warn_budgets(current_user.id, today())
    return redirect(url_for("expenses.recurring"))


@bp.route("/import", methods=["GET", "POST"])
@login_required
def import_csv():
    form = ImportForm()
    if form.validate_on_submit():
        try:
            text = form.file.data.read().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
            required = {"kind", "amount", "category", "date", "payment_method", "notes"}
            if (
                not reader.fieldnames
                or not required.issubset(set(reader.fieldnames))
                or set(reader.fieldnames) - required - {"currency", "base_amount_minor", "exchange_rate", "exchange_rate_date", "exchange_rate_source", "entry_type"}
                or len(reader.fieldnames) != len(set(reader.fieldnames))
            ):
                raise ValueError(
                    "Use exactly these CSV headers: kind,amount,category,date,payment_method,notes."
                )
            choices = category_choices(current_user.id)
            by_name = {name.casefold(): cid for cid, name in choices}
            pending = []
            for line, data in enumerate(reader, 2):
                if line > 2001:
                    raise ValueError("Import at most 2,000 rows at a time.")
                if None in data or any(value is None for value in data.values()):
                    raise ValueError(f"Row {line}: wrong number of columns.")
                if "base_amount_minor" in reader.fieldnames:
                    for field in ("category", "notes", "exchange_rate_source"):
                        if field in data:
                            data[field] = csv_plain(data[field])
                cid = by_name.get(data["category"].strip().casefold())
                if cid is None:
                    raise ValueError(
                        f"Row {line}: unknown category. Create that category before importing."
                    )
                data["category_id"] = str(cid)
                row_form = TransactionForm(MultiDict(data), meta={"csrf": False})
                row_form.category_id.choices = choices
                row_form.account_id.choices = [(0, "Unassigned")] + account_choices(current_user.id)
                if not row_form.validate():
                    errors = "; ".join(
                        f'{key}: {", ".join(values)}' for key, values in row_form.errors.items()
                    )
                    raise ValueError(f"Row {line}: {errors}")
                row = Transaction(user_id=current_user.id)
                save_transaction(row, row_form)
                if data.get("entry_type", "normal") != "normal":
                    raise ValueError(f"Row {line}: linked transfers, refunds and adjustments must not be imported independently.")
                if data.get("currency"):
                    currency = data["currency"].strip().upper()
                    if currency not in dict(CURRENCIES):
                        raise ValueError(f"Row {line}: unsupported currency.")
                    snapshot = data.get("base_amount_minor", "")
                    if not snapshot and currency != "INR":
                        raise ValueError(f"Row {line}: foreign currency needs an INR snapshot.")
                    try:
                        base = int(snapshot) if snapshot else row.amount_minor
                        if not 0 <= base <= 99999999999:
                            raise ValueError()
                        rate = Decimal(data.get("exchange_rate") or (Decimal(base) / row.amount_minor))
                        if not rate.is_finite() or rate <= 0 or rate > Decimal("9999999999"):
                            raise ValueError()
                        rate_date = date.fromisoformat(data["exchange_rate_date"]) if data.get("exchange_rate_date") else row.date
                        source = data.get("exchange_rate_source") or "CSV snapshot"
                        if len(source) > 80:
                            raise ValueError()
                    except (ValueError, ArithmeticError):
                        raise ValueError(f"Row {line}: invalid INR snapshot or exchange rate.") from None
                    row.currency, row.base_amount_minor = currency, base
                    row.exchange_rate, row.exchange_rate_date, row.exchange_rate_source = rate, rate_date, source
                pending.append(row)
            if not pending:
                raise ValueError("The CSV has no transaction rows.")
            # Only write after EVERY row validates: all-or-nothing import.
            db.session.add_all(pending)
            log_event(current_user.id, f"Imported {len(pending)} transactions from CSV")
            db.session.commit()
            flash(f"Imported {len(pending)} transactions.", "success")
            warn_budgets(current_user.id, today())
            return redirect(url_for("expenses.index"))
        except (UnicodeDecodeError, csv.Error, ValueError) as error:
            db.session.rollback()
            form.file.errors.append(
                str(error)
                if not isinstance(error, UnicodeDecodeError)
                else "Save the CSV as UTF-8."
            )
    return render_template("expenses/import.html", form=form, title="Import CSV")


@bp.post("/<int:record_id>/split")
@login_required
def split(record_id):
    from flask import jsonify
    row = owned(Transaction, record_id)
    if not BaseForm().validate_on_submit():
        return jsonify(error="Invalid request token."), 400
    try:
        payload = request.get_json(silent=True) or {}
        children = split_transaction(row, payload.get("parts"), current_user.id)
        db.session.flush()
        ids = [child.id for child in children]
        log_event(current_user.id, "Expense split")
        db.session.commit()
        return jsonify(ids=ids), 201
    except ValueError as error:
        db.session.rollback()
        return jsonify(error=str(error)), 400


@bp.post("/<int:record_id>/refund")
@login_required
def refund(record_id):
    from flask import jsonify
    row = owned(Transaction, record_id)
    if not BaseForm().validate_on_submit():
        return jsonify(error="Invalid request token."), 400
    try:
        payload = request.get_json(silent=True) if request.is_json else request.form
        payload = payload or {}
        result = refund_transaction(row, payload.get("amount"), payload.get("date"), payload.get("notes", ""), current_user.id)
        db.session.flush()
        rid = result.id
        log_event(current_user.id, "Expense refunded")
        db.session.commit()
        return jsonify(id=rid), 201
    except ValueError as error:
        db.session.rollback()
        return jsonify(error=str(error)), 400


@bp.post("/sample-data")
@login_required
def sample_data():
    if not BaseForm().validate_on_submit():
        abort(400)
    from ..sample_data import load_sample_data
    try:
        load_sample_data(current_user.id, today())
        log_event(current_user.id, "Loaded sample ledger data")
        db.session.commit()
    except ValueError as error:
        db.session.rollback()
        abort(409, description=str(error))
    flash("Sample transactions loaded. You can edit or delete these examples.", "success")
    return redirect(url_for("expenses.index"))
```

## app/services.py

```python
"""Small reusable functions keep the route handlers easy to read."""

import calendar
import secrets
import smtplib
import json
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal
from email.message import EmailMessage
from zoneinfo import ZoneInfo
from flask import current_app, abort, request
from flask_login import current_user
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import func, or_, case
from .ledger import minor, filtered_transactions, budget_status, period_totals, reporting_amount, reporting_entries, report_kind, base_value, signed_value
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload
from .extensions import db
from .models import (
    User,
    Category,
    Transaction,
    Budget,
    RecurringExpense,
    CreditCardBill,
    SavingsGoal,
    GoalContribution,
    Notification,
    RecurringOccurrence,
    Commitment,
    CommitmentOccurrence,
    ActivityLog,
    Account,
    Transfer,
    ExchangeRate,
    Investment,
    utcnow,
)


def today():
    return datetime.now(ZoneInfo(current_app.config["APP_TIMEZONE"])).date()


def account_choices(user_id, include_archived=False):
    query = db.select(Account).where(Account.user_id == user_id)
    if not include_archived:
        query = query.where(Account.active.is_(True))
    return [(account.id, account.name) for account in db.session.scalars(query.order_by(Account.name))]


def account_balance(account):
    delta = db.session.scalar(db.select(func.coalesce(func.sum(case((Transaction.kind == "income", Transaction.amount_minor), else_=-Transaction.amount_minor)), 0)).where(Transaction.account_id == account.id, Transaction.user_id == account.user_id, Transaction.entry_type != "split_parent"))
    return account.opening_balance_minor + delta


def account_summary(user_id):
    accounts = db.session.scalars(db.select(Account).where(Account.user_id == user_id).order_by(Account.active.desc(), Account.name)).all()
    aggregates = {aid: (delta, last_date) for aid, delta, last_date in db.session.execute(db.select(Transaction.account_id, func.sum(case((Transaction.kind == "income", Transaction.amount_minor), else_=-Transaction.amount_minor)), func.max(Transaction.date)).where(Transaction.user_id == user_id, Transaction.entry_type != "split_parent").group_by(Transaction.account_id))}
    cached_rates = db.session.scalars(db.select(ExchangeRate).where(or_(ExchangeRate.quote_currency == "INR", ExchangeRate.base_currency == "INR"))).all()
    rates = {row.quote_currency: Decimal(1) / Decimal(str(row.rate)) for row in cached_rates if row.base_currency == "INR" and row.rate > 0}
    rates.update({row.base_currency: Decimal(str(row.rate)) for row in cached_rates if row.quote_currency == "INR" and row.rate > 0})
    result = []
    for account in accounts:
        delta, last_date = aggregates.get(account.id, (0, None))
        balance = account.opening_balance_minor + delta
        rate = Decimal(1) if account.currency == "INR" else rates.get(account.currency)
        result.append({"account": account, "balance_minor": balance, "converted_minor": int((Decimal(balance) * rate).quantize(Decimal("1"))) if rate is not None else None, "last_activity": last_date})
    return result


def _has_cached_rate(base, quote):
    return db.session.scalar(
        db.select(ExchangeRate.id).where(
            ExchangeRate.base_currency == base, ExchangeRate.quote_currency == quote
        )
    ) is not None


def exchange_rate(base, quote):
    if base == quote:
        return Decimal("1"), "same currency", today()
    row = db.session.scalar(
        db.select(ExchangeRate).where(
            ExchangeRate.base_currency == base, ExchangeRate.quote_currency == quote
        )
    )
    if row and row.rate > 0:
        return Decimal(str(row.rate)), row.provider, row.fetched_at.date()
    inverse = db.session.scalar(db.select(ExchangeRate).where(ExchangeRate.base_currency == quote, ExchangeRate.quote_currency == base))
    if inverse and inverse.rate > 0:
        return Decimal(1) / Decimal(str(inverse.rate)), inverse.provider + " (inverse)", inverse.fetched_at.date()
    raise ValueError(f"No cached exchange rate for {base}/{quote}. Refresh rates first.")


def investment_summary(user_id):
    """Native holdings with explicitly cached INR portfolio estimates."""
    query = db.select(Investment).where(Investment.user_id == user_id)
    if request.args.get("type"):
        query = query.where(Investment.investment_type == request.args["type"])
    if request.args.get("status"):
        query = query.where(Investment.status == request.args["status"])
    rows = db.session.scalars(query.order_by(Investment.name)).all()
    items = []
    totals = dict.fromkeys(("invested", "value", "gain", "interest", "dividends", "unconverted_count"), 0)
    totals["allocation"] = {}
    rates = {}
    for row in rows:
        gain = row.current_value_minor - row.amount_invested_minor
        item = {"investment": row, "gain_minor": gain,
                "return_percent": Decimal(gain) * 100 / row.amount_invested_minor}
        try:
            if row.currency not in rates:
                rates[row.currency] = exchange_rate(row.currency, "INR")[0]
            rate = rates[row.currency]
            converted = {key: int((Decimal(value) * rate).quantize(Decimal("1"))) for key, value in {
                "invested": row.amount_invested_minor, "value": row.current_value_minor,
                "interest": row.interest_earned_minor, "dividends": row.dividend_income_minor}.items()}
            converted["gain"] = converted["value"] - converted["invested"]
            for key, value in converted.items():
                totals[key] += value
            totals["allocation"][row.investment_type] = totals["allocation"].get(row.investment_type, 0) + converted["value"]
            item["inr_value"] = converted["value"]
            item["inr_invested"] = converted["invested"]
            item["inr_gain"] = converted["gain"]
        except ValueError:
            totals["unconverted_count"] += 1
            item.update(inr_value=None, inr_invested=None, inr_gain=None)
        items.append(item)
    sort = request.args.get("sort", "name")
    key_map = {
        "value": lambda item: item["inr_value"] if item["inr_value"] is not None else -1,
        "invested": lambda item: item["inr_invested"] if item["inr_invested"] is not None else -1,
        "gain": lambda item: item["inr_gain"] if item["inr_gain"] is not None else -10**30,
        "return": lambda item: item["return_percent"],
        "maturity": lambda item: item["investment"].maturity_date or date.max,
        "name": lambda item: item["investment"].name.casefold(),
    }
    if sort in key_map:
        items.sort(key=key_map[sort], reverse=sort not in ("name", "maturity"))
    totals["return_percent"] = Decimal(totals["gain"]) * 100 / totals["invested"] if totals["invested"] else 0
    totals["upcoming_maturities"] = sorted([
        item["investment"] for item in items
        if item["investment"].status == "active" and item["investment"].maturity_date
        and item["investment"].maturity_date >= today()
    ], key=lambda row: row.maturity_date)[:5]
    return {"items": items, "totals": totals}


def refresh_exchange_rates(base, quotes):
    """Fetch and cache rates; callers can retain the previous cache on failure."""
    quotes = sorted({quote for quote in quotes if quote != base})
    if not quotes:
        return 0
    params = urllib.parse.urlencode({"from": base, "to": ",".join(quotes)})
    url = current_app.config["EXCHANGE_RATE_API_URL"] + "?" + params
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if current_app.config["EXCHANGE_RATE_API_KEY"]:
        request.add_header("Authorization", f"Bearer {current_app.config['EXCHANGE_RATE_API_KEY']}")
    with urllib.request.urlopen(request, timeout=current_app.config["EXCHANGE_RATE_TIMEOUT"]) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rates = payload.get("rates")
    if not isinstance(rates, dict):
        raise ValueError("Exchange-rate provider returned an invalid response.")
    count = 0
    for quote in quotes:
        value = rates.get(quote)
        try:
            rate = Decimal(str(value))
        except (TypeError, ValueError):
            raise ValueError(f"Exchange-rate provider omitted {quote}.")
        if not rate.is_finite() or rate <= 0:
            raise ValueError(f"Exchange-rate provider returned an invalid {quote} rate.")
        row = db.session.scalar(
            db.select(ExchangeRate).where(
                ExchangeRate.base_currency == base, ExchangeRate.quote_currency == quote
            )
        )
        if row is None:
            row = ExchangeRate(base_currency=base, quote_currency=quote)
            db.session.add(row)
        row.rate = rate
        row.provider = "Frankfurter" if "frankfurter" in current_app.config["EXCHANGE_RATE_API_URL"] else "Configured provider"
        row.fetched_at = utcnow()
        row.user_provided = False
        count += 1
    db.session.commit()
    return count


def calculate_emi(principal_minor, annual_rate, months):
    """Return a reducing-balance EMI in minor units using Decimal arithmetic."""
    principal = Decimal(principal_minor)
    months = int(months)
    if principal <= 0 or months <= 0:
        raise ValueError("Principal and tenure must be positive.")
    rate = Decimal(str(annual_rate or 0)) / Decimal("1200")
    if rate == 0:
        return int((principal / months).quantize(Decimal("1")))
    factor = (Decimal("1") + rate) ** months
    payment = principal * rate * factor / (factor - Decimal("1"))
    return int(payment.quantize(Decimal("1")))


def amortization_schedule(principal_minor, annual_rate, months, payment_minor=None):
    """Build a transparent reducing-balance schedule with final-payment rounding."""
    payment = payment_minor or calculate_emi(principal_minor, annual_rate, months)
    balance = Decimal(principal_minor)
    rate = Decimal(str(annual_rate or 0)) / Decimal("1200")
    schedule = []
    for month in range(1, int(months) + 1):
        interest = (balance * rate).quantize(Decimal("1"))
        principal = min(balance, Decimal(payment) - interest)
        if principal < 0:
            raise ValueError("Payment does not cover interest.")
        total = principal + interest
        balance -= principal
        schedule.append(
            {
                "month": month,
                "payment_minor": int(total),
                "principal_minor": int(principal),
                "interest_minor": int(interest),
                "balance_minor": max(0, int(balance)),
            }
        )
        if balance <= 0:
            break
    return schedule


def prepayment_simulation(principal_minor, annual_rate, months, payment_minor, extra_minor):
    """Compare tenure reduction with EMI reduction without changing stored loan data."""
    if extra_minor < 0:
        raise ValueError("Extra payment cannot be negative.")
    balance = max(0, int(principal_minor) - int(extra_minor))
    current = amortization_schedule(principal_minor, annual_rate, months, payment_minor)
    reduced_tenure = amortization_schedule(balance, annual_rate, months, payment_minor)
    reduced_emi = calculate_emi(balance, annual_rate, months)
    reduced_emi_schedule = amortization_schedule(balance, annual_rate, months, reduced_emi)
    return {
        "reduce_tenure": {
            "months": len(reduced_tenure),
            "months_saved": max(0, len(current) - len(reduced_tenure)),
            "interest_saved_minor": max(
                0,
                sum(item["interest_minor"] for item in current)
                - sum(item["interest_minor"] for item in reduced_tenure),
            ),
        },
        "reduce_emi": {
            "emi_minor": reduced_emi,
            "months": len(reduced_emi_schedule),
            "interest_saved_minor": max(
                0,
                sum(item["interest_minor"] for item in current)
                - sum(item["interest_minor"] for item in reduced_emi_schedule),
            ),
        },
    }


def commitment_occurrences(commitment, start, end):
    """Return stored/generated occurrences for a commitment date window."""
    rows = db.session.scalars(
        db.select(CommitmentOccurrence)
        .where(
            CommitmentOccurrence.commitment_id == commitment.id,
            CommitmentOccurrence.due_date >= start,
            CommitmentOccurrence.due_date < end,
        )
        .order_by(CommitmentOccurrence.due_date)
    ).all()
    due = commitment.next_due
    anchor_day = due.day
    while due < end and (commitment.end_date is None or due <= commitment.end_date):
        if due >= start and not any(item.due_date == due for item in rows):
            item = CommitmentOccurrence(
                commitment_id=commitment.id,
                due_date=due,
                expected_minor=commitment.amount_minor,
            )
            db.session.add(item)
            rows.append(item)
        if commitment.frequency == "once":
            break
        if commitment.frequency == "weekly":
            due += timedelta(days=7)
        else:
            step = 12 if commitment.frequency == "yearly" else 3 if commitment.frequency == "quarterly" else 1
            next_month = due.year * 12 + due.month - 1 + step
            year, month_index = divmod(next_month, 12)
            if year > 9998:
                break
            due = date(year, month_index + 1, min(anchor_day, calendar.monthrange(year, month_index + 1)[1]))
    return rows


def commitment_summary(user_id, reference=None):
    """INR estimates from cached rates; occurrence amounts stay native."""
    reference = reference or today()
    month = reference.replace(day=1)
    end = month_end(month)
    commitments = db.session.scalars(
        db.select(Commitment).where(
            Commitment.user_id == user_id, Commitment.status != "cancelled"
        )
    ).all()
    rates = {"INR": Decimal("1")}
    commitment_rates = {}
    for commitment in commitments:
        currency = commitment.account.currency if commitment.account else "INR"
        if currency not in rates:
            try:
                rates[currency] = exchange_rate(currency, "INR")[0]
            except ValueError:
                rates[currency] = None
        commitment_rates[commitment.id] = rates[currency]

    def inr(amount, commitment_id):
        rate = commitment_rates[commitment_id]
        return int((Decimal(amount) * rate).quantize(Decimal("1"))) if rate is not None else 0

    occurrences = []
    for commitment in commitments:
        occurrences.extend(commitment_occurrences(commitment, month, end))
    db.session.commit()
    overdue = [
        item
        for item in occurrences
        if item.due_date < reference and item.status not in ("paid", "skipped")
    ]
    upcoming = [
        item
        for item in occurrences
        if item.due_date >= reference and item.status not in ("paid", "skipped")
    ]
    paid = [item for item in occurrences if item.status == "paid"]
    return {
        "commitments": commitments,
        "occurrences": occurrences,
        "currency": "INR",
        "unconverted_count": sum(rate is None for rate in commitment_rates.values()),
        "due_month": sum(inr(item.expected_minor, item.commitment_id) for item in occurrences),
        "paid_month": sum(inr(item.actual_minor, item.commitment_id) for item in paid),
        "remaining": sum(inr(item.expected_minor, item.commitment_id) for item in upcoming),
        "overdue": sum(inr(item.expected_minor, item.commitment_id) for item in overdue),
        "upcoming": upcoming,
        "overdue_items": overdue,
        "next": min(upcoming, key=lambda item: item.due_date) if upcoming else None,
        "monthly_fixed": sum(
            inr(item.amount_minor, item.id) for item in commitments if item.frequency == "monthly"
        ),
        "annual_recurring": sum(
            inr(item.amount_minor
            * (52 if item.frequency == "weekly" else 4 if item.frequency == "quarterly" else 1 if item.frequency == "yearly" else 12), item.id)
            for item in commitments
            if item.frequency != "once"
        ),
    }


def log_event(user_id, event):
    db.session.add(ActivityLog(user_id=user_id, event=event))


def owned(model, record_id):
    """Never fetch a private record using its ID alone."""
    row = db.session.scalar(
        db.select(model).where(model.id == record_id, model.user_id == current_user.id)
    )
    if row is None:
        abort(404)
    return row


def category_choices(user_id):
    return [
        (c.id, c.name)
        for c in db.session.scalars(
            db.select(Category).where(Category.user_id == user_id).order_by(Category.name)
        )
    ]


def month_end(start):
    return date(start.year + (start.month == 12), start.month % 12 + 1, 1)


def warn_budgets(user_id, month):
    from flask import flash

    for item in budget_status(user_id, month.replace(day=1)):
        if item["percent"] >= 80:
            flash(
                f"{item['budget'].category.name}: {item['percent']:.0f}% of the monthly budget used.",
                item["level"],
            )


def _previous_month(month):
    return date(month.year - (month.month == 1), 12 if month.month == 1 else month.month - 1, 1)


def financial_insights(user_id, reference=None):
    """Return explainable, data-backed insight dictionaries for the dashboard."""
    reference = reference or today()
    month = reference.replace(day=1)
    previous = _previous_month(month)
    month_data = period_totals(user_id, month, month_end(month))
    previous_data = period_totals(user_id, previous, month)
    insights = []
    if previous_data["expense"] and month_data["expense"] != previous_data["expense"]:
        change = (month_data["expense"] - previous_data["expense"]) / previous_data["expense"] * 100
        direction = "increased" if change > 0 else "decreased"
        insights.append(
            {
                "kind": "trend",
                "tone": "warning" if change > 0 else "success",
                "title": f"Spending {direction} {abs(change):.0f}%",
                "message": f"This month: {month_data['expense'] / 100:,.2f}; last month: {previous_data['expense'] / 100:,.2f}.",
                "why": "We compare recorded expense transactions in the current and previous calendar month.",
            }
        )
    category_rows = db.session.execute(
        db.select(Category.name, func.sum(reporting_amount()))
        .join(Transaction, Transaction.category_id == Category.id)
        .where(
            Transaction.user_id == user_id,
            report_kind() == "expense",
            reporting_entries(),
            Transaction.date >= month,
            Transaction.date < month_end(month),
        )
        .group_by(Category.id, Category.name)
        .order_by(func.sum(reporting_amount()).desc())
    ).all()
    if category_rows:
        top_name, top_amount = category_rows[0]
        insights.append(
            {
                "kind": "category",
                "tone": "info",
                "title": f"{top_name} is your top category",
                "message": f"{top_amount / 100:,.2f} recorded this month.",
                "why": "This is the category with the largest sum of expense transactions this month.",
            }
        )
        largest_increase = None
        for name, amount in category_rows:
            previous_amount = db.session.scalar(
                db.select(func.sum(reporting_amount()))
                .join(Category, Transaction.category_id == Category.id)
                .where(
                    Transaction.user_id == user_id,
                    Category.name == name,
                    report_kind() == "expense",
                    reporting_entries(),
                    Transaction.date >= previous,
                    Transaction.date < month,
                )
            ) or 0
            increase = amount - previous_amount
            if increase > 0 and (largest_increase is None or increase > largest_increase[1]):
                largest_increase = (name, increase)
        if largest_increase:
            insights.append(
                {
                    "kind": "category_change",
                    "tone": "warning",
                    "title": f"{largest_increase[0]} changed most",
                    "message": f"It is up {largest_increase[1] / 100:,.2f} compared with last month.",
                    "why": "We compare each category's current-month expense total with the previous month.",
                }
            )
    history_start = month
    for _ in range(5):
        history_start = _previous_month(history_start)
    history = period_totals(user_id, history_start, month)
    month_count = max(1, (month.year - history_start.year) * 12 + month.month - history_start.month)
    average = history["expense"] / month_count
    if average and month_data["expense"] > average * 1.15:
        insights.append(
            {
                "kind": "average",
                "tone": "warning",
                "title": "Spending is above your six-month average",
                "message": f"Current month is {month_data['expense'] / average:.1f}× your recent average.",
                "why": "The comparison uses expense transactions from the six completed calendar months before this month.",
            }
        )
    for item in budget_status(user_id, month):
        if item["percent"] >= 80:
            insights.append(
                {
                    "kind": "budget",
                    "tone": "danger" if item["percent"] >= 100 else "warning",
                    "title": f"{item['budget'].category.name} budget needs attention",
                    "message": f"{item['percent']:.0f}% used this month.",
                    "why": "Budget progress is based on expense transactions in this calendar month.",
                }
            )
    unusually_high = db.session.scalar(
        db.select(Transaction)
        .where(
            Transaction.user_id == user_id,
            report_kind() == "expense",
            reporting_entries(),
            Transaction.date >= history_start,
            Transaction.date < month_end(month),
        )
        .order_by(reporting_amount().desc())
        .limit(1)
    )
    if unusually_high and average and signed_value(unusually_high) > average * 0.6:
        insights.append(
            {
                "kind": "unusual",
                "tone": "info",
                "title": "A high-value transaction stands out",
                "message": f"{signed_value(unusually_high) / 100:,.2f} on {unusually_high.date}.",
                "why": "This is the largest recent expense and is large relative to your average monthly spending.",
            }
        )
    return insights[:6]


def financial_forecast(user_id, reference=None):
    """Estimate month-end values using daily pace, three-month average, and recurring rules."""
    reference = reference or today()
    month = reference.replace(day=1)
    end = month_end(month)
    days_elapsed = max(1, reference.day)
    days_in_month = (end - month).days
    current = period_totals(user_id, month, end)
    daily_expense = current["expense"] / days_elapsed
    recent_start = month
    for _ in range(3):
        recent_start = _previous_month(recent_start)
    recent = period_totals(user_id, recent_start, month)
    completed_months = max(1, (month.year - recent_start.year) * 12 + month.month - recent_start.month)
    average_expense = recent["expense"] / completed_months
    average_income = recent["income"] / completed_months
    projected_expense = max(current["expense"] + daily_expense * (days_in_month - days_elapsed), average_expense)
    projected_income = max(current["income"], average_income)
    recurring_total = 0
    unconverted_recurring_count = 0
    rules = db.session.scalars(db.select(RecurringExpense).options(joinedload(RecurringExpense.account)).where(RecurringExpense.user_id == user_id, RecurringExpense.active.is_(True), RecurringExpense.next_due < end, RecurringExpense.next_due >= reference))
    for rule in rules:
        currency = rule.account.currency if rule.account else "INR"
        try:
            rate, _, _ = exchange_rate(currency, "INR")
        except ValueError:
            unconverted_recurring_count += 1
            continue
        recurring_total += int((Decimal(rule.amount_minor) * rate).quantize(Decimal("1")))
    projected_expense += recurring_total
    return {
        "month": month,
        "current_expense": current["expense"],
        "current_income": current["income"],
        "expected_expense": int(projected_expense),
        "expected_income": int(projected_income),
        "closing_balance": int(projected_income - projected_expense),
        "recurring_total": recurring_total,
        "unconverted_recurring_count": unconverted_recurring_count,
        "method": "Current daily pace, three-month average, and known active recurring payments.",
    }


def goal_metrics(goal, reference=None):
    reference = reference or today()
    remaining = max(0, goal.target_minor - goal.saved_minor)
    days = (goal.target_date - reference).days
    months = max(1, (goal.target_date.year - reference.year) * 12 + goal.target_date.month - reference.month)
    elapsed = max(1, (reference - goal.created_at.date()).days) if goal.created_at else 1
    rate = goal.saved_minor / elapsed
    predicted = reference + timedelta(days=int(remaining / rate)) if rate > 0 and remaining else None
    expected_progress = 1 - max(0, days) / max(1, (goal.target_date - goal.created_at.date()).days)
    progress = min(1, goal.saved_minor / goal.target_minor)
    return {
        "progress": progress * 100,
        "remaining": remaining,
        "days_remaining": days,
        "monthly_required": int(remaining / months),
        "predicted_date": predicted,
        "on_track": progress >= expected_progress,
    }


def financial_health(user_id, reference=None):
    reference = reference or today()
    month = reference.replace(day=1)
    current = period_totals(user_id, month, month_end(month))
    savings_rate = (
        max(0, current["income"] - current["expense"]) / current["income"]
        if current["income"]
        else 0
    )
    budgets = budget_status(user_id, month)
    budget_adherence = (
        sum(1 for item in budgets if item["percent"] <= 100) / len(budgets) if budgets else 0.5
    )
    ratio = min(1, current["income"] / current["expense"]) if current["expense"] else 1
    recent_expenses = []
    cursor = month
    for _ in range(3):
        previous = _previous_month(cursor)
        recent_expenses.append(period_totals(user_id, previous, cursor)["expense"])
        cursor = previous
    stability = 1
    if recent_expenses and max(recent_expenses):
        stability = max(0, 1 - (max(recent_expenses) - min(recent_expenses)) / max(recent_expenses))
    overdue = db.session.scalar(
        db.select(func.count(CreditCardBill.id)).where(
            CreditCardBill.user_id == user_id,
            CreditCardBill.paid.is_(False),
            CreditCardBill.due_date < reference,
        )
    ) or 0
    goals = db.session.scalars(
        db.select(SavingsGoal).where(SavingsGoal.user_id == user_id, SavingsGoal.status == "active")
    ).all()
    goal_progress = sum(goal_metrics(goal, reference)["progress"] for goal in goals) / len(goals) / 100 if goals else 0.5
    factors = {
        "Savings rate": round(savings_rate * 100),
        "Budget adherence": round(budget_adherence * 100),
        "Income-to-expense ratio": round(ratio * 100),
        "Spending stability": round(stability * 100),
        "Bills": max(0, 100 - overdue * 25),
        "Savings goals": round(goal_progress * 100),
    }
    score = max(0, min(100, round(sum(factors.values()) / len(factors))))
    status = "Excellent" if score >= 80 else "Good" if score >= 60 else "Needs Attention" if score >= 35 else "Critical"
    return {"score": score, "status": status, "factors": factors, "explanation": "An even-weighted score across savings, budgets, income coverage, stability, bills, and goal progress.", "suggestion": "Review the lowest factor first; the score is a planning aid, not a financial judgment."}


def create_notification(user_id, kind, title, message, dedupe_key):
    try:
        notification = Notification(
            user_id=user_id,
            kind=kind,
            title=title,
            message=message,
            dedupe_key=dedupe_key,
        )
        db.session.add(notification)
        db.session.commit()
        return notification
    except IntegrityError:
        db.session.rollback()
        return None


def generate_notifications(user_id, reference=None):
    reference = reference or today()
    created = 0
    month = reference.replace(day=1)
    for item in budget_status(user_id, month):
        if item["percent"] >= 100:
            notification = create_notification(user_id, "budget_exceeded", f"{item['budget'].category.name} budget exceeded", "Review this month's spending and budget.", f"budget-exceeded:{item['budget'].id}:{month}")
        elif item["percent"] >= 80:
            notification = create_notification(user_id, "budget_warning", f"{item['budget'].category.name} budget at 80%", "You are approaching this month's spending limit.", f"budget-warning:{item['budget'].id}:{month}")
        else:
            notification = None
        created += bool(notification)
    for bill in db.session.scalars(db.select(CreditCardBill).where(CreditCardBill.user_id == user_id, CreditCardBill.paid.is_(False))).all():
        if bill.due_date < reference:
            kind, title = "bill_overdue", f"{bill.card_name} bill is overdue"
        elif bill.due_date <= reference + timedelta(days=7):
            kind, title = "bill_due", f"{bill.card_name} bill is due soon"
        else:
            continue
        created += bool(create_notification(user_id, kind, title, f"Due {bill.due_date}.", f"{kind}:{bill.id}:{bill.due_date}"))
    for rule in db.session.scalars(
        db.select(RecurringExpense).where(
            RecurringExpense.user_id == user_id,
            RecurringExpense.active.is_(True),
            RecurringExpense.status == "active",
            RecurringExpense.next_due <= reference + timedelta(days=7),
        )
    ).all():
        created += bool(
            create_notification(
                user_id,
                "recurring_due",
                f"{rule.category.name} recurring payment is due",
                f"Expected {rule.amount_minor / 100:,.2f} on {rule.next_due}.",
                f"recurring-due:{rule.id}:{rule.next_due}",
            )
        )
    for investment in db.session.scalars(
        db.select(Investment).where(
            Investment.user_id == user_id,
            Investment.status == "active",
            Investment.maturity_date.is_not(None),
            Investment.maturity_date <= reference + timedelta(days=30),
        )
    ).all():
        created += bool(
            create_notification(
                user_id,
                "investment_maturity",
                f"{investment.name} maturity is approaching",
                f"Review this investment before {investment.maturity_date}. Values are estimates.",
                f"investment-maturity:{investment.id}:{investment.maturity_date}",
            )
        )
    for item in financial_insights(user_id, reference):
        if item["kind"] == "unusual":
            created += bool(
                create_notification(
                    user_id,
                    "unusual_spending",
                    item["title"],
                    item["message"],
                    f"unusual-spending:{user_id}:{reference.strftime('%Y-%m')}",
                )
            )
    for goal in db.session.scalars(db.select(SavingsGoal).where(SavingsGoal.user_id == user_id)).all():
        metrics = goal_metrics(goal, reference)
        if goal.status == "completed":
            created += bool(create_notification(user_id, "goal_completed", f"{goal.name} completed", "You reached your savings target.", f"goal-completed:{goal.id}"))
        elif metrics["progress"] >= 80:
            created += bool(create_notification(user_id, "goal_milestone", f"{goal.name} is 80% funded", "You are close to reaching this goal.", f"goal-milestone:{goal.id}:80"))
    return created


def advance_due(rule, due):
    if rule.frequency == "weekly":
        return due + timedelta(days=7)
    year = due.year + (rule.frequency == "yearly" or due.month == 12)
    month = due.month if rule.frequency == "yearly" else due.month % 12 + 1
    return date(year, month, min(rule.anchor_day, calendar.monthrange(year, month)[1]))


def process_recurring(user_id=None):
    """Claim each occurrence atomically, then insert it in the same transaction.

    The conditional UPDATE prevents two workers from claiming the same date.
    The unique occurrence constraint is a second guard against duplicates.
    """
    query = db.select(RecurringExpense).where(
        RecurringExpense.active.is_(True),
        RecurringExpense.status == "active",
        RecurringExpense.next_due <= today(),
    )
    if user_id is not None:
        query = query.where(RecurringExpense.user_id == user_id)
    rules = db.session.scalars(query).all()
    count = 0
    for rule in rules:
        due = rule.next_due
        while due <= today():
            following = advance_due(rule, due)
            claim = db.session.execute(
                db.update(RecurringExpense)
                .where(
                    RecurringExpense.id == rule.id,
                    RecurringExpense.next_due == due,
                    RecurringExpense.active.is_(True),
                    RecurringExpense.status == "active",
                )
                .values(next_due=following)
                .execution_options(synchronize_session=False)
            )
            if claim.rowcount != 1:
                break
            exists = db.session.scalar(
                db.select(Transaction.id).where(
                    Transaction.recurring_id == rule.id, Transaction.occurrence_date == due
                )
            )
            occurrence = db.session.scalar(
                db.select(RecurringOccurrence).where(
                    RecurringOccurrence.recurring_id == rule.id,
                    RecurringOccurrence.due_date == due,
                )
            )
            if not occurrence:
                occurrence = RecurringOccurrence(recurring_id=rule.id, due_date=due)
                db.session.add(occurrence)
            if occurrence.status != "skipped" and not exists:
                account = rule.account
                user_currency = "INR"
                transaction_currency = account.currency if account else user_currency
                rate = Decimal("1")
                rate_source = "same currency"
                rate_date = due
                base_amount = rule.amount_minor
                if account and transaction_currency != user_currency:
                    rate, rate_source, rate_date = exchange_rate(transaction_currency, user_currency)
                    base_amount = int((Decimal(rule.amount_minor) * rate).quantize(Decimal("1")))
                db.session.add(
                    transaction := Transaction(
                        user_id=rule.user_id,
                        category_id=rule.category_id,
                        amount_minor=rule.amount_minor,
                        kind="expense",
                        date=due,
                        occurrence_date=due,
                        payment_method=rule.payment_method,
                        notes=rule.notes,
                        recurring_id=rule.id,
                        account_id=account.id if account else None,
                        currency=transaction_currency,
                        base_amount_minor=base_amount,
                        exchange_rate=rate,
                        exchange_rate_date=rate_date,
                        exchange_rate_source=rate_source,
                    )
                )
                db.session.flush()
                occurrence.transaction_id = transaction.id
                occurrence.status = "paid"
                count += 1
            elif exists:
                occurrence.transaction_id = exists
                occurrence.status = "paid"
            due = following
    if count:
        users = {r.user_id for r in rules}
        for uid in users:
            log_event(uid, "Processed due recurring expenses")
    db.session.commit()
    db.session.expire_all()
    return count


def reset_token(user):
    serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    return serializer.dumps({"id": user.id, "version": user.session_token}, salt="password-reset")


def user_from_reset(token):
    try:
        data = URLSafeTimedSerializer(current_app.config["SECRET_KEY"]).loads(
            token, salt="password-reset", max_age=current_app.config["RESET_TOKEN_MAX_AGE"]
        )
        user = db.session.get(User, data["id"])
        if user and secrets.compare_digest(user.session_token, data["version"]):
            return user
    except (BadSignature, SignatureExpired, KeyError, TypeError):
        pass
    return None


def send_reset_email(user):
    # Build from trusted config, never the client-supplied Host header.
    link = current_app.config["APP_BASE_URL"] + "/auth/reset/" + reset_token(user)
    if current_app.config["MAIL_MODE"] == "console":
        current_app.logger.warning("LOCAL DEVELOPMENT reset link for %s: %s", user.email, link)
        return
    message = EmailMessage()
    message["Subject"] = "Reset your Xpense password"
    message["From"] = current_app.config["MAIL_FROM"]
    message["To"] = user.email
    message.set_content(
        f"Reset your password using this link (expires in 30 minutes):\n{link}\n\nIf you did not request this, ignore this email."
    )
    import ssl

    with smtplib.SMTP(
        current_app.config["MAIL_SERVER"], current_app.config["MAIL_PORT"], timeout=10
    ) as smtp:
        if current_app.config["MAIL_USE_TLS"]:
            smtp.starttls(context=ssl.create_default_context())
        if current_app.config["MAIL_USERNAME"]:
            smtp.login(current_app.config["MAIL_USERNAME"], current_app.config["MAIL_PASSWORD"])
        smtp.send_message(message)
```

## app/static/css/style.css

```css
/* Xpense · compact ledger. Zinc surfaces; financial colour carries meaning only. */
:root {
  color-scheme: light;
  --canvas: #fafafa; --surface: #ffffff; --surface-2: #f4f4f5;
  --ink: #18181b; --muted: #71717a; --line: #e4e4e7;
  --brand: #18181b; --brand-dark: #3f3f46; --brand-soft: #f4f4f5;
  --success: #047857; --warning: #92400e; --danger: #be123c;
  --radius: 8px; --sidebar: 232px; --gap: 20px;
  --motion-duration: 160ms; --motion-ease: cubic-bezier(0.22, 1, 0.36, 1);
  --font-ui: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  /* Compatibility aliases for existing templates and chart scripts. */
  --neu-bg: var(--canvas); --neu-surface: var(--surface); --neu-text: var(--ink);
  --neu-muted: var(--muted); --neu-accent: var(--brand);
  --neu-success: var(--success); --neu-warning: var(--warning); --neu-danger: var(--danger);
  --neu-raised: none; --neu-inset: none; --neu-small: none; --shadow: none;
  --gold: var(--warning); --bs-primary: var(--brand); --bs-body-color: var(--ink);
  --bs-body-bg: var(--canvas); --bs-border-color: var(--line);
  --bs-secondary-color: var(--muted); --bs-tertiary-bg: var(--surface-2);
  font-synthesis: none;
}
[data-bs-theme='dark'] {
  color-scheme: dark;
  --canvas: #09090b; --surface: #09090b; --surface-2: #18181b;
  --ink: #fafafa; --muted: #a1a1aa; --line: #27272a;
  --brand: #fafafa; --brand-dark: #d4d4d8; --brand-soft: #18181b;
  --success: #34d399; --warning: #fbbf24; --danger: #fb7185;
}
* { box-sizing: border-box; }
html { scrollbar-color: var(--line) var(--canvas); }
body { min-width: 320px; margin: 0; color: var(--ink); background: var(--canvas); font: 14px/1.5 var(--font-ui); font-variant-numeric: tabular-nums lining-nums; }
::selection { color: var(--canvas); background: var(--ink); }
input, textarea { caret-color: var(--ink); }
h1, h2, h3, h4, h5, h6 { color: var(--ink); font-family: var(--font-ui); font-weight: 600; letter-spacing: -.025em; text-wrap: balance; }
h1, h1.h2, .page-heading h1 { margin: 0; font-size: 24px; line-height: 1.25; }
h2, .h4 { font-size: 18px; }
h3, .h5, .card-title { font-size: 15px; }
a { color: var(--ink); text-decoration: none; text-underline-offset: 3px; }
a:hover { color: var(--brand-dark); }
p a, .small a { text-decoration: underline; }
button, input, select, textarea { font-family: inherit; font-variant-numeric: inherit; }
.content-shell { max-width: 1512px; margin-inline: auto; padding-inline: clamp(20px, 2.5vw, 40px); }
main.content-shell { padding-block: 28px !important; }
.app-main { min-height: 100vh; margin-left: var(--sidebar); }
.app-sidebar { position: fixed; inset: 0 auto 0 0; z-index: 1030; display: flex; flex-direction: column; width: var(--sidebar); padding: 22px 12px 14px; overflow-y: auto; overflow-x: hidden; border-right: 1px solid var(--line); background: var(--canvas); overscroll-behavior: contain; scrollbar-width: thin; }
.brand { display: flex; align-items: center; gap: 8px; padding: 0 10px 22px; color: var(--ink); }
.brand-mark { display: grid; place-items: center; width: 25px; height: 25px; color: var(--ink); font-size: 20px; }
.brand-name { font-size: 18px; font-weight: 650; letter-spacing: -.035em; }
.brand-subtitle { display: block; margin-top: 1px; color: var(--muted); font-size: 11px; letter-spacing: 0; text-transform: none; }
.nav-section { margin: 8px 10px; color: var(--muted); font-size: 12px; }
.sidebar-nav { display: grid; gap: 2px; }
.sidebar-link { display: flex; align-items: center; gap: 10px; min-height: 32px; padding: 5px 10px; color: var(--muted); border-radius: 5px; font-size: 13px; }
.sidebar-link > span { display: flex; align-items: center; gap: 5px; flex: 1; }
.sidebar-link .bi { width: 16px; text-align: center; font-size: 14px; }
.sidebar-link:hover, .sidebar-link.active { color: var(--ink); background: var(--surface-2); }
.sidebar-link.active { font-weight: 600; }
.sidebar-footer { margin-top: auto; padding-top: 24px; }
.profile-link { display: flex; align-items: center; gap: 9px; padding: 8px; border-top: 1px solid var(--line); }
.avatar { display: grid; place-items: center; width: 28px; height: 28px; flex: 0 0 28px; border: 1px solid var(--line); border-radius: 50%; font-size: 12px; font-weight: 600; }
.profile-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
.profile-email { display: block; overflow: hidden; text-overflow: ellipsis; color: var(--muted); font-size: 11px; font-weight: 400; }
.theme-button { display: flex; align-items: center; justify-content: flex-start; gap: 8px; width: 100%; min-height: 30px !important; margin-top: 2px; padding: 4px 9px; border-color: transparent !important; background: transparent !important; font-size: 13px; font-weight: 400 !important; }
.mobile-header { display: none; }
.page-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 24px; }
.page-heading p { max-width: 70ch; margin: 7px 0 0; color: var(--muted); }
/* Legacy decorative kickers are suppressed across the existing screens. */
.eyebrow { display: none; }
.card, .neu-card { color: var(--ink); border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); box-shadow: none; }
.card-body { padding: 20px; }
.card-title { margin-bottom: 12px; font-weight: 600; }
.metric-icon, .icon-tile, .stat-icon { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; color: var(--muted); font-size: 17px; background: transparent; }
.metric-label, .dashboard-label { display: block; margin-bottom: 7px; color: var(--muted); font-size: 12px; font-weight: 400; }
.money-value { display: inline-flex; align-items: baseline; gap: .2em; max-width: 100%; font-variant-numeric: tabular-nums lining-nums; white-space: nowrap; letter-spacing: -.025em; }
.money-value--hero, .hero-value { font-size: 32px; font-weight: 600; line-height: 1.2; }
.money-value--metric, .metric-value { font-size: clamp(20px, 1.8vw, 26px); font-weight: 600; line-height: 1.3; letter-spacing: -.025em; white-space: nowrap; }
.money-value--compact { font-size: 13px; font-weight: 600; }
.btn, .neu-button { min-height: 36px; padding: 7px 12px; border-radius: 5px; font-size: 13px; font-weight: 500; box-shadow: none !important; transition: background-color 120ms var(--motion-ease), border-color 120ms var(--motion-ease); }
.btn-outline-danger { --bs-btn-color: var(--danger); --bs-btn-border-color: var(--danger); --bs-btn-hover-color: var(--canvas); --bs-btn-hover-bg: var(--danger); --bs-btn-hover-border-color: var(--danger); --bs-btn-active-color: var(--canvas); --bs-btn-active-bg: var(--danger); --bs-btn-active-border-color: var(--danger); }
.btn-primary { --bs-btn-color: var(--canvas); --bs-btn-bg: var(--brand); --bs-btn-border-color: var(--brand); --bs-btn-hover-color: var(--canvas); --bs-btn-hover-bg: var(--brand-dark); --bs-btn-hover-border-color: var(--brand-dark); --bs-btn-active-color: var(--canvas); --bs-btn-active-bg: var(--brand-dark); --bs-btn-active-border-color: var(--brand-dark); --bs-btn-disabled-color: var(--canvas); --bs-btn-disabled-bg: var(--brand); --bs-btn-disabled-border-color: var(--brand); }
.btn-light, .btn-dark, .btn-outline-dark, .btn-outline-primary, .btn-outline-secondary { --bs-btn-color: var(--ink); --bs-btn-bg: var(--surface); --bs-btn-border-color: var(--line); --bs-btn-hover-color: var(--ink); --bs-btn-hover-bg: var(--surface-2); --bs-btn-hover-border-color: var(--muted); --bs-btn-active-color: var(--ink); --bs-btn-active-bg: var(--surface-2); --bs-btn-active-border-color: var(--muted); }
.btn-link { color: var(--ink); }
.btn-sm { min-height: 28px; padding: 3px 8px; font-size: 12px; }
.btn:disabled, .btn.disabled, [aria-disabled='true'] { opacity: .5; cursor: not-allowed; }
.shortcut, kbd { padding: 1px 5px; color: inherit; background: transparent; border: 1px solid currentColor; border-radius: 3px; font: inherit; font-size: 10px; opacity: .7; }
.form-label { margin-bottom: 5px; color: var(--ink); font-size: 12px; font-weight: 500; }
.form-control, .form-select, .neu-input, .input-group-text { min-height: 38px; color: var(--ink); background-color: var(--surface); border: 1px solid var(--line); border-radius: 5px; font-size: 13px; }
.form-control::placeholder { color: var(--muted); opacity: 1; }
.form-control:focus, .form-select:focus { color: var(--ink); border-color: var(--ink); background-color: var(--surface); box-shadow: none; }
.form-control:disabled, .form-select:disabled { background: var(--surface-2); opacity: .65; }
.input-group-text { color: var(--muted); }
.form-check-input { border-color: var(--muted); background-color: var(--surface); }
.form-check-input:checked { border-color: var(--ink); background-color: var(--ink); }
.form-text, .text-body-secondary { color: var(--muted) !important; }
.text-primary { color: var(--ink) !important; }
.text-success { color: var(--success) !important; }
.text-warning { color: var(--warning) !important; }
.text-danger, .invalid-feedback { color: var(--danger) !important; }
.border-success { border-color: var(--success) !important; }
.table { --bs-table-color: var(--ink); --bs-table-bg: transparent; --bs-table-border-color: var(--line); margin-bottom: 0; font-size: 13px; font-variant-numeric: tabular-nums; }
.table th { padding: 9px 12px; color: var(--muted); font-size: 12px; font-weight: 500; white-space: nowrap; }
.table td { height: 42px; padding: 8px 12px; vertical-align: middle; }
.table tbody tr:last-child td { border-bottom: 0; }
.table tbody tr:hover { background: var(--surface-2); }
.table .btn { min-height: 26px; }
.badge, .category-pill { display: inline-flex; align-items: center; padding: 3px 6px; border-radius: 4px; color: var(--muted); background: var(--surface-2); font-size: 11px; font-weight: 500; }
.text-bg-primary, .text-bg-info, .text-bg-secondary { color: var(--ink) !important; background: var(--surface-2) !important; }
.text-bg-success { color: var(--success) !important; background: var(--surface-2) !important; }
.text-bg-warning { color: var(--warning) !important; background: var(--surface-2) !important; }
.text-bg-danger { color: var(--danger) !important; background: var(--surface-2) !important; }
.alert { padding: 12px 16px; border: 1px solid var(--line); border-radius: 5px; color: var(--ink); background: var(--surface); box-shadow: none; }
.alert-success { color: var(--success); }
.alert-warning { color: var(--warning); }
.alert-danger { color: var(--danger); }
.dropdown-menu { --bs-dropdown-color: var(--ink); --bs-dropdown-bg: var(--surface); --bs-dropdown-link-color: var(--ink); --bs-dropdown-link-hover-color: var(--ink); --bs-dropdown-link-hover-bg: var(--surface-2); --bs-dropdown-border-color: var(--line); padding: 5px; border-radius: 6px; box-shadow: 0 8px 24px #00000014; font-size: 13px; }
.list-group-item { color: var(--ink); background: var(--surface); border-color: var(--line); }
.pagination .page-link { color: var(--ink); background: var(--surface); border-color: var(--line); }
.pagination .active .page-link { color: var(--canvas); background: var(--ink); border-color: var(--ink); }
.auth-layout { max-width: 430px; padding-block: 7vh; }
.form-layout { max-width: 700px; }
.note-text { max-width: 65ch; white-space: pre-wrap; overflow-wrap: anywhere; }
.empty-state, .empty-block { display: grid; justify-items: center; gap: 8px; padding: 28px 18px; text-align: center; }
.empty-illustration { color: var(--muted); font-size: 24px; }
.empty-state p { max-width: 45ch; margin: 0; color: var(--muted); font-size: 13px; }
.site-footer { color: var(--muted); font-size: 11px; }
.chart-wrap { position: relative; height: 220px; font-variant-numeric: tabular-nums; }
.chart-wrap-large { height: 240px; }
.chart-wrap canvas { max-width: 100%; font-variant-numeric: tabular-nums; }
/* 1440px: 232px sidebar + 2×36px canvas gutter + 1136px content.
   Main split: (1136px - 20px gap) × 2/3 and × 1/3; all edges close. */
.executive-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin-bottom: 24px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); }
.executive-summary:has(> :last-child:nth-child(3)) { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.executive-summary > * { min-width: 0; padding: 18px 20px; }
.executive-summary > * + * { border-left: 1px solid var(--line); }
.executive-summary .metric-value { margin-block: 4px; }
.executive-summary .summary-metric:nth-child(3) .metric-value { font-size: 30px; }
.executive-summary .money-value { font-size: inherit; font-weight: inherit; }
.dashboard-columns { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); gap: var(--gap); align-items: start; }
.dashboard-columns > *, .dashboard-stack { min-width: 0; }
.dashboard-stack { display: grid; gap: var(--gap); }
.section-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
.section-header h2, .section-header h3 { margin: 0; font-size: 15px; }
.section-header p { margin: 4px 0 0; color: var(--muted); font-size: 12px; }
.section-header a { color: var(--muted); font-size: 12px; }
.pacing-panel { padding: 18px 20px; margin-bottom: 24px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); }
.pacing-heading, .pacing-meta { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.pacing-meta { margin-top: 8px; color: var(--muted); font-size: 12px; }
.budget-progress { display: block; width: 100%; height: 5px; margin-top: 12px; border: 0; border-radius: 2px; appearance: none; background: var(--surface-2); color: var(--success); }
.budget-progress::-webkit-progress-bar { background: var(--surface-2); border-radius: 2px; }
.budget-progress::-webkit-progress-value { background: var(--success); border-radius: 2px; }
.budget-progress::-moz-progress-bar { background: var(--success); border-radius: 2px; }
.budget-progress.warning::-webkit-progress-value { background: var(--warning); }
.budget-progress.warning::-moz-progress-bar { background: var(--warning); }
.budget-progress.danger::-webkit-progress-value { background: var(--danger); }
.budget-progress.danger::-moz-progress-bar { background: var(--danger); }
.compact-list { padding: 0; margin: 0; list-style: none; }
.compact-list > li, .compact-list > div, .compact-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 42px; padding-block: 9px; border-bottom: 1px solid var(--line); }
.compact-list > :last-child { border-bottom: 0; }
.transaction-group { padding: 9px 12px; color: var(--muted); background: var(--surface-2); font-size: 12px; font-weight: 500; }
.transaction-description { max-width: 320px; white-space: nowrap; }
.transaction-note { display: inline-block; max-width: 120px; margin-left: 7px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: middle; }
.account-caption { display: inline; margin-left: 6px; color: var(--muted); font-size: 12px; }
.transaction-kind { font-size: 13px; }
.transaction-group th { padding-block: 6px; background: var(--surface-2); font-size: 11px; }
.budget-line + .budget-line, .insight-row { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--line); }
.budget-line strong { font-size: 12px; font-weight: 500; }
.budget-line p { margin: 6px 0 0; }
.insight-row p { margin: 4px 0 0; color: var(--muted); font-size: 12px; }
.workspace-details > summary { width: fit-content; cursor: pointer; font-weight: 500; }
.chart-skeleton { display: none; position: absolute; inset: 0; border: 1px dashed var(--line); background: var(--surface-2); }
.chart-loading .chart-skeleton { display: block; }
.chart-loading canvas { visibility: hidden; }
.assistant-shell, .assistant-heading { max-width: 920px; margin-inline: auto; }
.assistant-empty { display: flex; align-items: center; gap: 16px; max-width: 500px; margin: 8px auto 24px; }
.assistant-empty p { color: var(--muted); margin-bottom: 0; }
.assistant-message { max-width: 78%; margin-bottom: 16px; padding: 12px 16px; border-radius: 8px; border: 1px solid var(--line); }
.assistant-message.user { margin-left: auto; background: var(--surface-2); }
.assistant-role { display: block; margin-bottom: 5px; color: var(--muted); font-size: 11px; }
.assistant-composer { border-top: 1px solid var(--line); padding-top: 16px; }
.assistant-prompts { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.assistant-prompt { padding: 6px 9px; border: 1px solid var(--line); border-radius: 5px; color: var(--muted); background: var(--surface); font-size: 12px; cursor: pointer; }
.assistant-prompt:hover { color: var(--ink); border-color: var(--muted); }
.neu-dialog, .composer-dialog, .filter-dialog { width: min(600px, calc(100vw - 32px)); max-width: 600px; max-height: calc(100dvh - 40px); padding: 0; color: var(--ink); border: 1px solid var(--line); border-radius: 10px; background: var(--surface); box-shadow: 0 20px 60px #00000030; }
.neu-dialog::backdrop, .composer-dialog::backdrop, .filter-dialog::backdrop { background: #00000070; }
.dialog-panel { max-height: calc(100dvh - 42px); overflow-y: auto; background: var(--surface); }
.dialog-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; padding: 20px; border-bottom: 1px solid var(--line); }
.dialog-body { padding: 20px; }
.dialog-form > .form-field { margin-bottom: 16px !important; }
.dialog-form .dialog-optional-field { display: none; }
.dialog-form .dialog-optional-field.is-visible { display: block; }
.dialog-more { margin-block: 4px 18px; }
.dialog-more summary { color: var(--ink); cursor: pointer; font-size: 12px; }
.composer-type { display: flex; width: fit-content; gap: 4px; padding: 3px; background: var(--surface-2); border-radius: 5px; }
.composer-type button { border: 0; background: transparent; color: var(--muted); padding: 5px 10px; border-radius: 3px; font-size: 12px; }
.composer-type [aria-pressed='true'], .composer-type .active { color: var(--ink); background: var(--surface); }
.composer-amount { width: 100%; min-height: 70px; padding: 12px 0; color: var(--ink); background: transparent; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; font-size: 36px; font-weight: 600; letter-spacing: -.03em; }
.composer-fields { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 18px; }
.composer-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding-top: 20px; }
.composer-status, [data-compose-status] { font-size: 12px; }
[aria-busy='true'] { cursor: progress; }
[aria-busy='true'] button[type='submit'] { opacity: .6; }
.field-error, .error-message { color: var(--danger); font-size: 12px; }
[aria-invalid='true'] { border-color: var(--danger) !important; }
:focus-visible { outline: 2px solid var(--ink); outline-offset: 3px; }
.form-control:focus-visible, .form-select:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
.privacy-mode :is(.metric-value,.hero-value,.table td.text-end,.investment-value) { filter: blur(7px); user-select: none; }
body.sidebar-collapsed { --sidebar: 64px; }
body.sidebar-collapsed .app-sidebar { padding-inline: 8px; }
body.sidebar-collapsed .brand { justify-content: center; padding-inline: 0; }
body.sidebar-collapsed :is(.brand > span:last-child,.nav-section,.sidebar-link span,.sidebar-footer .btn span,.profile-name,.theme-button span,.sidebar-collapse-button span,.sidebar-footer .shortcut) { display: none; }
body.sidebar-collapsed .sidebar-link, body.sidebar-collapsed .profile-link, body.sidebar-collapsed .theme-button { justify-content: center; padding-inline: 0; }
@media (max-width: 1199px) {
  .executive-summary > * { padding: 16px 14px; }
}
@media (max-width: 991px) {
  body.sidebar-collapsed { --sidebar: 232px; }
  .app-sidebar { transform: translateX(-100%); transition: transform var(--motion-duration) var(--motion-ease); }
  body.nav-open .app-sidebar { transform: none; }
  .app-main { margin-left: 0; }
  .mobile-header { position: sticky; top: 0; z-index: 1020; display: flex; justify-content: space-between; align-items: center; min-height: 54px; padding-inline: 20px; background: var(--canvas); border-bottom: 1px solid var(--line); }
  .mobile-header .brand { padding: 0; }
  .mobile-menu-toggle { display: grid; place-items: center; width: 36px; height: 36px; color: var(--ink); background: var(--surface); border: 1px solid var(--line); border-radius: 5px; font-size: 20px; }
  body.nav-open::after { position: fixed; inset: 0; z-index: 1025; background: #00000060; content: ''; }
  body.sidebar-collapsed .app-sidebar { padding-inline: 12px; }
  body.sidebar-collapsed .brand { justify-content: flex-start; padding-inline: 10px; }
  body.sidebar-collapsed :is(.brand > span:last-child,.nav-section,.sidebar-link span,.sidebar-footer .btn span,.profile-name,.theme-button span,.sidebar-collapse-button span) { display: revert; }
  body.sidebar-collapsed .sidebar-link, body.sidebar-collapsed .profile-link, body.sidebar-collapsed .theme-button { justify-content: flex-start; padding-inline: 10px; }
}
@media (max-width: 767px) {
  .dashboard-columns { grid-template-columns: minmax(0, 1fr); }
  .executive-summary, .executive-summary:has(> :last-child:nth-child(3)) { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .executive-summary > :nth-child(odd) { border-left: 0; }
  .executive-summary > :nth-child(n+3) { border-top: 1px solid var(--line); }
  .executive-summary > :last-child:nth-child(3) { grid-column: 1 / -1; }
  .page-heading { align-items: flex-start; flex-wrap: wrap; gap: 14px; }
  .page-heading > .d-flex { flex-wrap: wrap; }
  .table td { min-height: 44px; }
  .transaction-description { min-width: 160px; }
  .transaction-note { display: block; max-width: 150px; margin: 3px 0 0; }
  .account-caption { display: block; margin: 2px 0 0; }
  .payment-column { display: none; }
  .composer-fields { grid-template-columns: minmax(0, 1fr); }
  .chart-wrap { height: 200px; }
}
@media (max-width: 575px) {
  main.content-shell { padding-block: 20px !important; }
  .card-body { padding: 16px; }
  .metric-value, .money-value--metric { font-size: 20px; }
  .executive-summary .metric-value, .executive-summary .summary-metric:nth-child(3) .metric-value { font-size: clamp(15px, 4.3vw, 19px); }
  .page-heading .btn { min-height: 40px; }
  .table td, .table th { padding-inline: 10px; }
  .assistant-message { max-width: 93%; }
  .neu-dialog, .composer-dialog { width: calc(100vw - 24px); max-height: calc(100dvh - 24px); }
  .dialog-header, .dialog-body { padding: 18px; }
  .composer-footer { flex-wrap: wrap; }
  .composer-footer .btn { min-height: 42px; }
  .form-control, .form-select { font-size: 16px; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; animation-duration: .01ms !important; animation-iteration-count: 1 !important; transition-duration: .01ms !important; }
}


/* Asynchronous ledger feedback: pending entries remain visibly unconfirmed. */
.pending-transaction { color: var(--muted); background: var(--surface-2); }
.composer-dialog [hidden] { display: none !important; }
.composer-dialog .field-error:not(:empty) { display: block; margin-top: 5px; }
.composer-dialog fieldset { border: 0; padding: 0; }
.composer-dialog legend { font-size: 13px; font-weight: 600; }
.composer-dialog [role='status'] { max-width: 65%; font-size: 12px; }


/* Mobile sheets share a protected drag strip; editing fields never initiate a drag. */
.sheet-handle { display: none; }
.mobile-filter-trigger, .mobile-transaction-kind { display: none; }
@media (max-width: 639px) {
  .composer-dialog, .filter-dialog {
    position: fixed; inset: auto 0 0; width: 100%; max-width: none;
    max-height: 90dvh; margin: 0; padding: 0 0 env(safe-area-inset-bottom);
    border-radius: 14px 14px 0 0; overflow-y: auto; overscroll-behavior: contain;
  }
  .filters-enhanced .mobile-filter-trigger { display: inline-flex; min-height: 44px; }
  .filters-enhanced #filter-home { display: none; }
  .sheet-handle { display: grid; place-items: center; width: 100%; height: 44px; touch-action: none; user-select: none; cursor: grab; }
  .sheet-handle > span { width: 32px; height: 4px; border-radius: 2px; background: var(--muted); }
  .sheet-handle:active { cursor: grabbing; }
  .composer-dialog .dialog-header, .filter-dialog .dialog-header { align-items: center; padding-top: 0; }
  .composer-dialog .dialog-body, .filter-dialog .dialog-body { padding-bottom: max(20px, env(safe-area-inset-bottom)); }
  button, .btn, select, input:not([type='hidden']), .form-control, .form-select, summary { min-height: 44px; }
  button, .btn { min-width: 44px; }
  .btn-close { display: grid; place-items: center; width: 44px; height: 44px; margin: -4px -8px -4px 0; padding: 0; background-size: 12px; }
  .composer-type button, .composer-footer .btn, .table .btn { min-height: 44px; min-width: 44px; }
  .theme-button { min-height: 44px !important; }
  .sidebar-link { min-height: 44px; }
  .filter-dialog .dialog-body { overflow: visible; }
  .transaction-table th, .transaction-table .category-pill { font-size: 13px; }
  .mobile-transaction-kind { display: block; color: var(--muted); font-size: 12px; }
  .transaction-table th:nth-child(4), .transaction-table td:has(> .transaction-kind) { display: none; }
}
```

## app/static/js/motion.js

```javascript
/* Interruptible interface feedback. The ledger remains authoritative. */
(() => {
  const duration = 160;
  const easing = 'cubic-bezier(0.22, 1, 0.36, 1)';
  const running = new WeakMap();
  const requests = new WeakMap();
  const next = element => { const token = {}; requests.set(element, token); return token; };
  const sheets = new WeakSet();
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const mobile = () => matchMedia('(max-width: 639px)').matches;
  const snapshot = element => {
    const style = getComputedStyle(element);
    return {opacity: style.opacity, transform: style.transform};
  };
  function clear(element) { element.style.opacity = ''; element.style.transform = ''; element.style.willChange = ''; }
  function stop(element) {
    const frame = snapshot(element);
    const old = running.get(element);
    if (old) { running.delete(element); old.animation.cancel(); }
    element.style.opacity = frame.opacity;
    element.style.transform = frame.transform;
    return frame;
  }
  async function transition(element, destination, initial) {
    const wasRunning = running.has(element);
    const current = stop(element);
    const from = !wasRunning && initial ? initial : current;
    if (reduced() || typeof element.animate !== 'function') { clear(element); return true; }
    element.style.willChange = 'transform, opacity';
    const animation = element.animate([from, destination], {duration, easing, fill: 'both'});
    const record = {animation}; running.set(element, record);
    try {
      await animation.finished;
      if (running.get(element) !== record) return false;
      running.delete(element); animation.cancel(); clear(element); return true;
    } catch (_) {
      // Cancellation is a normal retarget, not an error or a completed close.
      return false;
    }
  }
  const rest = {opacity: '1', transform: 'translateY(0px) scale(1)'};
  const exitFrame = () => ({opacity: mobile() ? '1' : '0', transform: mobile() ? 'translateY(100%)' : 'translateY(6px) scale(.98)'});
  function open(dialog) {
    next(dialog);
    const alreadyOpen = dialog.open;
    if (!alreadyOpen) dialog.showModal();
    return transition(dialog, rest, alreadyOpen ? null : exitFrame());
  }
  async function close(dialog) {
    if (!dialog.open) { reset(dialog); return true; }
    const token = next(dialog);
    const settled = await transition(dialog, exitFrame());
    if (!settled || requests.get(dialog) !== token) return false;
    dialog.close();
    return settled;
  }
  function insert(row) { next(row); return transition(row, rest, {opacity: '0', transform: 'translateY(-4px)'}); }
  async function remove(row) {
    const token = next(row);
    const settled = await transition(row, {opacity: '0', transform: 'translateY(-4px)'});
    if (!settled || requests.get(row) !== token) return false;
    row.remove();
    return settled;
  }
  function reset(element) { next(element); stop(element); clear(element); }
  function bindSheet(dialog, requestClose) {
    if (sheets.has(dialog)) return;
    const handle = dialog.querySelector('[data-sheet-handle]');
    if (!handle) return;
    sheets.add(dialog);
    let drag = null;
    const busy = () => dialog.querySelector('form[aria-busy="true"]');
    handle.addEventListener('pointerdown', event => {
      if (!mobile() || !dialog.open || busy() || drag || event.isPrimary === false || event.button !== 0) return;
      const frame = stop(dialog);
      // A pointer can take over during an interrupted entrance without snapping to zero.
      let base = 0;
      const matrix = frame.transform.match(/^matrix\([^,]+,[^,]+,[^,]+,[^,]+,[^,]+,\s*([^\)]+)\)$/);
      const translated = frame.transform.match(/^translateY\((-?[\d.]+)px\)/);
      if (matrix) base = Number(matrix[1]) || 0;
      else if (translated) base = Number(translated[1]) || 0;
      drag = {id: event.pointerId, start: event.clientY, distance: 0, base};
      handle.setPointerCapture?.(event.pointerId); event.preventDefault();
    });
    handle.addEventListener('pointermove', event => {
      if (!drag || drag.id !== event.pointerId) return;
      drag.distance = Math.max(0, event.clientY - drag.start);
      if (!reduced()) dialog.style.transform = `translateY(${Math.min(dialog.getBoundingClientRect().height, Math.max(0, drag.base + event.clientY - drag.start))}px)`;
      event.preventDefault();
    });
    function finish(event, cancelled) {
      if (!drag || drag.id !== event.pointerId) return;
      const dismiss = !cancelled && drag.distance > 64 && mobile() && !busy();
      drag = null;
      try { handle.releasePointerCapture?.(event.pointerId); } catch (_) {}
      if (dismiss) requestClose();
      else transition(dialog, rest);
    }
    handle.addEventListener('pointerup', event => finish(event, false));
    handle.addEventListener('pointercancel', event => finish(event, true));
    dialog.addEventListener('close', () => { drag = null; reset(dialog); });
  }
  window.XpenseMotion = {open, close, insert, remove, reset, bindSheet};
})();
```

## app/static/js/app.js

```javascript
const toggle = document.getElementById('theme-toggle');
const sidebar = document.getElementById('app-sidebar');
const menuToggle = document.getElementById('mobile-menu-toggle');
const privacyToggle = document.getElementById('privacy-toggle');
const collapseToggle = document.getElementById('sidebar-collapse');

function updateToggle() {
  if (!toggle) return;
  const dark = document.documentElement.dataset.bsTheme === 'dark';
  toggle.innerHTML = `<i class="bi bi-${dark ? 'sun' : 'moon-stars'}" aria-hidden="true"></i><span>${dark ? 'Light mode' : 'Dark mode'}</span>`;
  toggle.setAttribute('aria-pressed', String(dark));
}

if (toggle) {
  updateToggle();
  toggle.addEventListener('click', () => {
    const theme = document.documentElement.dataset.bsTheme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.bsTheme = theme;
    try { localStorage.setItem('expense-theme', theme); } catch (_) {}
    updateToggle();
    window.dispatchEvent(new Event('themechange'));
  });
}

function updatePrivacyToggle() {
  if (!privacyToggle) return;
  const enabled = document.body.classList.contains('privacy-mode');
  privacyToggle.setAttribute('aria-pressed', String(enabled));
  privacyToggle.innerHTML = `<i class="bi bi-${enabled ? 'eye' : 'eye-slash'}" aria-hidden="true"></i><span>${enabled ? 'Show amounts' : 'Privacy mode'}</span>`;
}

try {
  if (localStorage.getItem('expense-privacy') === 'on') document.body.classList.add('privacy-mode');
} catch (_) {}
updatePrivacyToggle();
if (privacyToggle) {
  privacyToggle.addEventListener('click', () => {
    const enabled = document.body.classList.toggle('privacy-mode');
    try { localStorage.setItem('expense-privacy', enabled ? 'on' : 'off'); } catch (_) {}
    updatePrivacyToggle();
  });
}

function updateCollapseToggle() {
  if (!collapseToggle) return;
  const collapsed = document.body.classList.contains('sidebar-collapsed');
  collapseToggle.setAttribute('aria-pressed', String(collapsed));
  collapseToggle.setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
  collapseToggle.innerHTML = `<i class="bi bi-${collapsed ? 'layout-sidebar' : 'layout-sidebar-inset'}" aria-hidden="true"></i><span>${collapsed ? 'Expand sidebar' : 'Collapse sidebar'}</span>`;
}
try {
  if (localStorage.getItem('expense-sidebar') === 'collapsed') document.body.classList.add('sidebar-collapsed');
} catch (_) {}
updateCollapseToggle();
if (collapseToggle) {
  collapseToggle.addEventListener('click', () => {
    const collapsed = document.body.classList.toggle('sidebar-collapsed');
    try { localStorage.setItem('expense-sidebar', collapsed ? 'collapsed' : 'expanded'); } catch (_) {}
    updateCollapseToggle();
  });
}

function closeMobileNav() {
  document.body.classList.remove('nav-open');
  if (menuToggle) {
    menuToggle.setAttribute('aria-expanded', 'false');
    menuToggle.innerHTML = '<i class="bi bi-list" aria-hidden="true"></i>';
  }
}

if (menuToggle && sidebar) {
  menuToggle.addEventListener('click', () => {
    const open = document.body.classList.toggle('nav-open');
    menuToggle.setAttribute('aria-expanded', String(open));
    menuToggle.innerHTML = `<i class="bi bi-${open ? 'x-lg' : 'list'}" aria-hidden="true"></i>`;
  });
  document.querySelectorAll('.sidebar-link').forEach(link => link.addEventListener('click', closeMobileNav));
  document.addEventListener('click', event => {
    if (document.body.classList.contains('nav-open') && !sidebar.contains(event.target) && !menuToggle.contains(event.target)) closeMobileNav();
  });
}


// Ordinary forms retain their normal navigation; asynchronous forms own their pending state.
document.addEventListener('submit', event => {
  const form = event.target;
  if (form.matches('[data-async-form]') || event.defaultPrevented) return;
  if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) { event.preventDefault(); return; }
  const button = form.querySelector('button[type="submit"]');
  if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); button.textContent = 'Saving…'; }
});
window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
document.querySelectorAll('.assistant-prompt').forEach(prompt => {
  prompt.addEventListener('click', () => {
    const input = document.getElementById('assistant-question');
    if (input) { input.value = prompt.dataset.prompt || ''; input.focus(); }
  });
});
document.addEventListener('keydown', event => { if (event.key === 'Escape') closeMobileNav(); });

const dialogControls = new Map();
function wireDialog(dialog, closeSelector) {
  let previous;
  const motion = window.XpenseMotion;
  const busy = () => dialog.querySelector('form[aria-busy="true"]');
  const close = () => {
    if (busy()) return Promise.resolve(false);
    if (motion) return motion.close(dialog);
    dialog.close(); return Promise.resolve(true);
  };
  const open = opener => {
    if (typeof dialog.showModal !== 'function') return false;
    if (!dialog.open) previous = opener || document.activeElement;
    closeMobileNav();
    if (motion) motion.open(dialog);
    else if (!dialog.open) dialog.showModal();
    return true;
  };
  dialog.addEventListener('click', event => {
    if (event.target.closest(closeSelector)) { close(); return; }
    const rect = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) close();
  });
  dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
  dialog.addEventListener('close', () => {
    if (dialog.open) return;
    motion?.reset(dialog);
    if (previous?.isConnected) previous.focus();
  });
  motion?.bindSheet(dialog, close);
  dialogControls.set(dialog.id, {open, close});
  return open;
}
window.XpenseDialogs = {
  open: (id, opener) => dialogControls.get(id)?.open(opener),
  close: id => dialogControls.get(id)?.close()
};
const filterDialog = document.getElementById('transaction-filters-sheet');
if (filterDialog) {
  const openFilters = wireDialog(filterDialog, '[data-filter-close]');
  document.addEventListener('click', event => {
    const opener = event.target.closest('[data-filter-open]');
    if (opener && openFilters(opener)) { event.preventDefault(); filterDialog.querySelector('input:not([type="hidden"]),select,button')?.focus(); }
  });
}
document.querySelectorAll('dialog[data-dialog], dialog.neu-dialog').forEach(dialog => {
  const open = wireDialog(dialog, '[data-dialog-close]');
  const optional = [...dialog.querySelectorAll('.dialog-optional-field')];
  if (optional.length) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'btn btn-link dialog-more p-0 text-start'; button.textContent = 'More details'; button.setAttribute('aria-expanded', 'false'); optional[0].before(button);
    button.addEventListener('click', () => {
      const visible = button.getAttribute('aria-expanded') !== 'true';
      button.setAttribute('aria-expanded', String(visible)); button.textContent = visible ? 'Hide optional details' : 'More details';
      optional.forEach(field => field.classList.toggle('is-visible', visible));
    });
  }
  const updateAccountFields = () => {
    const type = dialog.querySelector('[name="account_type"]');
    if (!type) return;
    ['provider', 'last_four'].forEach(name => { const field = dialog.querySelector(`[data-field-name="${name}"]`); if (field) field.hidden = type.value === 'cash'; });
  };
  document.querySelectorAll(`[data-dialog-open="${dialog.id}"]`).forEach(button => button.addEventListener('click', () => {
    if (open(button)) { updateAccountFields(); dialog.querySelector('input:not([type="hidden"]),select,textarea')?.focus(); }
  }));
  dialog.querySelector('[name="account_type"]')?.addEventListener('change', updateAccountFields);
});

function setPending(form, pending) {
  form.setAttribute('aria-busy', String(pending));
  form.querySelectorAll('button[type="submit"]').forEach(button => {
    if (pending) { button.dataset.idleLabel = button.textContent; button.textContent = 'Saving…'; }
    else button.textContent = button.dataset.idleLabel || 'Save';
    button.disabled = pending;
  });
}
async function saveRequest(url, options) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 20000);
  try { return await fetch(url, {...options, signal: controller.signal}); }
  catch (error) {
    if (error.name === 'AbortError') throw new Error('The save is taking too long. Check your ledger before trying again; the server may have received it.');
    throw new Error('Connection lost. Check your ledger before trying again; the server may have received it.');
  } finally { window.clearTimeout(timeout); }
}
async function readResponse(response) {
  let result;
  try { result = await response.json(); } catch (_) { throw new Error('The server could not save this entry. Check your connection and try again.'); }
  if (!response.ok) {
    const error = new Error(result.error || 'Check the highlighted fields and try again.');
    error.fields = result.errors; throw error;
  }
  return result;
}
function pendingTransaction(form) {
  const tbody = document.querySelector('.transaction-table tbody');
  if (!tbody) return null;
  const row = document.createElement('tr'); row.className = 'pending-transaction'; row.setAttribute('aria-busy', 'true');
  const cell = document.createElement('td'); cell.colSpan = 6;
  cell.textContent = `Saving ${form.elements.kind.value}: ${form.elements.amount.value} ${form.elements.account_id.selectedOptions[0]?.textContent.match(/\b[A-Z]{3}\s*$/)?.[0]?.trim() || 'INR'} · ${form.elements.category_id.selectedOptions[0]?.textContent || 'Transaction'}…`;
  row.append(cell); tbody.prepend(row); window.XpenseMotion?.insert(row); return row;
}
const composer = document.getElementById('expense-composer');
if (composer) {
  const form = document.getElementById('compose-form');
  const status = document.getElementById('compose-status');
  const amount = document.getElementById('compose-amount');
  const account = document.getElementById('compose-account');
  const updateCurrency = () => {
    const name = account.selectedOptions[0]?.textContent || '';
    const currency = name.match(/\b[A-Z]{3}\s*$/)?.[0]?.trim() || 'INR';
    document.getElementById('compose-amount-label').textContent = `Amount · ${currency}`;
  };
  account.addEventListener('change', updateCurrency); updateCurrency();
  const open = wireDialog(composer, '[data-compose-close]');
  const openComposer = opener => { if (open(opener)) { amount.focus(); return true; } return false; };
  document.addEventListener('click', event => {
    const opener = event.target.closest('[data-compose]');
    if (!opener) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (openComposer(opener)) event.preventDefault();
  });
  document.addEventListener('keydown', event => {
    const editable = event.target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"])');
    if (event.defaultPrevented || event.repeat || editable || document.querySelector('dialog[open]') || document.querySelector('.modal.show')) return;
    const command = (event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === 'k';
    const c = !event.metaKey && !event.ctrlKey && !event.altKey && event.key.toLowerCase() === 'c';
    if ((command || c) && openComposer()) event.preventDefault();
  });
  composer.querySelectorAll('[data-compose-kind]').forEach(button => button.addEventListener('click', () => {
    form.elements.kind.value = button.dataset.composeKind;
    composer.querySelectorAll('[data-compose-kind]').forEach(option => option.setAttribute('aria-pressed', String(option === button)));
  }));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (form.getAttribute('aria-busy') === 'true') return;
    form.querySelectorAll('[aria-invalid]').forEach(field => field.removeAttribute('aria-invalid'));
    form.querySelectorAll('[data-error-for]').forEach(field => { field.textContent = ''; });
    const body = new FormData(form);
    const temporary = pendingTransaction(form);
    setPending(form, true); status.classList.remove('field-error'); status.textContent = 'Saving transaction…';
    try {
      const response = await saveRequest(form.action, { method: 'POST', headers: { Accept: 'application/json' }, body });
      const result = await readResponse(response);
      status.textContent = 'Transaction saved.';
      if (temporary) temporary.remove();
      // Refresh server-computed totals and ledger groups only after the save is confirmed.
      window.location.assign(result.redirect || window.location.href);
    } catch (error) {
      temporary?.remove(); status.classList.add('field-error'); status.textContent = error.message;
      let first;
      Object.entries(error.fields || {}).forEach(([name, messages]) => {
        const input = form.elements.namedItem(name);
        const message = [...form.querySelectorAll('[data-error-for]')].find(item => item.dataset.errorFor === name);
        if (message) message.textContent = Array.isArray(messages) ? messages.join(' ') : String(messages);
        if (input?.setAttribute) { input.setAttribute('aria-invalid', 'true'); first ||= input; }
      });
      if (first) { const details = first.closest('details'); if (details) details.open = true; first.focus(); }
      setPending(form, false);
    }
  });
}
const linkedDialog = document.getElementById('linked-composer');
if (linkedDialog) {
  const form = document.getElementById('linked-form');
  const mode = document.getElementById('linked-mode');
  const status = document.getElementById('linked-status');
  const open = wireDialog(linkedDialog, '[data-linked-close]');
  let recordId;
  const updateMode = () => {
    const split = mode.value === 'split';
    document.getElementById('refund-fields').hidden = split;
    document.getElementById('split-fields').hidden = !split;
    document.querySelectorAll('#refund-fields input').forEach(input => { input.disabled = split; input.required = !split; });
    document.querySelectorAll('#split-fields input,#split-fields select').forEach(input => { input.disabled = !split; input.required = split; });
  };
  mode.addEventListener('change', updateMode);
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-refund]');
    if (!button) return;
    recordId = button.dataset.refund; mode.value = 'refund'; status.textContent = '';
    const splitOption = mode.querySelector('option[value="split"]');
    splitOption.disabled = button.hasAttribute('data-refund-only');
    splitOption.hidden = splitOption.disabled;
    document.getElementById('refund-amount').value = button.dataset.amount;
    document.getElementById('refund-amount').max = button.dataset.amount;
    document.getElementById('linked-original').textContent = `Available to refund: ${button.dataset.amount}`;
    document.getElementById('split-category-1').value = button.dataset.category;
    document.getElementById('split-amount-1').value = button.dataset.amount;
    document.getElementById('split-amount-2').value = '';
    document.getElementById('linked-notes').value = '';
    updateMode(); if (open(button)) document.getElementById('refund-amount').focus();
  });
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (form.getAttribute('aria-busy') === 'true') return;
    const notes = document.getElementById('linked-notes').value;
    const split = mode.value === 'split';
    const payload = split ? {parts: [1, 2].map(part => ({category_id: Number(document.getElementById(`split-category-${part}`).value), amount: document.getElementById(`split-amount-${part}`).value, notes}))} : { amount: document.getElementById('refund-amount').value, date: document.getElementById('refund-date').value, notes };
    setPending(form, true); status.classList.remove('field-error'); status.textContent = 'Saving…';
    try {
      const response = await saveRequest(`/expenses/${encodeURIComponent(recordId)}/${split ? 'split' : 'refund'}`, {method: 'POST', headers: {'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRFToken': form.querySelector('[name="csrf_token"]').value}, body: JSON.stringify(payload)});
      await readResponse(response); window.location.reload();
    } catch (error) { status.classList.add('field-error'); status.textContent = error.message; setPending(form, false); }
  });
}
```

## app/static/js/ledger-filters.js

```javascript
/* Private, bounded snapshots stay in this tab's memory. Server forms remain the fallback. */
(() => {
  const asciiFold = value => String(value || '').replace(/[A-Z]/g, letter => letter.toLowerCase());
  function minorFromInput(value) {
    if (!/^\d+(?:\.\d{1,2})?$/.test(value)) throw new Error('Enter a non-negative amount with at most two decimal places.');
    const [whole, fraction = ''] = value.split('.');
    const amount = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
    if (!Number.isSafeInteger(amount) || amount > 99999999999) throw new Error('Amount exceeds the supported limit.');
    return amount;
  }
  function createIndex(rows) {
    const prepared = rows.map(row => ({...row, notesFold: asciiFold(row.notes), categoryFold: asciiFold(row.category)}));
    const tie = (a, b) => b.id - a.id;
    const compareText = (a, b) => a < b ? -1 : a > b ? 1 : 0;
    const orders = {
      date_desc: (a, b) => compareText(b.date, a.date) || tie(a, b),
      date_asc: (a, b) => compareText(a.date, b.date) || tie(a, b),
      amount_desc: (a, b) => b.amount_minor - a.amount_minor || tie(a, b),
      amount_asc: (a, b) => a.amount_minor - b.amount_minor || tie(a, b),
      category: (a, b) => compareText(a.categoryFold, b.categoryFold) || tie(a, b)
    };
    return Object.fromEntries(Object.entries(orders).map(([key, compare]) => [key, [...prepared].sort(compare)]));
  }
  function select(index, params) {
    const sort = params.get('sort') || 'date_desc';
    if (!Object.hasOwn(index, sort)) throw new Error('Choose a valid sort order.');
    const start = params.get('start') || '', end = params.get('end') || '';
    for (const value of [start, end].filter(Boolean)) {
      const parsed = new Date(value + 'T00:00:00Z');
      if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== value) throw new Error('Enter a valid date.');
    }
    if (start && end && start > end) throw new Error('From date must not be after To date.');
    const min = params.get('min_amount') ? minorFromInput(params.get('min_amount')) : 0;
    const max = params.get('max_amount') ? minorFromInput(params.get('max_amount')) : 99999999999;
    if (min > max) throw new Error('Minimum amount must not exceed maximum amount.');
    const kind = params.get('kind') || '';
    if (kind && !['income', 'expense'].includes(kind)) throw new Error('Choose income or expense.');
    const category = params.get('category'), account = params.get('account');
    const query = asciiFold((params.get('q') || '').slice(0, 100));
    return index[sort].filter(row => (!start || row.date >= start) && (!end || row.date <= end) &&
      (!category || String(row.category_id) === category) && (!account || String(row.account_id) === account) &&
      (!kind || row.kind === kind) && row.amount_minor >= min && row.amount_minor <= max &&
      (!query || row.notesFold.includes(query) || row.categoryFold.includes(query)));
  }
  window.XpenseFilters = {minorFromInput, createIndex, select};
  const root = document.querySelector('#ledger-results');
  if (!root) return;
  const form = document.getElementById('transaction-filters');
  const home = document.getElementById('filter-home');
  const sheet = document.getElementById('transaction-filters-sheet');
  const sheetBody = document.getElementById('filter-sheet-body');
  const status = document.getElementById('local-filter-status');
  const error = document.getElementById('filter-error');
  const pager = document.getElementById('ledger-pagination');
  const tbody = root.querySelector('tbody');
  const mobile = matchMedia('(max-width: 639px)');
  const placeFilters = () => {
    if (mobile.matches) sheetBody.append(form);
    else { window.XpenseDialogs?.close(sheet.id); home.querySelector('.card-body').append(form); }
  };
  document.documentElement.classList.add('filters-enhanced');
  placeFilters(); mobile.addEventListener('change', placeFilters);
  const rows = new Map();
  let snapshot, index, ready = false, dirty = false;
  window.XpenseFilterMetrics = [];
  const paramsFromForm = () => {
    const params = new URLSearchParams();
    for (const [key, value] of new FormData(form)) if (value) params.set(key, value);
    return params;
  };
  const updateExport = params => {
    const link = form.querySelector('[data-filter-export]');
    const url = new URL(link.href); url.search = params.toString(); url.searchParams.delete('page'); link.href = url;
  };
  const dateLabels = new Map();
  const dateFormatter = new Intl.DateTimeFormat('en-GB', {day:'2-digit',month:'short',year:'numeric',timeZone:'UTC'});
  const dateLabel = value => {
    if (dateLabels.has(value)) return dateLabels.get(value);
    if (value === snapshot.today) return 'Today';
    const date = new Date(value + 'T00:00:00Z');
    const yesterday = new Date(snapshot.today + 'T00:00:00Z'); yesterday.setUTCDate(yesterday.getUTCDate() - 1);
    const label = value === yesterday.toISOString().slice(0, 10) ? 'Yesterday' : dateFormatter.format(date);
    dateLabels.set(value, label); return label;
  };
  function render(params, history = true) {
    const began = performance.now();
    try {
      const matches = select(index, params);
      const pages = Math.max(1, Math.ceil(matches.length / 20));
      const page = Math.max(1, Math.min(pages, Number.parseInt(params.get('page'), 10) || 1));
      const fragment = document.createDocumentFragment();
      let date;
      for (const record of matches.slice((page - 1) * 20, page * 20)) {
        if (date !== record.date && ['date_desc', 'date_asc'].includes(params.get('sort') || 'date_desc')) {
          const group = document.createElement('tr'); group.className = 'transaction-group';
          const cell = document.createElement('th'); cell.colSpan = 6; cell.scope = 'rowgroup'; cell.textContent = dateLabel(record.date); group.append(cell); fragment.append(group); date = record.date;
        }
        fragment.append(rows.get(record.id));
      }
      if (!matches.length) {
        const row = document.createElement('tr'), cell = document.createElement('td'); cell.colSpan = 6; cell.className = 'text-center py-4 text-body-secondary'; cell.textContent = 'No transactions match these filters. Clear filters or choose another date.'; row.append(cell); fragment.append(row);
      }
      tbody.replaceChildren(fragment);
      pager.replaceChildren();
      const label = document.createElement('span'); label.className = 'small text-body-secondary'; label.textContent = `${matches.length} records · Page ${page} of ${pages}`; pager.append(label);
      const controls = document.createElement('div'); controls.className = 'd-flex gap-2';
      for (const [text, number] of [['Previous', page - 1], ['Next', page + 1]]) if (number > 0 && number <= pages) {
        const link = document.createElement('a'); link.className = 'btn btn-light btn-sm'; link.textContent = text;
        const next = new URLSearchParams(params); next.set('page', number); link.href = '?' + next;
        link.addEventListener('click', event => { if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return; event.preventDefault(); render(next); }); controls.append(link);
      }
      pager.append(controls); error.textContent = ''; updateExport(params);
      if (history) window.history.replaceState(null, '', '?' + params.toString());
      status.textContent = `${matches.length} matching transactions · Filters ready`;
      const duration = performance.now() - began;
      root.dataset.filterDurationMs = duration.toFixed(3);
      window.XpenseFilterMetrics.push(duration); if (window.XpenseFilterMetrics.length > 100) window.XpenseFilterMetrics.shift();
      document.dispatchEvent(new CustomEvent('ledger:rendered'));
      return true;
    } catch (failure) { error.textContent = failure.message; return false; }
  }
  function apply() { dirty = true; if (ready && form.reportValidity()) render(paramsFromForm()); }
  form.addEventListener('input', event => { dirty = true; if (ready && !['date','number'].includes(event.target.type)) apply(); });
  form.addEventListener('change', apply);
  form.addEventListener('submit', event => {
    if (!ready) return;
    event.preventDefault();
    if (form.reportValidity() && render(paramsFromForm())) window.XpenseDialogs?.close(sheet.id);
  });
  form.querySelector('[data-filter-clear]').addEventListener('click', event => {
    if (!ready || event.ctrlKey || event.metaKey) return;
    event.preventDefault(); form.reset();
    for (const field of form.elements) if (field.name && field.tagName !== 'BUTTON') field.value = field.name === 'sort' ? 'date_desc' : '';
    render(paramsFromForm());
  });
  window.addEventListener('popstate', () => {
    const params = new URLSearchParams(location.search);
    for (const field of form.elements) if (field.name) field.value = params.get(field.name) || (field.name === 'sort' ? 'date_desc' : '');
    if (ready) render(params, false);
  });
  root.addEventListener('submit', async event => {
    const deletion = event.target.closest('form[data-ledger-delete]');
    if (!deletion || !ready) return;
    event.preventDefault(); if (deletion.getAttribute('aria-busy') === 'true' || !window.confirm(deletion.dataset.confirm)) return;
    const row = deletion.closest('tr[data-transaction-id]'), id = Number(row.dataset.transactionId);
    const body = new FormData(deletion); setPending(deletion, true); row.setAttribute('aria-busy','true');
    status.textContent = 'Deleting transaction…';
    try {
      const response = await saveRequest(deletion.action, {method:'POST',headers:{Accept:'application/json'},body});
      const result = await readResponse(response);
      if (result.id !== id) throw new Error('Could not confirm the deletion. Reload your ledger before trying again.');
      await window.XpenseMotion?.remove(row);
      snapshot.rows = snapshot.rows.filter(record => record.id !== id); rows.delete(id); index = createIndex(snapshot.rows); render(paramsFromForm());
      status.textContent = 'Transaction deleted.';
    } catch (failure) { row.removeAttribute('aria-busy'); setPending(deletion, false); error.textContent = failure.message; status.textContent = 'Transaction was not confirmed deleted. Reload to check its status.'; }
  });
  async function prepare() {
    status.textContent = 'Preparing fast filters…';
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(root.dataset.snapshotUrl, {cache:'no-store',headers:{Accept:'application/json'},signal:controller.signal});
      if (!response.ok) throw new Error('Snapshot unavailable');
      const data = await response.json();
      if (!data.complete) { status.textContent = 'Use Apply filters to search this ledger.'; return; }
      if (!data.rows.length) { status.textContent = 'Add a transaction to start filtering.'; return; }
      const template = document.createElement('template'); template.innerHTML = data.html;
      // This markup comes only from our same-origin, escaping Jinja row template.
      template.content.querySelectorAll('tr[data-transaction-id]').forEach(row => {
        row.querySelectorAll('form[data-confirm]').forEach(form => { form.dataset.asyncForm = ''; form.dataset.ledgerDelete = ''; });
        rows.set(Number(row.dataset.transactionId), row);
      });
      if (data.rows.length !== rows.size) throw new Error('Incomplete ledger markup');
      snapshot = data; index = createIndex(data.rows); ready = true; render(dirty ? paramsFromForm() : new URLSearchParams(location.search), dirty);
    } catch (_) { status.textContent = 'Fast filters unavailable. Apply filters still works.'; }
    finally { clearTimeout(timeout); }
  }
  prepare();
})();
```

## app/templates/base.html

```html
<!DOCTYPE html>
<html lang="en" data-bs-theme="light">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="theme-color" content="#fafafa">
    <meta name="application-name" content="Xpense">
    <link rel="icon" href="{{ url_for('static', filename='img/xpense-mark.svg') }}" type="image/svg+xml">
    <title>{{ title|default("Xpense") }} · Xpense</title>
    <script src="{{ url_for('static', filename='js/theme.js') }}"></script>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
    <link href="{{ url_for('static', filename='css/style.css') }}" rel="stylesheet">
  </head>
  <body>
    <a class="visually-hidden-focusable" href="#main">Skip to content</a>
    {% if current_user.is_authenticated %}
      <aside class="app-sidebar" id="app-sidebar" aria-label="Primary navigation">
        <a class="brand" href="{{ url_for('reports.dashboard') }}">
          <span class="brand-mark"><i class="bi bi-graph-up-arrow" aria-hidden="true"></i></span>
          <span><span class="brand-name">Xpense</span><span class="brand-subtitle">Personal ledger</span></span>
        </a>
        <div class="nav-section">Workspace</div>
        <nav class="sidebar-nav">
          {% for endpoint, label, icon in [('reports.dashboard','Overview','bi-grid-1x2-fill'),('expenses.index','Transactions','bi-arrow-left-right'),('accounts.index','Accounts','bi-wallet2'),('expenses.budgets','Budgets','bi-bullseye'),('expenses.bills','Credit card bills','bi-credit-card-2-front'),('expenses.recurring','Recurring','bi-arrow-repeat'),('investments.index','Investments','bi-graph-up-arrow'),('insights.commitments','Commitments','bi-collection'),('expenses.categories','Categories','bi-tags')] %}
            <a class="sidebar-link neu-nav-item {{ 'active' if request.endpoint == endpoint else '' }}" href="{{ url_for(endpoint) }}" title="{{ label }}" {% if request.endpoint == endpoint %}aria-current="page"{% endif %}>
              <i class="bi {{ icon }}" aria-hidden="true"></i><span>{{ label }}</span>
            </a>
          {% endfor %}
        </nav>
        <div class="nav-section mt-4">Insights</div>
        <nav class="sidebar-nav">
          <a class="sidebar-link neu-nav-item {{ 'active' if request.endpoint == 'assistant.index' else '' }}" href="{{ url_for('assistant.index') }}" title="Xpense AI"><i class="bi bi-stars" aria-hidden="true"></i><span>Xpense AI</span></a>
          <a class="sidebar-link neu-nav-item {{ 'active' if request.endpoint == 'insights.insights' else '' }}" href="{{ url_for('insights.insights') }}" title="Smart insights"><i class="bi bi-lightbulb" aria-hidden="true"></i><span>Smart insights</span></a>
          <a class="sidebar-link {{ 'active' if request.endpoint == 'insights.notifications' else '' }}" href="{{ url_for('insights.notifications') }}" title="Notifications"><i class="bi bi-bell" aria-hidden="true"></i><span>Notifications{% if unread_notifications %}<span class="badge rounded-pill text-bg-danger ms-auto">{{ unread_notifications }}</span>{% endif %}</span></a>
          <a class="sidebar-link {{ 'active' if request.endpoint == 'insights.calendar_view' else '' }}" href="{{ url_for('insights.calendar_view') }}" title="Bill calendar"><i class="bi bi-calendar3" aria-hidden="true"></i><span>Bill calendar</span></a>
          <a class="sidebar-link {{ 'active' if request.endpoint == 'insights.goals' else '' }}" href="{{ url_for('insights.goals') }}" title="Savings goals"><i class="bi bi-stars" aria-hidden="true"></i><span>Savings goals</span></a>
          <a class="sidebar-link {{ 'active' if request.endpoint == 'reports.report' else '' }}" href="{{ url_for('reports.report', period='monthly') }}" title="Reports"><i class="bi bi-bar-chart-line" aria-hidden="true"></i><span>Reports</span></a>
          <a class="sidebar-link" href="{{ url_for('expenses.import_csv') }}" title="Import data"><i class="bi bi-cloud-arrow-up" aria-hidden="true"></i><span>Import data</span></a>
        </nav>
        <div class="sidebar-footer">
          <a class="btn btn-primary w-100 mb-3 d-flex align-items-center justify-content-center gap-2" data-compose href="{{ url_for('expenses.add') }}"><i class="bi bi-plus-lg" aria-hidden="true"></i><span>Add transaction</span><kbd class="shortcut" aria-hidden="true">C</kbd></a>
          <a class="profile-link" href="{{ url_for('auth.profile') }}">
            <span class="avatar">{{ current_user.name[:1]|upper }}</span>
            <span class="profile-name">{{ current_user.name }}<span class="profile-email">{{ current_user.email }}</span></span>
            <i class="bi bi-chevron-right ms-auto text-body-secondary" aria-hidden="true"></i>
          </a>
          <div class="dropdown mt-2">
            <button class="btn btn-light theme-button dropdown-toggle" data-bs-toggle="dropdown" type="button" aria-expanded="false"><i class="bi bi-bell me-1" aria-hidden="true"></i>Recent alerts{% if unread_notifications %}<span class="badge text-bg-danger ms-1">{{ unread_notifications }}</span>{% endif %}</button>
            <div class="dropdown-menu dropdown-menu-dark w-100 p-2">
              {% for item in recent_notifications %}<a class="dropdown-item rounded small text-wrap" href="{{ url_for('insights.notifications') }}"><strong>{{ item.title }}</strong><br><span class="opacity-75">{{ item.message }}</span></a>{% else %}<span class="dropdown-item-text small">No notifications yet.</span>{% endfor %}
              <a class="dropdown-item small text-primary mt-1" href="{{ url_for('insights.notifications') }}">View all notifications</a>
            </div>
          </div>
          <button id="theme-toggle" class="btn btn-light theme-button" type="button" aria-label="Toggle dark mode" aria-pressed="false"><i class="bi bi-moon-stars" aria-hidden="true"></i><span>Dark mode</span></button>
          <button id="privacy-toggle" class="btn btn-light theme-button" type="button" aria-label="Toggle privacy mode" aria-pressed="false"><i class="bi bi-eye-slash" aria-hidden="true"></i><span>Privacy mode</span></button>
          <button id="sidebar-collapse" class="btn btn-light theme-button sidebar-collapse-button" type="button" aria-label="Collapse sidebar" aria-pressed="false"><i class="bi bi-layout-sidebar-inset" aria-hidden="true"></i><span>Collapse sidebar</span></button>
          <form method="post" action="{{ url_for('auth.logout') }}" class="mt-2">
            {{ action_form.hidden_tag() }}<button class="btn btn-sm btn-link text-body-secondary w-100" type="submit"><i class="bi bi-box-arrow-right me-1" aria-hidden="true"></i>Log out</button>
          </form>
        </div>
      </aside>
      <header class="mobile-header">
        <a class="brand" href="{{ url_for('reports.dashboard') }}"><span class="brand-mark"><i class="bi bi-graph-up-arrow" aria-hidden="true"></i></span><span class="brand-name">Xpense</span></a>
        <button class="mobile-menu-toggle" id="mobile-menu-toggle" type="button" aria-controls="app-sidebar" aria-expanded="false" aria-label="Open navigation"><i class="bi bi-list" aria-hidden="true"></i></button>
      </header>
    {% endif %}
    <div class="{{ 'app-main' if current_user.is_authenticated else '' }}">
      <main id="main" class="container-fluid content-shell py-4 py-lg-5 page-enter">
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% for category, message in messages %}
            <div class="alert alert-{{ category if category in ['success','warning','danger','info'] else 'info' }} alert-dismissible fade show toast-message" role="status">
              <i class="bi bi-{{ 'check-circle' if category == 'success' else 'exclamation-triangle' if category in ['warning','danger'] else 'info-circle' }} me-2" aria-hidden="true"></i>{{ message }}<button class="btn-close" data-bs-dismiss="alert" aria-label="Dismiss"></button>
            </div>
          {% endfor %}
        {% endwith %}
        {% block content %}{% endblock %}
      </main>
      <footer class="container-fluid content-shell site-footer pb-4"><i class="bi bi-shield-check me-1" aria-hidden="true"></i>Private by design · Xpense · Your money, clearly.</footer>
    </div>
    {% if current_user.is_authenticated %}{% include 'expenses/composer.html' %}{% endif %}
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
    <script src="{{ url_for('static', filename='js/motion.js') }}" defer></script>
    <script src="{{ url_for('static', filename='js/app.js') }}" defer></script>
    {% block scripts %}{% endblock %}
  </body>
</html>
```

## app/templates/macros.html

```html
{% macro render_form(form, button='Save', multipart=false, dialog=false) %}
  <form method="post" class="{{ 'dialog-form' if dialog else '' }}"
        {% if multipart %}enctype="multipart/form-data"{% endif %}>
    {{ form.hidden_tag() }}
    {% for field in form if field.type not in ['CSRFTokenField','HiddenField','SubmitField'] and field.name not in ['logo_key','icon'] %}
      <div class="mb-3 form-field field-{{ field.name }} {{ 'dialog-optional-field' if dialog and field.name in ['last_four','color','linked_account_id','active','notes','destination_amount','fee'] else '' }}" data-field-name="{{ field.name }}">
        {% if field.type == "BooleanField" %}
          <div class="form-check">{{ field(class='form-check-input') }}{{ field.label(class='form-check-label') }}</div>
        {% else %}
          {{ field.label(class='form-label') }}
          {% if field.type == "SelectField" %}{{ field(class='form-select neu-input' + (' is-invalid' if field.errors else '')) }}
          {% elif field.type == "DecimalField" %}{{ field(class='form-control neu-input' + (' is-invalid' if field.errors else ''), step='0.01', min='0.01', max='999999999.99') }}
          {% elif field.type == "PasswordField" %}{{ field(class='form-control neu-input' + (' is-invalid' if field.errors else ''), autocomplete='current-password' if field.name in ['current_password','password'] and request.endpoint in ['auth.login','auth.profile','auth.delete_account'] else 'new-password') }}
        {% elif field.name == 'provider' %}{{ field(class='form-control neu-input' + (' is-invalid' if field.errors else ''), list='provider-options') }}
        {% else %}{{ field(class='form-control neu-input' + (' is-invalid' if field.errors else '')) }}{% endif %}
      {% endif %}
      {% for error in field.errors %}
        <div class="invalid-feedback d-block mt-1"><i class="bi bi-exclamation-circle me-1" aria-hidden="true"></i>{{ error }}</div>
      {% endfor %}
      </div>
    {% endfor %}
    <button class="btn btn-primary neu-button px-4" type="submit"><i class="bi bi-check2 me-1" aria-hidden="true"></i>{{ button }}</button>
  </form>
  {% if form.provider is defined %}<datalist id="provider-options">{% for provider in banks|default([]) %}<option value="{{ provider }}"></option>{% endfor %}{% for provider in wallets|default([]) %}<option value="{{ provider }}"></option>{% endfor %}</datalist>{% endif %}
{% endmacro %}
{% macro delete_button(action, message='Delete this record?') %}
  <form method="post"
        action="{{ action }}"
        class="d-inline"
        data-confirm="{{ message }}">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    <button class="btn btn-sm btn-outline-danger" type="submit">Delete</button>
  </form>
{% endmacro %}
{% macro money_value(value, currency=none, size='compact') %}
  <span class="money-value money-value--{{ size }}" role="text">{{ (value|money_currency(currency)) if currency else (value|money) }}</span>
{% endmacro %}
{% macro summary_cards(summary, month_spend=none) %}
  <section class="executive-summary" aria-label="Ledger summary">
    {% for label,value in [('Lifetime income',summary.income),('Lifetime spending',summary.expense),('Net cash flow',summary.balance)] %}
    <div class="summary-metric"><span class="metric-label">{{ label }}</span><strong class="metric-value {{ 'text-success' if loop.first else '' }}">{{ money_value(value) }}</strong></div>
    {% endfor %}
    {% if month_spend is not none %}<div class="summary-metric"><span class="metric-label">This month's spending</span><strong class="metric-value">{{ money_value(month_spend) }}</strong></div>{% endif %}
  </section>
{% endmacro %}
{% macro transaction_table(rows, editable=false) %}
<div class="table-responsive">
<table class="table transaction-table align-middle mb-0">
  <caption class="visually-hidden">Transactions with native currency amounts. Refunds reduce spending.</caption>
  <thead><tr><th scope="col">Date</th><th scope="col">Transaction</th><th scope="col" class="payment-column">Payment</th><th scope="col">Type</th><th scope="col" class="text-end">Amount</th>{% if editable %}<th scope="col" class="text-end">Actions</th>{% endif %}</tr></thead>
  <tbody>
  {% set group = namespace(date=none) %}
  {% for row in rows %}
    {% if group.date != row.date and request.args.get('sort','date_desc') in ['date_desc','date_asc'] %}
    <tr class="transaction-group"><th scope="rowgroup" colspan="{{ 6 if editable else 5 }}">{% if row.date == today %}Today{% elif (today - row.date).days == 1 %}Yesterday{% else %}{{ row.date.strftime('%d %b %Y') }}{% endif %}</th></tr>
    {% set group.date = row.date %}{% endif %}
    <tr data-transaction-id="{{ row.id }}">
      <td class="text-nowrap"><time datetime="{{ row.date }}">{{ row.date.strftime('%d %b') }}</time></td>
      <td class="transaction-description"><span class="category-pill">{{ row.category.name }}</span><span class="transaction-note">{{ row.notes or ('Income received' if row.kind == 'income' else 'Expense recorded') }}</span>{% if row.account %}<span class="account-caption">{{ row.account.name }}</span>{% endif %}<span class="mobile-transaction-kind">{{ "Refund" if row.entry_type == "refund" else row.entry_type|title if row.entry_type != "normal" else row.kind|title }}</span></td>
      <td class="payment-column text-body-secondary">{{ row.payment_method }}</td>
      <td><span class="transaction-kind {{ 'text-success' if row.kind == 'income' else '' }}">{{ 'Refund' if row.entry_type == 'refund' else row.entry_type|title if row.entry_type != 'normal' else row.kind|title }}</span></td>
      <td class="text-end text-nowrap">{{ money_value(row.amount_minor, row.currency or 'INR') }}</td>
      {% if editable %}<td class="text-end text-nowrap">
      {% if row.entry_type == 'normal' and not row.transfer_id and not row.parent_id and not row.refund_of_id %}
        {% if row.id not in refund_totals %}<a class="btn btn-sm btn-light" href="{{ url_for('expenses.edit',record_id=row.id) }}">Edit</a>{% endif %}
        {% if row.kind == 'expense' and row.amount_minor > refund_totals.get(row.id,0) %}<button class="btn btn-sm btn-light" type="button" data-refund="{{ row.id }}" data-amount="{{ '%.2f'|format((row.amount_minor - refund_totals.get(row.id,0)) / 100) }}" data-currency="{{ row.currency or 'INR' }}" {% if row.id in refund_totals %}data-refund-only{% endif %} data-category="{{ row.category_id }}">Refund / split</button>{% endif %}
        {% if row.id not in refund_totals %}{{ delete_button(url_for('expenses.delete',record_id=row.id)) }}{% endif %}
      {% else %}<span class="text-body-secondary small">Linked entry</span>{% if row.entry_type == 'normal' and row.kind == 'expense' and row.parent_id and row.amount_minor > refund_totals.get(row.id,0) %}<button class="btn btn-sm btn-light" type="button" data-refund="{{ row.id }}" data-amount="{{ '%.2f'|format((row.amount_minor - refund_totals.get(row.id,0)) / 100) }}" data-currency="{{ row.currency or 'INR' }}" data-refund-only data-category="{{ row.category_id }}">Refund</button>{% endif %}{% endif %}
      </td>{% endif %}
    </tr>
  {% else %}
    <tr><td colspan="{{ 6 if editable else 5 }}"><div class="empty-state"><strong>Your ledger starts here</strong><p>Add your first expense or explore with sample transactions.</p><a class="btn btn-primary" data-compose href="{{ url_for('expenses.add') }}">Add expense</a><form method="post" action="{{ '/expenses/sample-data' }}">{{ action_form.hidden_tag() }}<button class="btn btn-light" type="submit">Load Sample Data</button></form></div></td></tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% endmacro %}
```

## app/templates/expenses/composer.html

```html
<dialog id="expense-composer" class="composer-dialog" aria-labelledby="compose-title" aria-keyshortcuts="c Meta+k Control+k">
  <div class="sheet-handle" data-sheet-handle aria-hidden="true"><span></span></div>
  <div class="dialog-header"><h2 id="compose-title" class="mb-0">Add transaction</h2><button type="button" class="btn-close" data-compose-close aria-label="Close transaction form"></button></div>
  <form id="compose-form" class="dialog-body" method="post" action="{{ url_for('expenses.add') }}" data-async-form>
    {{ action_form.hidden_tag() }}
    <div class="composer-type" aria-label="Transaction type"><button type="button" data-compose-kind="expense" aria-pressed="true">Expense</button><button type="button" data-compose-kind="income" aria-pressed="false">Income</button></div>
    <input type="hidden" name="kind" value="expense">
    <label id="compose-amount-label" for="compose-amount" class="form-label mt-3">Amount · INR</label><input id="compose-amount" class="composer-amount" name="amount" type="number" inputmode="decimal" step="0.01" min="0.01" max="999999999.99" placeholder="0.00" required autocomplete="off" aria-describedby="compose-amount-error"><span id="compose-amount-error" class="field-error" data-error-for="amount"></span>
    <div class="composer-fields">
      <div><label class="form-label" for="compose-category">Category</label><select id="compose-category" class="form-select" name="category_id" required aria-describedby="compose-category-error"><option value="">Choose category</option>{% for id,name in composer_categories %}<option value="{{ id }}">{{ name }}</option>{% endfor %}</select><span id="compose-category-error" class="field-error" data-error-for="category_id"></span></div>
      <div><label class="form-label" for="compose-date">Date</label><input id="compose-date" class="form-control" name="date" type="date" value="{{ today }}" required aria-describedby="compose-date-error"><span id="compose-date-error" class="field-error" data-error-for="date"></span></div>
      <div><label class="form-label" for="compose-account">Account</label><select id="compose-account" class="form-select" name="account_id" aria-describedby="compose-account-error"><option value="0">No account · INR</option>{% for id,name in composer_accounts %}<option value="{{ id }}">{{ name }}</option>{% endfor %}</select><span id="compose-account-error" class="field-error" data-error-for="account_id"></span></div>
    </div>
    <details class="dialog-more mt-3"><summary>Notes & payment method</summary><label class="form-label mt-3" for="compose-notes">Notes</label><textarea id="compose-notes" class="form-control" name="notes" rows="2" maxlength="500" aria-describedby="compose-notes-error"></textarea><span id="compose-notes-error" class="field-error" data-error-for="notes"></span><label class="form-label mt-3" for="compose-payment">Payment method</label><select id="compose-payment" name="payment_method" class="form-select">{% for payment in ['Cash','UPI','Debit card','Credit card','Bank transfer','Other'] %}<option>{{ payment }}</option>{% endfor %}</select><span class="field-error" data-error-for="payment_method"></span></details>
    <div class="composer-footer"><span id="compose-status" role="status" aria-live="polite" class="composer-status"></span><button type="submit" class="btn btn-primary">Save transaction</button></div>
  </form>
</dialog>
<dialog id="linked-composer" class="composer-dialog" aria-labelledby="linked-title">
  <div class="sheet-handle" data-sheet-handle aria-hidden="true"><span></span></div>
  <div class="dialog-header"><h2 id="linked-title" class="mb-0">Refund or split expense</h2><button class="btn-close" type="button" data-linked-close aria-label="Close refund or split form"></button></div>
  <form id="linked-form" class="dialog-body" data-async-form>
    {{ action_form.hidden_tag() }}
    <label class="form-label" for="linked-mode">Action</label><select id="linked-mode" class="form-select"><option value="refund">Record refund</option><option value="split">Split into two categories</option></select>
    <p id="linked-original" class="small text-body-secondary mt-2"></p>
    <div id="refund-fields"><label class="form-label" for="refund-amount">Refund amount</label><input id="refund-amount" class="form-control" type="number" step="0.01" min="0.01" required><label class="form-label mt-3" for="refund-date">Refund date</label><input id="refund-date" class="form-control" type="date" value="{{ today }}" required></div>
    <div id="split-fields" hidden>{% for part in [1,2] %}<fieldset class="mt-3"><legend class="h6">Category {{ part }}</legend><label class="form-label" for="split-category-{{ part }}">Category</label><select id="split-category-{{ part }}" class="form-select"><option value="">Choose category</option>{% for id,name in composer_categories %}<option value="{{ id }}">{{ name }}</option>{% endfor %}</select><label class="form-label mt-2" for="split-amount-{{ part }}">Amount</label><input id="split-amount-{{ part }}" type="number" class="form-control" min="0.01" step="0.01"></fieldset>{% endfor %}<p class="small text-body-secondary mt-2">Both amounts must add up to the original expense.</p></div>
    <label class="form-label mt-3" for="linked-notes">Notes</label><textarea id="linked-notes" class="form-control" maxlength="500" rows="2"></textarea>
    <div class="composer-footer"><span id="linked-status" role="status" aria-live="polite"></span><button type="submit" class="btn btn-primary">Save</button></div>
  </form>
</dialog>
```

## app/templates/expenses/index.html

```html
{% extends "base.html" %}
{% from 'macros.html' import transaction_table with context %}
{% block content %}
  <div class="page-heading">
    <div><div class="eyebrow">YOUR LEDGER</div><h1>Transactions</h1><p>Search, review, and keep every inflow and outflow in one calm workspace.</p></div>
    <div class="d-flex flex-wrap gap-2 mobile-actions"><a class="btn btn-light" href="{{ url_for('expenses.import_csv') }}"><i class="bi bi-cloud-arrow-up me-1" aria-hidden="true"></i>Import</a><a data-compose class="btn btn-primary" href="{{ url_for('expenses.add') }}"><i class="bi bi-plus-lg me-1" aria-hidden="true"></i>Add transaction</a></div>
  </div>
  <button type="button" class="btn btn-light mobile-filter-trigger mb-3" data-filter-open="transaction-filters-sheet">Filter transactions</button>
  <div id="filter-home">  <div class="card mb-4">
    <div class="card-body">
      <form method="get" id="transaction-filters" data-async-form class="row g-3 align-items-end">
        <div class="col-12 col-lg-4"><label class="form-label" for="q">Search transactions</label><div class="input-group"><span class="input-group-text bg-transparent border-end-0"><i class="bi bi-search" aria-hidden="true"></i></span><input class="form-control border-start-0" name="q" id="q" value="{{ request.args.get('q', '') }}" maxlength="100" placeholder="Search notes or categories…"></div></div>
        <div class="col-6 col-lg-2"><label for="kind" class="form-label">Type</label><select name="kind" id="kind" class="form-select"><option value="">All types</option>{% for val in ['income','expense'] %}<option value="{{ val }}" {{ 'selected' if request.args.get("kind") == val }}>{{ val|title }}</option>{% endfor %}</select></div>
        <div class="col-6 col-lg-3"><label for="category" class="form-label">Category</label><select name="category" id="category" class="form-select"><option value="">All categories</option>{% for id,name in categories %}<option value="{{ id }}" {{ 'selected' if request.args.get("category") == id|string }}>{{ name }}</option>{% endfor %}</select></div>
        <div class="col-6 col-lg-3"><label for="account" class="form-label">Account</label><select name="account" id="account" class="form-select"><option value="">All accounts</option>{% for id,name in accounts %}<option value="{{ id }}" {{ 'selected' if request.args.get("account") == id|string }}>{{ name }}</option>{% endfor %}</select></div>
        <div class="col-12 col-lg-3"><label for="sort" class="form-label">Sort by</label><select class="form-select" id="sort" name="sort">{% for val,label in [('date_desc','Newest first'),('date_asc','Oldest first'),('amount_desc','Highest amount'),('amount_asc','Lowest amount'),('category','Category A–Z')] %}<option value="{{ val }}" {{ 'selected' if request.args.get('sort', 'date_desc') == val }}>{{ label }}</option>{% endfor %}</select></div>
        <div class="col-6 col-lg-2"><label class="form-label" for="start">From date</label><input type="date" class="form-control" id="start" name="start" value="{{ request.args.get('start','') }}"></div>
        <div class="col-6 col-lg-2"><label class="form-label" for="end">To date</label><input type="date" class="form-control" id="end" name="end" value="{{ request.args.get('end','') }}"></div>
        <div class="col-6 col-lg-2"><label class="form-label" for="min_amount">Min amount</label><input type="number" step="0.01" min="0" class="form-control" id="min_amount" name="min_amount" value="{{ request.args.get('min_amount','') }}"></div>
        <div class="col-6 col-lg-2"><label class="form-label" for="max_amount">Max amount</label><input type="number" step="0.01" min="0" class="form-control" id="max_amount" name="max_amount" value="{{ request.args.get('max_amount','') }}"></div>
        <p id="filter-error" class="field-error mb-0" role="status"></p>
        <div class="col-12 col-lg-4 d-flex gap-2"><button class="btn btn-primary"><i class="bi bi-funnel me-1" aria-hidden="true"></i>Apply filters</button><a href="{{ url_for('expenses.index') }}" class="btn btn-light" data-filter-clear>Clear</a><a class="btn btn-light ms-auto" href="{{ url_for('reports.export_csv', **request.args) }}" data-filter-export aria-label="Export filtered transactions"><i class="bi bi-download" aria-hidden="true"></i></a></div>
      </form>
    </div>
  </div>
</div>
  <dialog id="transaction-filters-sheet" class="filter-dialog" aria-labelledby="filter-title">
    <div class="sheet-handle" data-sheet-handle aria-hidden="true"><span></span></div>
    <div class="dialog-header"><h2 id="filter-title">Filter transactions</h2><button type="button" class="btn-close" data-filter-close aria-label="Close filters"></button></div>
    <div id="filter-sheet-body" class="dialog-body"></div>
  </dialog>
  <p class="small text-body-secondary" id="local-filter-status" role="status" aria-live="polite"></p>
  <div id="ledger-results" data-snapshot-url="{{ url_for('expenses.local_data') }}">
  <div class="card overflow-hidden">{{ transaction_table(page.items, true) }}</div>
  <div id="ledger-pagination" class="d-flex justify-content-between align-items-center mt-3"><span class="small text-body-secondary">{{ page.total }} records · Page {{ page.page }} of {{ page.pages or 1 }}</span><div class="d-flex gap-2">{% if page.has_prev %}<a class="btn btn-light btn-sm" href="{{ url_for('expenses.index', **dict(request.args, page=page.prev_num)) }}">Previous</a>{% endif %}{% if page.has_next %}<a class="btn btn-light btn-sm" href="{{ url_for('expenses.index', **dict(request.args, page=page.next_num)) }}">Next</a>{% endif %}</div></div>
  </div>
{% endblock %}
{% block scripts %}<script src="{{ url_for('static', filename='js/ledger-filters.js') }}" defer></script>{% endblock %}
```

## app/templates/expenses/snapshot.html

```html
{% from 'macros.html' import transaction_table with context %}
{{ transaction_table(rows, true) }}
```

## app/templates/expenses/recurring.html

```html
{% extends "base.html" %}
{% from 'macros.html' import render_form, delete_button with context %}
{% block content %}
  <div class="page-heading">
    <div>
      <div class="eyebrow">PLAN AHEAD</div>
      <h1>Recurring expenses</h1>
    </div>
    <form method="post" action="{{ url_for('expenses.generate_recurring') }}">
      {{ action_form.hidden_tag() }}<button class="btn btn-primary"><i class="bi bi-arrow-repeat me-1" aria-hidden="true"></i>Generate due expenses</button>
    </form>
  </div>
  <p class="text-body-secondary">Create rules for rent and subscriptions. Generate overdue entries here or run the daily scheduled command described in the README.</p>
  <div class="row g-4">
    <div class="col-lg-4">
      <div class="card">
        <div class="card-body p-4">
          <h2 class="h5 mb-3">New recurring expense</h2>
          {{ render_form(form, 'Create rule') }}
        </div>
      </div>
    </div>
    <div class="col-lg-8">
      {% for rule in rules %}
        <div class="card mb-3">
          <div class="card-body">
            <div class="d-flex justify-content-between gap-3">
              <h2 class="h5">{{ rule.category.name }}</h2>
              <strong>{{ rule.amount_minor|money_currency(rule.account.currency if rule.account else 'INR') }}</strong>
            </div>
            <p class="text-body-secondary mb-2">{{ rule.frequency|title }} · Next due {{ rule.next_due }} · {{ rule.payment_method }}</p>
            <p class="note-text">{{ rule.notes }}</p>
            <div class="d-flex align-items-center gap-2">
              <span class="badge {{ 'text-bg-success' if rule.status == 'active' else 'text-bg-secondary' }} me-auto">{{ rule.status|title }}</span>
              <a class="btn btn-sm btn-outline-secondary"
                 href="{{ url_for('expenses.edit_recurring', record_id=rule.id) }}">Edit / pause</a>{{ delete_button(url_for('expenses.delete_recurring', record_id=rule.id), 'Delete this rule? Previously generated transactions will remain.') }}
              {% if rule.status != 'cancelled' %}<form method="post" action="{{ url_for('expenses.cancel_recurring', record_id=rule.id) }}" class="d-inline">{{ action_form.hidden_tag() }}<button class="btn btn-sm btn-outline-warning" type="submit">Cancel</button></form>{% endif %}
            </div>
          </div>
        </div>
      {% else %}
        <div class="card">
          <div class="card-body py-5 text-center text-body-secondary">No recurring rules yet.</div>
        </div>
      {% endfor %}
    </div>
  </div>
{% endblock %}
```

## app/templates/insights/calendar.html

```html
{% extends "base.html" %}
{% block content %}
  <div class="page-heading"><div><div class="eyebrow">UPCOMING COMMITMENTS</div><h1>Bill calendar</h1><p>Recurring payments and manual bills in one view.</p></div><form method="get" class="d-flex gap-2"><input class="form-control" type="month" name="month" value="{{ month.strftime('%Y-%m') }}"><button class="btn btn-primary">View</button></form></div>
  <div class="row g-3 mb-4"><div class="col-sm-4"><div class="card"><div class="card-body"><span class="metric-label">Due today</span><strong class="metric-value d-block">{{ due_today|length }}</strong></div></div></div><div class="col-sm-4"><div class="card"><div class="card-body"><span class="metric-label">Due within 7 days</span><strong class="metric-value d-block">{{ due_soon|length }}</strong></div></div></div><div class="col-sm-4"><div class="card"><div class="card-body"><span class="metric-label">Overdue</span><strong class="metric-value d-block">{{ overdue|length }}</strong></div></div></div></div>
  <div class="alert alert-info small"><i class="bi bi-repeat me-1"></i>Active recurring cost estimate: <strong>{{ recurring_monthly_total|money }}/month</strong> · <strong>{{ recurring_annual_total|money }}/year</strong>. Estimates use the rule frequency and do not include one-off bills.</div>
  <div class="row g-3">{% for day in days %}{% set bill_items = bills|selectattr('due_date.day', 'equalto', day)|list %}{% set commitment_day_items = commitment_items|selectattr('due_date.day', 'equalto', day)|list %}<div class="col-6 col-md-4 col-xl-3"><div class="card h-100"><div class="card-body"><div class="d-flex justify-content-between"><strong>{{ month.strftime('%b') }} {{ day }}</strong>{% if day == today.day and month.month == today.month and month.year == today.year %}<span class="badge text-bg-success">Today</span>{% endif %}</div>{% for bill in bill_items %}<div class="small border-top mt-3 pt-2"><strong>{{ bill.card_name }}</strong><br>{{ bill.amount_minor|money }} · {{ 'Paid' if bill.paid else 'Unpaid' }}</div>{% endfor %}{% for item in commitment_day_items %}<div class="small border-top mt-3 pt-2"><strong>{{ item.commitment.name }}</strong><br>{{ item.expected_minor|money_currency(item.commitment.account.currency if item.commitment.account else 'INR') }} · {{ item.status|title }}</div>{% endfor %}</div></div></div>{% endfor %}</div>
  <div class="card mt-4"><div class="card-body"><h2 class="card-title">Recurring occurrences</h2>{% for occurrence in occurrences %}<div class="d-flex align-items-center gap-2 border-top py-3"><span class="flex-grow-1"><strong>{{ occurrence.recurring.category.name }}</strong> · {{ occurrence.due_date }} · {{ occurrence.recurring.amount_minor|money_currency(occurrence.recurring.account.currency if occurrence.recurring.account else 'INR') }}</span><span class="badge text-bg-{{ 'success' if occurrence.status == 'paid' else 'secondary' }}">{{ occurrence.status|title }}</span>{% if occurrence.status == 'scheduled' %}<form method="post" action="{{ url_for('insights.occurrence_action', record_id=occurrence.id, action='paid') }}">{{ action_form.hidden_tag() }}<button class="btn btn-sm btn-light">Paid</button></form><form method="post" action="{{ url_for('insights.occurrence_action', record_id=occurrence.id, action='skip') }}">{{ action_form.hidden_tag() }}<button class="btn btn-sm btn-light">Skip</button></form>{% endif %}</div>{% else %}<div class="empty-state"><div class="empty-illustration"><i class="bi bi-calendar3"></i></div><strong>No generated occurrences this month</strong><p>Generate due expenses from the Recurring page to populate the calendar.</p></div>{% endfor %}</div></div>
{% endblock %}
```

## app/templates/commitments/index.html

```html
{% extends "base.html" %}
{% from 'macros.html' import render_form, delete_button with context %}
{% block content %}
  <div class="page-heading">
    <div><div class="eyebrow">FINANCIAL COMMITMENTS HUB</div><h1>Commitments</h1><p>Loans, bills, subscriptions, and future payments in one view. Estimates do not change your ledger until you confirm a payment.</p></div>
    <a class="btn btn-light" href="{{ url_for('insights.calendar_view') }}"><i class="bi bi-calendar3 me-1"></i>Calendar</a>
  </div>
  {% if summary.unconverted_count %}<p class="small text-warning" role="status">{{ summary.unconverted_count }} commitments excluded from INR totals: no cached conversion rate.</p>{% endif %}
  <div class="row g-3 mb-4">
    {% for label, value, icon in [('Due this month', summary.due_month, 'bi-calendar2-check'), ('Already paid', summary.paid_month, 'bi-check-circle'), ('Remaining', summary.remaining, 'bi-hourglass-split'), ('Overdue', summary.overdue, 'bi-exclamation-triangle')] %}
      <div class="col-6 col-xl-3"><div class="card"><div class="card-body"><div class="d-flex justify-content-between"><span class="metric-label">{{ label }}</span><span class="metric-icon"><i class="bi {{ icon }}"></i></span></div><strong class="metric-value d-block">{{ value|money }}</strong></div></div></div>
    {% endfor %}
  </div>
  <div class="row g-4">
    <div class="col-xl-4">
      <div class="card"><div class="card-body"><h2 class="card-title">Add commitment</h2><p class="small text-body-secondary">For loans, select Loan / EMI and complete the loan fields too.</p>{{ render_form(form, 'Save commitment') }}</div></div>
    </div>
    <div class="col-xl-8">
      <div class="card mb-4"><div class="card-body">
        <div class="d-flex justify-content-between align-items-center"><div><h2 class="card-title mb-1">Upcoming payments</h2><p class="small text-body-secondary mb-0">Next payment: {% if summary.next %}{{ summary.next.due_date }} · {{ summary.next.expected_minor|money_currency(summary.next.commitment.account.currency if summary.next.commitment.account else 'INR') }}{% else %}Nothing scheduled{% endif %}</p></div><span class="badge text-bg-info">{{ summary.upcoming|length }} upcoming</span></div>
        {% for occurrence in summary.upcoming %}
          <div class="d-flex align-items-center gap-3 border-top py-3 mt-2"><span class="metric-icon"><i class="bi bi-arrow-up-right"></i></span><div class="flex-grow-1"><strong>{{ occurrence.commitment.name }}</strong><div class="small text-body-secondary">{{ occurrence.commitment.commitment_type|replace('_', ' ')|title }} · Due {{ occurrence.due_date }}{% if occurrence.commitment.autopay %} · Autopay{% endif %}</div></div><strong>{{ occurrence.expected_minor|money_currency(occurrence.commitment.account.currency if occurrence.commitment.account else 'INR') }}</strong><div class="d-flex gap-1"><a class="btn btn-sm btn-light" href="{{ url_for('insights.pay_commitment_occurrence', record_id=occurrence.commitment_id, occurrence_id=occurrence.id) }}">Pay</a><form method="post" action="{{ url_for('insights.commitment_occurrence_action', record_id=occurrence.commitment_id, occurrence_id=occurrence.id, action='skip') }}">{{ action_form.hidden_tag() }}<button class="btn btn-sm btn-light">Skip</button></form></div></div>
        {% else %}<div class="empty-state"><div class="empty-illustration"><i class="bi bi-check2-circle"></i></div><strong>No upcoming payments</strong><p>Add a loan, subscription, rent, or future bill to track it here.</p></div>{% endfor %}
      </div></div>
      <div class="card mb-4"><div class="card-body"><h2 class="card-title">Overdue payments</h2>{% for occurrence in summary.overdue_items %}<div class="d-flex justify-content-between border-top py-3"><span><strong>{{ occurrence.commitment.name }}</strong><br><small class="text-danger">Due {{ occurrence.due_date }}</small></span><strong>{{ occurrence.expected_minor|money_currency(occurrence.commitment.account.currency if occurrence.commitment.account else 'INR') }}</strong></div>{% else %}<p class="small text-body-secondary mb-0">No overdue commitments.</p>{% endfor %}</div></div>
      <div class="card"><div class="card-body"><h2 class="card-title">Your commitments</h2>{% for item in summary.commitments %}<div class="d-flex align-items-center gap-2 border-top py-3"><div class="flex-grow-1"><strong>{{ item.name }}</strong><div class="small text-body-secondary">{{ item.commitment_type|replace('_', ' ')|title }} · {{ item.frequency|title }} · Next {{ item.next_due }}</div></div><strong>{{ item.amount_minor|money_currency(item.account.currency if item.account else 'INR') }}</strong><a class="btn btn-sm btn-light" href="{{ url_for('insights.edit_commitment', record_id=item.id) }}">Edit</a><a class="btn btn-sm btn-light" href="{{ url_for('insights.commitment_simulator', record_id=item.id) }}">Loan tools</a>{{ delete_button(url_for('insights.delete_commitment', record_id=item.id)) }}</div>{% else %}<div class="empty-state"><div class="empty-illustration"><i class="bi bi-collection"></i></div><strong>Your hub is ready</strong><p>Commitments are separate from expenses so planned payments are not double-counted.</p></div>{% endfor %}</div></div>
    </div>
  </div>
{% endblock %}
```

## app/templates/reports/dashboard.html

```html
{% extends 'base.html' %}
{% from 'macros.html' import transaction_table, summary_cards with context %}
{% block content %}
<div class="page-heading"><div><h1>Overview</h1><p>{{ today.strftime('%B %Y') }} · Your money, clearly.</p></div><a class="btn btn-primary" data-compose href="{{ url_for('expenses.add') }}">Add expense <kbd>C</kbd></a></div>
{{ summary_cards(summary, month_spend) }}
{% set budget_total = budgets|sum(attribute='budget.amount_minor') %}
{% set planned_spend = budgets|sum(attribute='spent') %}
<section class="pacing-panel" aria-label="Monthly budget pacing">
  <div class="section-header"><div><h2>Monthly budget pacing</h2><p>{% if budget_total %}{{ planned_spend|money }} spent across budgeted categories{% else %}Set a monthly category limit to see your pace{% endif %}</p></div><a href="{{ url_for('expenses.budgets') }}">Manage budgets</a></div>
  {% if budget_total %}{% set pace = planned_spend * 100 / budget_total %}<progress class="budget-progress {{ 'danger' if pace >= 100 else 'warning' if pace >= 80 else 'success' }}" max="100" value="{{ [100,[0,pace]|max]|min }}" aria-label="Monthly category budget used">{{ pace|round(1) }}%</progress><div class="d-flex justify-content-between small mt-2"><span>{{ pace|round(1) }}% used</span><span>{{ ((budget_total - planned_spend)|abs)|money }} {{ 'over limit' if planned_spend > budget_total else 'remaining' }}</span></div>{% endif %}
</section>
{% if unconverted_count|default(0) %}<div class="alert alert-warning">{{ unconverted_count }} foreign-currency entries have no INR conversion snapshot and are excluded from totals. Add a verified rate before relying on these reports.</div>{% endif %}
<div class="dashboard-columns">
  <section class="card"><div class="card-body"><div class="section-header"><div><h2>Income & spending</h2><p>Last 12 months · INR</p></div><a href="{{ url_for('reports.report',period='yearly') }}">Full report</a></div><div class="chart-wrap chart-loading"><canvas id="comparison-chart" role="img" aria-label="Monthly income and spending"></canvas><div class="chart-skeleton" aria-hidden="true"></div></div></div></section>
  <section class="card"><div class="card-body"><div class="section-header"><div><h2>Where it went</h2><p>This month · net of refunds</p></div></div><div class="chart-wrap chart-loading"><canvas id="category-chart" role="img" aria-label="Spending by category"></canvas><div class="chart-skeleton" aria-hidden="true"></div></div><div id="category-empty" class="empty-state d-none"><strong>No spending yet this month</strong><p>Your category breakdown appears after your first expense.</p><a data-compose href="{{ url_for('expenses.add') }}">Add expense</a></div><a class="small" href="{{ url_for('reports.report',period='monthly') }}">View accessible report</a></div></section>
</div>
<div id="chart-error" class="alert alert-warning d-none" role="status">Charts could not load. Your ledger and reports are still available. <button class="btn btn-sm btn-light" id="retry-charts" type="button">Retry charts</button></div>
<div class="dashboard-columns mt-4">
  <section class="card overflow-hidden"><div class="card-body section-header"><div><h2>Recent transactions</h2><p>Latest activity across your accounts</p></div><a href="{{ url_for('expenses.index') }}">View all</a></div>{{ transaction_table(recent) }}</section>
  <section class="card"><div class="card-body"><div class="section-header"><h2>Category budgets</h2><a href="{{ url_for('expenses.budgets') }}">Edit</a></div>{% for item in budgets %}<div class="budget-line"><div class="d-flex justify-content-between"><strong>{{ item.budget.category.name }}</strong><span>{{ item.percent|round(0) }}%</span></div><progress class="budget-progress {{ item.level }}" max="100" value="{{ [100,[0,item.percent]|max]|min }}" aria-label="{{ item.budget.category.name }} budget used"></progress><p class="small text-body-secondary">{{ item.spent|money }} of {{ item.budget.amount_minor|money }}</p></div>{% else %}<div class="empty-state"><strong>A little structure goes a long way</strong><p>Choose a category and set the amount you want to spend this month.</p><a class="btn btn-light" href="{{ url_for('expenses.budgets') }}">Create a budget</a></div>{% endfor %}</div></section>
</div>
<div class="dashboard-columns mt-4">
  <section class="card"><div class="card-body"><div class="section-header"><div><h2>Spending rhythm</h2><p>Your monthly trend · INR</p></div></div><div class="chart-wrap chart-loading"><canvas id="trend-chart" role="img" aria-label="Monthly spending trend"></canvas><div class="chart-skeleton" aria-hidden="true"></div></div></div></section>
  <section class="card"><div class="card-body"><div class="section-header"><h2>Planning signals</h2><a href="{{ url_for('insights.insights') }}">Details</a></div><div class="compact-list"><div><span>Financial health</span><strong>{{ health.score }} / 100 · {{ health.status }}</strong></div><div><span>Forecast closing balance</span><strong>{{ forecast.closing_balance|money }}</strong></div><div><span>Upcoming commitments</span><strong>{{ commitment_data.remaining|money }}</strong></div></div><p class="small text-body-secondary mt-3">Estimates from recorded activity, not guaranteed bank balances.</p>{% if commitment_data.unconverted_count %}<p class="small text-warning" role="status">{{ commitment_data.unconverted_count }} commitments excluded from INR totals: no cached conversion rate.</p>{% endif %}{% for item in insights %}<div class="insight-row"><strong>{{ item.title }}</strong><p>{{ item.message }}</p></div>{% endfor %}</div></section>
</div>
{% if forecast.unconverted_recurring_count|default(0) %}<div class="alert alert-warning" role="status">{{ forecast.unconverted_recurring_count }} upcoming foreign-currency payments could not be converted. Forecast totals exclude these payments until an INR rate is available.</div>{% endif %}
<details class="workspace-details mt-4"><summary>Accounts, investments & upcoming plans</summary><div class="dashboard-columns mt-3"><section class="card"><div class="card-body"><div class="section-header"><h2>Accounts</h2><a href="{{ url_for('accounts.index') }}">Manage</a></div><div class="compact-list">{% for item in account_items %}<div><span>{{ item.account.name }}</span><strong>{{ item.balance_minor|money_currency(item.account.currency) }}</strong></div>{% else %}<p>No accounts yet. Add a bank, wallet, or cash account.</p>{% endfor %}</div><div class="section-header mt-4"><h2>Investments</h2><a href="{{ url_for('investments.index') }}">Portfolio</a></div>{% if investment_data.totals.unconverted_count %}<p class="small text-warning">{{ investment_data.totals.unconverted_count }} holdings excluded: no INR conversion rate.</p>{% endif %}<div class="compact-list"><div><span>Invested</span><strong>{{ investment_data.totals.invested|money }}</strong></div><div><span>Current value</span><strong>{{ investment_data.totals.value|money }}</strong></div></div></div></section><section class="card"><div class="card-body"><div class="section-header"><h2>Upcoming bills</h2><a href="{{ url_for('insights.calendar_view') }}">Calendar</a></div><div class="compact-list">{% for bill in upcoming_bills %}<div><span>{{ bill.card_name }} · {{ bill.due_date }}</span><strong>{{ bill.amount_minor|money }}</strong></div>{% else %}<p>No bills due in the next two weeks.</p>{% endfor %}</div><div class="section-header mt-4"><h2>Savings goals</h2><a href="{{ url_for('insights.goals') }}">Manage</a></div>{% for goal,metrics in goals %}<div class="budget-line"><div class="d-flex justify-content-between"><strong>{{ goal.name }}</strong><span>{{ metrics.progress|round(0) }}%</span></div><progress class="budget-progress" max="100" value="{{ [100,metrics.progress]|min }}" aria-label="{{ goal.name }} savings progress"></progress><p class="small">{{ goal.saved_minor|money }} of {{ goal.target_minor|money }}</p></div>{% else %}<p class="small text-body-secondary">No active savings goals.</p>{% endfor %}</div></section></div></details>
{% endblock %}
{% block scripts %}<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.8/dist/chart.umd.min.js"></script><script src="{{ url_for('static',filename='js/charts.js') }}" defer></script>{% endblock %}
```

## tests/test_integration_flow.py

```python
"""Exercise the real composer contract through CSRF, validation, ledger and UI.

These checks catch decimal scaling/rounding, writes on invalid input, tenant
leaks, and missing refund/split presentation without mocking the boundaries.
"""

from html.parser import HTMLParser
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import Category, ExchangeRate, Transaction, User
from conftest import category_id, transaction_data
from test_accounts import account_data, account_id
from test_commitments import commitment_data


class LedgerPage(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = {}
        self.rows = {}
        self.refunds = {}
        self._form = None
        self._row = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self._form = attrs.get("id")
            if self._form:
                self.forms[self._form] = dict(attrs)
        if tag == "input" and self._form and attrs.get("name") == "csrf_token":
            self.forms[self._form]["csrf_token"] = attrs["value"]
        if tag == "tr" and "data-transaction-id" in attrs:
            self._row = int(attrs["data-transaction-id"])
            self.rows[self._row] = ""
        if tag == "button" and "data-refund" in attrs:
            self.refunds[int(attrs["data-refund"])] = attrs

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        if tag == "tr":
            self._row = None

    def handle_data(self, text):
        if self._row is not None:
            self.rows[self._row] += text


@pytest.fixture
def csrf_client(signed_in, app):
    # Authentication setup uses the shared fixture; every tested write uses CSRF.
    app.config["WTF_CSRF_ENABLED"] = True
    return signed_in


def composer(client):
    response = client.get("/expenses/")
    assert response.status_code == 200
    form = LedgerPage(response.text).forms["compose-form"]
    assert form["csrf_token"]
    return form


def submit(client, form, cid, amount, **overrides):
    return client.post(
        form["action"],
        data=transaction_data(cid, amount=amount, csrf_token=form["csrf_token"], account_id="0", **overrides),
        headers={"Accept": "application/json"},
    )


def test_decimal_composer_reaches_integer_ledger_formatted_ui_and_charts(csrf_client, app):
    cid = category_id(app)
    form = composer(csrf_client)
    first = submit(csrf_client, form, cid, "0.10")
    second = submit(csrf_client, form, cid, "0.20")
    assert first.status_code == second.status_code == 201
    ids = [first.json["transaction"]["id"], second.json["transaction"]["id"]]
    assert first.json["transaction"]["amount_minor"] == 10
    assert second.json["transaction"]["amount_minor"] == 20
    with app.app_context():
        rows = db.session.scalars(db.select(Transaction).order_by(Transaction.id)).all()
        assert [row.amount_minor for row in rows] == [10, 20]
        assert [row.base_amount_minor for row in rows] == [10, 20]
        assert db.session.scalar(db.select(db.func.sum(Transaction.amount_minor))) == 30
    page = LedgerPage(csrf_client.get("/expenses/").text)
    assert "INR 0.10" in page.rows[ids[0]]
    assert "INR 0.20" in page.rows[ids[1]]
    dashboard = csrf_client.get("/")
    assert dashboard.status_code == 200
    assert "INR 0.30" in dashboard.text
    chart = csrf_client.get("/reports/chart-data").json
    assert chart["currency"] == "INR"
    assert chart["expense"][-1] == 0.3
    assert chart["category_labels"] == ["Food"]
    assert chart["category_values"] == [0.3]


@pytest.mark.parametrize("amount", ["abc", "NaN", "Infinity", "-Infinity", "0.001", "0", "-0.01"])
def test_composer_rejects_invalid_money_without_writes(csrf_client, app, amount):
    response = submit(csrf_client, composer(csrf_client), category_id(app), amount)
    assert response.status_code == 422
    assert "amount" in response.json["errors"]
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Transaction.id))) == 0


def test_composer_rejects_foreign_category_without_writes(csrf_client, app):
    with app.app_context():
        other = User(name="Other", email="other@example.com", password_hash="unused")
        db.session.add(other)
        db.session.flush()
        category = Category(user_id=other.id, name="Private category")
        db.session.add(category)
        db.session.commit()
        foreign_id = category.id
    form = composer(csrf_client)
    response = submit(csrf_client, form, foreign_id, "0.01")
    assert response.status_code == 422
    assert "category_id" in response.json["errors"]
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Transaction.id))) == 0


def test_composer_requires_csrf_before_writing(csrf_client, app):
    form = composer(csrf_client)
    form["csrf_token"] = "invalid-token"
    assert submit(csrf_client, form, category_id(app), "0.01").status_code == 400
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Transaction.id))) == 0


def test_split_and_refund_keep_cent_values_and_remaining_controls(csrf_client, app):
    cid = category_id(app)
    form = composer(csrf_client)
    original = submit(csrf_client, form, cid, "0.03")
    assert original.status_code == 201
    parent_id = original.json["transaction"]["id"]
    headers = {"X-CSRFToken": form["csrf_token"]}
    split = csrf_client.post(f"/expenses/{parent_id}/split", json={"parts": [
        {"category_id": cid, "amount": "0.01"},
        {"category_id": cid, "amount": "0.02"},
    ]}, headers=headers)
    assert split.status_code == 201
    penny_id, two_cent_id = split.json["ids"]
    page = LedgerPage(csrf_client.get("/expenses/").text)
    assert parent_id not in page.rows
    assert "INR 0.01" in page.rows[penny_id]
    assert "Linked entry" in page.rows[penny_id]
    assert "INR 0.02" in page.rows[two_cent_id]
    assert page.refunds[two_cent_id]["data-amount"] == "0.02"
    assert "data-refund-only" in page.refunds[two_cent_id]
    refund = csrf_client.post(f"/expenses/{two_cent_id}/refund", json={
        "amount": "0.01", "date": "2026-10-30", "notes": "One cent returned",
    }, headers=headers)
    assert refund.status_code == 201
    refund_id = refund.json["id"]
    page = LedgerPage(csrf_client.get("/expenses/").text)
    assert "INR 0.01" in page.rows[refund_id]
    assert "Refund" in page.rows[refund_id]
    assert "Linked entry" in page.rows[refund_id]
    assert refund_id not in page.refunds
    assert page.refunds[two_cent_id]["data-amount"] == "0.01"
    assert page.refunds[two_cent_id]["data-currency"] == "INR"
    assert "data-refund-only" in page.refunds[two_cent_id]
    with app.app_context():
        assert db.session.get(Transaction, parent_id).entry_type == "split_parent"
        assert db.session.get(Transaction, penny_id).amount_minor == 1
        assert db.session.get(Transaction, two_cent_id).amount_minor == 2
        saved_refund = db.session.get(Transaction, refund_id)
        assert saved_refund.amount_minor == 1
        assert saved_refund.refund_of_id == two_cent_id
    chart = csrf_client.get("/reports/chart-data").json
    assert chart["expense"][-1] == 0.02
    assert chart["category_values"] == [0.02]
    final = csrf_client.post(f"/expenses/{two_cent_id}/refund", json={
        "amount": "0.01", "date": "2026-10-30",
    }, headers=headers)
    assert final.status_code == 201
    assert two_cent_id not in LedgerPage(csrf_client.get("/expenses/").text).refunds


@pytest.mark.parametrize("path,amounts", [
    ("/expenses/recurring", ["USD 100.00"]),
    ("/calendar?month=2026-10", ["USD 100.00", "USD 200.00"]),
    ("/commitments", ["USD 200.00"]),
])
def test_native_planned_amounts_keep_account_currency(csrf_client, app, path, amounts):
    token = composer(csrf_client)["csrf_token"]
    assert csrf_client.post("/accounts/", data=account_data(
        currency="USD", csrf_token=token,
    )).status_code == 302
    aid = account_id(app, "Main bank")
    cid = category_id(app)
    with app.app_context():
        db.session.add(ExchangeRate(base_currency="USD", quote_currency="INR",
            rate=Decimal("83"), provider="test fixture"))
        db.session.commit()
    assert csrf_client.post("/expenses/recurring", data={
        "category_id": cid, "account_id": aid, "amount": "100.00",
        "next_due": "2026-10-30", "frequency": "monthly",
        "payment_method": "UPI", "active": "y", "csrf_token": token,
    }).status_code == 302
    assert csrf_client.post("/expenses/recurring/generate", data={
        "csrf_token": token,
    }).status_code == 302
    assert csrf_client.post("/commitments", data=commitment_data(
        cid, account_id=aid, amount="200.00", next_due="2026-10-30",
        csrf_token=token,
    )).status_code == 302
    response = csrf_client.get(path)
    assert response.status_code == 200
    for amount in amounts:
        assert amount in response.text
```

## tests/test_local_filters.py

```python
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
```

## tests/test_planning_currency.py

```python
"""Planning totals report INR while occurrences retain their native amounts."""
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import Account, Commitment, CommitmentOccurrence, ExchangeRate, User
from app.services import commitment_summary


def planned_usd(app, rate_pair=None, include_inr=False):
    with app.app_context():
        user = db.session.scalar(db.select(User))
        account = Account(user_id=user.id, name="USD cash", account_type="cash", currency="USD")
        db.session.add(account)
        db.session.flush()
        commitment = Commitment(user_id=user.id, account_id=account.id,
            name="USD plan", commitment_type="custom", amount_minor=10000,
            start_date=date(2026, 10, 1), next_due=date(2026, 10, 15), frequency="monthly")
        db.session.add(commitment)
        db.session.flush()
        for day, status, actual in [(5, "overdue", 0), (15, "paid", 2500), (30, "scheduled", 0)]:
            db.session.add(CommitmentOccurrence(commitment_id=commitment.id,
                due_date=date(2026, 10, day), expected_minor=10000, actual_minor=actual, status=status))
        if rate_pair:
            base, quote, rate = rate_pair
            db.session.add(ExchangeRate(base_currency=base, quote_currency=quote,
                rate=Decimal(rate), provider="test fixture"))
        if include_inr:
            db.session.add(Commitment(user_id=user.id, name="INR plan", commitment_type="custom",
                amount_minor=10000, start_date=date(2026, 10, 1), next_due=date(2026, 10, 30), frequency="monthly"))
        db.session.commit()
        return user.id


@pytest.mark.parametrize("pair,expected", [
    (("USD", "INR", "83"), {"due_month": 2490000, "paid_month": 207500,
        "remaining": 830000, "overdue": 830000, "monthly_fixed": 830000, "annual_recurring": 9960000}),
    (("INR", "USD", "0.0125"), {"due_month": 2400000, "paid_month": 200000,
        "remaining": 800000, "overdue": 800000, "monthly_fixed": 800000, "annual_recurring": 9600000}),
])
def test_planning_aggregates_convert_cached_direct_or_inverse_rate(signed_in, app, pair, expected):
    uid = planned_usd(app, pair)
    with app.app_context():
        summary = commitment_summary(uid, date(2026, 10, 30))
        for key, amount in expected.items():
            assert summary[key] == amount
        assert summary["unconverted_count"] == 0
        assert summary["currency"] == "INR"
        assert [row.expected_minor for row in summary["occurrences"]] == [10000, 10000, 10000]
        assert summary["next"].expected_minor == 10000


def test_planning_combines_inr_and_converted_foreign_amounts(signed_in, app):
    uid = planned_usd(app, ("USD", "INR", "83"), include_inr=True)
    with app.app_context():
        summary = commitment_summary(uid, date(2026, 10, 30))
        assert summary["due_month"] == 2500000
        assert summary["remaining"] == 840000
        assert summary["monthly_fixed"] == 840000
        assert summary["annual_recurring"] == 10080000
        assert summary["unconverted_count"] == 0


def test_missing_planning_rate_excludes_one_commitment_and_warns(signed_in, app):
    uid = planned_usd(app, include_inr=True)
    with app.app_context():
        summary = commitment_summary(uid, date(2026, 10, 30))
        assert summary["due_month"] == 10000
        assert summary["paid_month"] == 0
        assert summary["remaining"] == 10000
        assert summary["overdue"] == 0
        assert summary["monthly_fixed"] == 10000
        assert summary["annual_recurring"] == 120000
        assert summary["unconverted_count"] == 1
        assert len(summary["occurrences"]) == 4
    for path in ["/commitments", "/"]:
        response = signed_in.get(path)
        assert response.status_code == 200
        assert "1 commitments excluded from INR totals: no cached conversion rate." in response.text
```

## tools/check-motion.mjs

```javascript
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
let reduced = false;
let mobile = true;
const context = {window: {}, matchMedia: query => ({get matches() {return query.includes('reduced') ? reduced : mobile;}}), getComputedStyle: element => ({opacity: element.visual?.opacity || element.style.opacity || '1', transform: element.visual?.transform || element.style.transform || 'none'})};
vm.createContext(context);
vm.runInContext(fs.readFileSync('app/static/js/motion.js', 'utf8'), context);
const motion = context.window.XpenseMotion;
function element() {
  const listeners = {};
  const handle = {listeners: {}, addEventListener(type, callback) {this.listeners[type] = callback;}, setPointerCapture() {}, releasePointerCapture() {}};
  return {style: {}, open: false, animations: [], handle,
    showModal() {this.open = true;}, close() {this.open = false;}, remove() {this.removed = true;},
    getBoundingClientRect: () => ({height: 300}), querySelector(selector) {return selector === '[data-sheet-handle]' ? handle : this.busy ? {} : null;},
    addEventListener(type, callback) {listeners[type] = callback;},
    animate(frames, options) {
      let resolve, reject;
      const animation = {frames, options, finished: new Promise((ok, no) => {resolve = ok; reject = no;}), cancel() {this.cancelled = true; reject(new Error('cancelled'));}, finish() {resolve();}};
      this.animations.push(animation); return animation;
    }};
}
const dialog = element();
const opening = motion.open(dialog);
assert.equal(dialog.open, true);
assert.ok(dialog.animations[0].options.duration <= 160);
dialog.visual = {opacity: '.65', transform: 'matrix(1, 0, 0, 1, 0, 8)'};
const closing = motion.close(dialog);
assert.equal(dialog.animations[0].cancelled, true);
assert.equal(dialog.animations[1].frames[0].opacity, '.65');
assert.equal(dialog.animations[1].frames[0].transform, dialog.visual.transform);
const reopening = motion.open(dialog);
dialog.animations.at(-1).finish();
assert.equal(await opening, false); assert.equal(await closing, false); assert.equal(await reopening, true);
assert.equal(dialog.open, true); assert.equal(dialog.style.transform, '');
const row = element();
const removing = motion.remove(row); const reinserting = motion.insert(row);
row.animations.at(-1).finish();
assert.equal(await removing, false); assert.equal(await reinserting, true); assert.ok(!row.removed);
reduced = true;
const quiet = element(); await motion.open(quiet); await motion.close(quiet); assert.equal(quiet.animations.length, 0); assert.equal(quiet.open, false);
const quietRace = element(); quietRace.open = true; const staleClose = motion.close(quietRace); await motion.open(quietRace); assert.equal(await staleClose, false); assert.equal(quietRace.open, true);
const quietRow = element(); await motion.remove(quietRow); assert.equal(quietRow.removed, true);
reduced = false;
let dismissed = 0;
const sheet = element(); sheet.open = true; motion.bindSheet(sheet, () => {dismissed++;});
const pointer = (type, y, id = 1) => sheet.handle.listeners[type]({pointerId: id, clientY: y, button: 0, isPrimary: true, preventDefault() {}});
pointer('pointerdown', 100); pointer('pointermove', 50); assert.equal(sheet.style.transform, 'translateY(0px)');
pointer('pointermove', 900); assert.equal(sheet.style.transform, 'translateY(300px)');
pointer('pointerup', 900); assert.equal(dismissed, 1);
pointer('pointerdown', 100); pointer('pointermove', 164); pointer('pointerup', 164); assert.equal(dismissed, 1);
sheet.animations.at(-1).finish(); await Promise.resolve(); await Promise.resolve();
pointer('pointerdown', 100); pointer('pointermove', 190, 2); pointer('pointerup', 190, 2); assert.equal(dismissed, 1); pointer('pointercancel', 190); sheet.animations.at(-1).finish(); await Promise.resolve(); await Promise.resolve();
sheet.busy = true; pointer('pointerdown', 100); pointer('pointermove', 250); pointer('pointerup', 250); assert.equal(dismissed, 1); sheet.busy = false;
mobile = false; pointer('pointerdown', 100); pointer('pointermove', 250); pointer('pointerup', 250); assert.equal(dismissed, 1);
console.log('PASS: interrupted close/reopen preserves current frame, row removal superseded safely, reduced motion bypasses WAAPI, drag bounds/threshold/mobile-only behavior.');
```

## tools/check-filters.mjs

```javascript
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const context={window:{},URLSearchParams,Intl,document:{querySelector:()=>null}};
vm.runInNewContext(readFileSync('app/static/js/ledger-filters.js','utf8'),context);
const {minorFromInput,createIndex,select}=context.window.XpenseFilters;
assert.equal(minorFromInput('0.10'),10);
assert.equal(minorFromInput('999999999.99'),99999999999);
for(const bad of ['NaN','Infinity','1.999','-1','1e3','9999999999']) assert.throws(()=>minorFromInput(bad));
const rows=[{id:1,date:'2026-10-01',category_id:1,category:'Food',account_id:null,kind:'expense',amount_minor:10,notes:'Coffee'},{id:2,date:'2026-10-02',category_id:2,category:'Travel',account_id:3,kind:'expense',amount_minor:20,notes:'Metro'},{id:3,date:'2026-10-02',category_id:1,category:'Food',account_id:3,kind:'income',amount_minor:30,notes:'Refund'}];
const index=createIndex(rows);
assert.deepEqual(Array.from(select(index,new URLSearchParams('category=1&kind=expense&min_amount=0.10&max_amount=0.10')).map(r=>r.id)),[1]);
assert.deepEqual(Array.from(select(index,new URLSearchParams('account=3&sort=amount_asc')).map(r=>r.id)),[2,3]);
assert.deepEqual(Array.from(select(index,new URLSearchParams('q=COFFEE')).map(r=>r.id)),[1]);
assert.deepEqual(Array.from(select(index,new URLSearchParams('start=2026-10-02&end=2026-10-02')).map(r=>r.id)),[3,2]);
assert.throws(()=>select(index,new URLSearchParams('start=2026-11-01&end=2026-10-01')));
assert.deepEqual(rows.map(r=>r.id),[1,2,3],'sorting does not mutate snapshot');
const large=createIndex(Array.from({length:2000},(_,id)=>({...rows[id%3],id})));
const samples=[];
for(let i=0;i<200;i++){ const start=performance.now();select(large,new URLSearchParams('category=1&sort=amount_asc'));samples.push(performance.now()-start); }
samples.sort((a,b)=>a-b);
console.log('PASS: local filters precision,sort,dates,search; 2000-row kernel p95 '+samples[190].toFixed(3)+'ms');
```

## tools/check-filter-loading.mjs

```javascript
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// Minimal DOM boundary only: the complete production controller owns filtering/rendering.
class Node {
  constructor(tagName = 'DIV') { this.tagName = tagName; this.children = []; this.dataset = {}; this.listeners = new Map(); this.classList = {add() {}}; this.textContent = ''; }
  append(...children) { for (const child of children) this.children.push(...(child.fragment ? child.children : [child])); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  addEventListener(type, listener) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(listener); }
  emit(type, target = this) { for (const listener of this.listeners.get(type) || []) listener({target, preventDefault() {}}); }
  querySelectorAll() { return []; }
  querySelector() { return null; }
}
const category = {name: 'category', value: '', tagName: 'SELECT', type: 'select-one'};
const form = new Node('FORM'); form.elements = [category]; form.reportValidity = () => true;
const clear = new Node('A');
const exportLink = new Node('A'); exportLink.href = 'https://xpense.test/expenses/export';
form.querySelector = selector => selector === '[data-filter-clear]' ? clear : selector === '[data-filter-export]' ? exportLink : null;
const root = new Node(); root.dataset.snapshotUrl = '/expenses/snapshot';
const tbody = new Node('TBODY'); root.querySelector = selector => selector === 'tbody' ? tbody : null;
const home = new Node(), homeBody = new Node(); home.querySelector = () => homeBody;
const sheet = new Node('DIALOG'); sheet.id = 'transaction-filters-sheet';
const status = new Node(), error = new Node(), pager = new Node();
const elements = new Map([['transaction-filters',form],['filter-home',home],['transaction-filters-sheet',sheet],['filter-sheet-body',new Node()],['local-filter-status',status],['filter-error',error],['ledger-pagination',pager]]);
const markupRows = [1,2].map(id => { const row = new Node('TR'); row.dataset.transactionId = String(id); return row; });
const document = {
  documentElement: new Node(), querySelector: selector => selector === '#ledger-results' ? root : null,
  getElementById: id => elements.get(id), dispatchEvent() {},
  createDocumentFragment() { const fragment = new Node(); fragment.fragment = true; return fragment; },
  createElement(tag) { const element = new Node(tag.toUpperCase()); if (tag === 'template') element.content = {querySelectorAll: () => markupRows}; return element; }
};
const location = {search: ''};
const historyCalls = [];
let respond;
const pendingFetch = new Promise(resolve => {respond = resolve;});
const window = {history: {replaceState(_state, _title, url) {historyCalls.push(url); location.search = url;}}, addEventListener() {}};
class FormData { constructor(element) {this.entries = element.elements.map(field => [field.name, field.value]);} [Symbol.iterator]() {return this.entries[Symbol.iterator]();} }
const context = {window, document, location, FormData, URL, URLSearchParams, Intl, performance, AbortController, setTimeout, clearTimeout, CustomEvent: class {constructor(type) {this.type = type;}}, matchMedia: () => ({matches:false,addEventListener(){}}), fetch: () => pendingFetch};
vm.runInNewContext(fs.readFileSync('app/static/js/ledger-filters.js','utf8'), context);
assert.equal(status.textContent, 'Preparing fast filters…', 'snapshot must still be pending');
category.value = '2';
form.emit('change', category);
respond({ok:true,json:async () => ({complete:true,today:'2026-11-01',html:'<server-rendered-rows>',rows:[
  {id:1,date:'2026-11-01',category_id:1,category:'Food',account_id:null,kind:'expense',amount_minor:100,notes:'Lunch'},
  {id:2,date:'2026-11-01',category_id:2,category:'Travel',account_id:null,kind:'expense',amount_minor:200,notes:'Metro'}
]})});
for (let turn=0;turn<12;turn++) await Promise.resolve();
assert.equal(error.textContent, '', 'the real render must finish without DOM-stub exceptions');
const renderedIds = tbody.children.filter(row => row.dataset.transactionId).map(row => Number(row.dataset.transactionId));
assert.deepEqual(renderedIds, [2], 'changing category while snapshot loads must render the current selection');
assert.equal(new URLSearchParams(location.search).get('category'), '2', 'URL must reflect the selection rendered after preparation');
assert.equal(status.textContent, '1 matching transactions · Filters ready');
assert.equal(category.value, '2', 'preparation must preserve the selected control');
assert.equal(new URL(exportLink.href).searchParams.get('category'), '2', 'export must use the same active filter');
assert.ok(historyCalls.length, 'preparation must synchronise history when it applies a queued selection');
console.log('PASS: category changed during delayed snapshot preparation stays consistent across rows, controls, status, URL and export.');
```
