"""Personal investment ledger; this module never places trades or connects to brokers."""

from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..data.investments import INVESTMENT_TYPES
from ..extensions import db
from ..forms import BaseForm, InvestmentForm, InvestmentRedemptionForm
from ..models import Account, Category, Investment, InvestmentRedemption, Transaction
from ..services import (
    account_choices,
    investment_summary,
    log_event,
    minor,
    owned,
    today,
)

bp = Blueprint("investments", __name__, url_prefix="/investments")


def setup_form(form, row=None):
    form.account_id.choices = [(0, "No funding account")] + account_choices(current_user.id)
    if row and row.account and not row.account.active:
        form.account_id.choices.append((row.account.id, row.account.name))
    form.investment_type.choices = INVESTMENT_TYPES


def validate_funding(form, row=None):
    account_id = form.account_id.data or None
    if row is not None:
        redemption = db.session.scalar(db.select(InvestmentRedemption).where(InvestmentRedemption.investment_id == row.id))
        if redemption and (form.status.data != 'redeemed' or minor(form.amount_invested.data) != row.amount_invested_minor
                or form.currency.data != row.currency or form.purchase_date.data != row.purchase_date):
            form.status.errors.append('A redeemed investment cannot reopen or change its posted funding details.')
            return False
        if account_id != row.account_id:
            form.account_id.errors.append("An existing investment cannot change funding account. Create a new investment instead.")
            return False
        if row.account_id and (minor(form.amount_invested.data) != row.amount_invested_minor or form.currency.data != row.currency or form.purchase_date.data != row.purchase_date):
            form.amount_invested.errors.append("Posted funding amount, currency and purchase date are locked. Valuations can still be updated.")
            return False
    elif account_id:
        account = db.session.get(Account, account_id)
        if account is None or account.user_id != current_user.id or not account.active:
            form.account_id.errors.append("Choose your own active funding account.")
            return False
        if account.currency != form.currency.data:
            form.currency.errors.append("Investment and funding account currencies must match.")
            return False
    return True


def investment_category():
    category = db.session.scalar(
        db.select(Category).where(
            Category.user_id == current_user.id, Category.name == "Investments"
        )
    )
    if category is None:
        category = Category(user_id=current_user.id, name="Investments")
        db.session.add(category)
        db.session.flush()
    return category


def save_investment(row, form):
    row.name = form.name.data
    row.investment_type = form.investment_type.data
    row.institution = form.institution.data or ""
    row.account_id = form.account_id.data or None
    row.currency = form.currency.data
    row.purchase_date = form.purchase_date.data
    row.amount_invested_minor = minor(form.amount_invested.data)
    row.current_value_minor = minor(form.current_value.data if form.current_value.data is not None else form.amount_invested.data)
    row.reference = form.reference.data or None
    row.notes = form.notes.data or ""
    row.status = form.status.data
    row.maturity_date = form.maturity_date.data
    row.expected_return_rate = form.expected_return_rate.data
    row.interest_earned_minor = minor(form.interest_earned.data or 0)
    row.dividend_income_minor = minor(form.dividend_income.data or 0)


@bp.route("/add", methods=["GET", "POST"])
@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    form = InvestmentForm()
    setup_form(form)
    if request.method == "GET":
        form.purchase_date.data = today()
        form.currency.data = current_user.currency
    if form.validate_on_submit() and validate_funding(form):
        row = Investment(user_id=current_user.id)
        save_investment(row, form)
        db.session.add(row)
        db.session.flush()
        if row.account_id:
            category = investment_category()
            db.session.add(
                Transaction(
                    user_id=current_user.id,
                    category_id=category.id,
                    kind="expense",
                    amount_minor=row.amount_invested_minor,
                    date=row.purchase_date,
                    payment_method="Investment funding",
                    notes=f"Investment funding: {row.name}",
                    account_id=row.account_id,
                    entry_type="investment",
                    currency=row.currency,
                    base_amount_minor=row.amount_invested_minor,
                    exchange_rate=Decimal("1"),
                    exchange_rate_date=row.purchase_date,
                    exchange_rate_source="same currency",
                )
            )
        log_event(current_user.id, "Investment added")
        db.session.commit()
        flash("Investment added to your personal ledger.", "success")
        return redirect(url_for("investments.index"))
    if request.path.endswith('/add') or request.method == 'POST':
        return render_template('planning/form.html', title='Add investment', form=form,
                               back_url=url_for('investments.index'), back_label='Investments')
    return render_template(
        "investments/index.html",
        title="Investments",
        form=form,
        summary=investment_summary(current_user.id),
        investment_types=INVESTMENT_TYPES,
    )


