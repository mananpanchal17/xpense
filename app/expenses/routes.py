"""Transactions, categories, budgets, recurring rules, bills, and CSV import."""

import csv
import io
import json
import secrets
from datetime import timedelta
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


@bp.route("/recurring/add", methods=["GET", "POST"], endpoint="add_recurring")
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
    if request.path.endswith('/add') or request.method == 'POST':
        return render_template(
            'planning/form.html', title='New recurring expense', form=form,
            back_url=url_for('expenses.recurring'), back_label='Recurring expenses')
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


@bp.route("/import/preview", methods=["POST"], endpoint="preview_import")
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
            if request.endpoint == 'expenses.preview_import':
                from ..models import ImportBatch
                entries = []
                seen = set()
                for row in pending:
                    signature = (row.kind, row.amount_minor, row.category_id, row.date.isoformat(), row.payment_method, row.notes or '', row.currency or 'INR')
                    existing = db.session.scalar(db.select(Transaction.id).where(
                        Transaction.user_id == current_user.id, Transaction.kind == row.kind,
                        Transaction.amount_minor == row.amount_minor, Transaction.category_id == row.category_id,
                        Transaction.date == row.date, Transaction.payment_method == row.payment_method,
                        Transaction.notes == (row.notes or ''),
                        db.func.coalesce(Transaction.currency, 'INR') == (row.currency or 'INR')))
                    data = {column.name: getattr(row, column.name) for column in Transaction.__table__.columns
                            if column.name not in ('id', 'user_id', 'created_at') and getattr(row, column.name) is not None}
                    data = {key: value.isoformat() if isinstance(value, date) else str(value) if isinstance(value, Decimal) else value for key,value in data.items()}
                    entries.append({'data': data, 'duplicate': bool(existing or signature in seen)})
                    seen.add(signature)
                batch = ImportBatch(user_id=current_user.id, token=secrets.token_urlsafe(24), payload=json.dumps(entries))
                db.session.add(batch)
                db.session.commit()
                return render_template('expenses/import_preview.html', batch=batch, entries=entries)
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


@bp.post('/import/confirm/<token>')
@login_required
def confirm_import(token):
    from ..models import ImportBatch, utcnow
    batch = db.session.scalar(db.select(ImportBatch).where(ImportBatch.token == token, ImportBatch.user_id == current_user.id))
    if batch is None:
        abort(404)
    if not BaseForm().validate_on_submit():
        abort(400)
    if utcnow() - batch.created_at > timedelta(minutes=30):
        flash('Import preview expired. Upload the file again.', 'warning')
        return redirect(url_for('expenses.import_csv'))
    claim = db.session.execute(db.update(ImportBatch).where(ImportBatch.id == batch.id, ImportBatch.applied.is_(False)).values(applied=True))
    if claim.rowcount != 1:
        db.session.rollback()
        flash('This import was already confirmed.', 'info')
        return redirect(url_for('expenses.index'))
    count = 0
    for entry in json.loads(batch.payload):
        if entry['duplicate'] and request.form.get('include_duplicates') != 'yes':
            continue
        data = entry['data']
        category = db.session.get(Category, data['category_id'])
        if category is None or category.user_id != current_user.id:
            db.session.rollback()
            flash('An imported category is no longer available. Upload again.', 'warning')
            return redirect(url_for('expenses.import_csv'))
        for key in ['date', 'exchange_rate_date']:
            if data.get(key):
                data[key] = date.fromisoformat(data[key])
        if data.get('exchange_rate'):
            data['exchange_rate'] = Decimal(data['exchange_rate'])
        db.session.add(Transaction(user_id=current_user.id, **data))
        count += 1
    log_event(current_user.id, f'Confirmed import of {count} transactions')
    db.session.commit()
    flash(f'Imported {count} transactions.', 'success')
    return redirect(url_for('expenses.index'))


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