@bp.route("/<int:record_id>/edit", methods=["GET", "POST"])
@login_required
def edit(record_id):
    row = owned(Investment, record_id)
    form = InvestmentForm(obj=row)
    setup_form(form, row)
    if request.method == "GET":
        form.amount_invested.data = Decimal(row.amount_invested_minor) / 100
        form.current_value.data = Decimal(row.current_value_minor) / 100
        form.interest_earned.data = Decimal(row.interest_earned_minor) / 100
        form.dividend_income.data = Decimal(row.dividend_income_minor) / 100
    if form.validate_on_submit() and validate_funding(form, row):
        save_investment(row, form)
        db.session.commit()
        flash("Investment updated.", "success")
        return redirect(url_for("investments.index"))
    return render_template("expenses/form.html", form=form, title="Edit investment")


@bp.post("/<int:record_id>/delete")
@login_required
def delete(record_id):
    row = owned(Investment, record_id)
    if BaseForm().validate_on_submit():
        if row.status != 'redeemed':
            row.status = "closed"
        db.session.commit()
        flash("Investment closed; its history was retained.", "success")
    return redirect(url_for("investments.index"))


@bp.route('/<int:record_id>/redeem', methods=['GET', 'POST'])
@login_required
def redeem(record_id):
    row = owned(Investment, record_id)
    if request.method == 'POST':
        # Serialize retries against this holding before writing linked cash proceeds.
        db.session.execute(db.update(Investment).where(Investment.id == row.id,
            Investment.user_id == current_user.id).values(status=Investment.status))
        db.session.refresh(row)
    existing = db.session.scalar(db.select(InvestmentRedemption).where(
        InvestmentRedemption.investment_id == row.id))
    if existing:
        abort(409, description='This investment has already been redeemed.')
    form = InvestmentRedemptionForm()
    form.account_id.choices = account_choices(current_user.id)
    if request.method == 'GET':
        form.redemption_date.data = today()
    if form.validate_on_submit():
        account = db.session.scalar(db.select(Account).where(Account.id == form.account_id.data,
            Account.user_id == current_user.id, Account.active.is_(True)))
        if account is None:
            form.account_id.errors.append('Choose your own active destination account.')
        elif account.currency != row.currency:
            form.account_id.errors.append('Investment and destination account currencies must match.')
        elif not 1900 <= form.redemption_date.data.year <= 9998 or form.redemption_date.data < row.purchase_date:
            form.redemption_date.errors.append('Use a date on or after the purchase date, between 1900 and 9998.')
        else:
            amount = minor(form.amount.data)
            entry = Transaction(user_id=current_user.id, category_id=investment_category().id,
                kind='income', amount_minor=amount, date=form.redemption_date.data,
                payment_method='Investment proceeds', notes=f'Investment redemption: {row.name}',
                account_id=account.id, entry_type='investment', currency=row.currency,
                base_amount_minor=amount if row.currency == 'INR' else None)
            db.session.add(entry)
            db.session.flush()
            db.session.add(InvestmentRedemption(user_id=current_user.id, investment_id=row.id,
                account_id=account.id, transaction_id=entry.id, amount_minor=amount,
                currency=row.currency, redemption_date=form.redemption_date.data, notes=form.notes.data or ''))
            row.status = 'redeemed'
            log_event(current_user.id, 'Investment redeemed')
            db.session.commit()
            flash('Redemption recorded and proceeds credited to your destination account.', 'success')
            return redirect(url_for('investments.index'))
    return render_template('investments/redeem.html', title='Redeem investment', form=form, investment=row)
