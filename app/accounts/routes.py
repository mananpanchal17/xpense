"""Personal ledger accounts, transfers, adjustments, and cached currency rates."""

import secrets
from decimal import Decimal, DecimalException

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db, limiter
from ..forms import AccountForm, BalanceAdjustmentForm, BaseForm, TransferForm, CURRENCIES
from ..models import Account, Category, Transaction, Transfer, ExchangeRate
from ..services import (
    account_balance,
    account_choices,
    account_summary,
    exchange_rate,
    log_event,
    minor,
    refresh_exchange_rates,
    today,
    owned,
)
from ..data.providers import BANKS, WALLETS

bp = Blueprint("accounts", __name__, url_prefix="/accounts")


def save_account(row, form):
    row.name = form.name.data
    row.account_type = form.account_type.data
    row.provider = form.provider.data or ""
    row.logo_key = form.logo_key.data or None
    row.last_four = form.last_four.data or None
    row.opening_balance_minor = minor(form.opening_balance.data or 0)
    row.currency = form.currency.data
    row.color = form.color.data
    row.icon = form.icon.data or "wallet2"
    row.linked_account_id = form.linked_account_id.data or None
    row.include_in_total = form.include_in_total.data
    row.active = form.active.data
    row.notes = form.notes.data or ""


def transfer_category(user_id):
    category = db.session.scalar(
        db.select(Category).where(Category.user_id == user_id, Category.name == "Transfers")
    )
    if category is None:
        category = Category(user_id=user_id, name="Transfers")
        db.session.add(category)
        db.session.flush()
    return category


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    form = AccountForm()
    form.linked_account_id.choices = [(0, "Not linked")] + account_choices(current_user.id, True)
    if request.method == "GET":
        form.currency.data = current_user.currency
    if form.validate_on_submit():
        row = Account(user_id=current_user.id)
        save_account(row, form)
        db.session.add(row)
        log_event(current_user.id, "Account added")
        db.session.commit()
        flash("Account added.", "success")
        return redirect(url_for("accounts.index"))
    return render_template(
        "accounts/index.html",
        title="Accounts",
        form=form,
        accounts=account_summary(current_user.id),
        currencies=dict(CURRENCIES),
        banks=BANKS,
        wallets=WALLETS,
    )


@bp.route("/<int:record_id>/edit", methods=["GET", "POST"])
@login_required
def edit(record_id):
    row = owned(Account, record_id)
    form = AccountForm(obj=row)
    form.linked_account_id.choices = [
        (0, "Not linked")
    ] + [(account_id, name) for account_id, name in account_choices(current_user.id, True) if account_id != row.id]
    if request.method == "GET":
        form.opening_balance.data = Decimal(row.opening_balance_minor) / 100
    if form.validate_on_submit():
        if form.currency.data != row.currency and (row.opening_balance_minor or db.session.scalar(db.select(Transaction.id).where(Transaction.account_id == row.id).limit(1)) is not None):
            form.currency.errors.append("An account with a balance or posted history cannot change currency.")
            return render_template("expenses/form.html", form=form, title="Edit account")
        save_account(row, form)
        db.session.commit()
        flash("Account updated.", "success")
        return redirect(url_for("accounts.index"))
    return render_template("expenses/form.html", form=form, title="Edit account")


@bp.post("/<int:record_id>/archive")
@login_required
def archive(record_id):
    row = owned(Account, record_id)
    if BaseForm().validate_on_submit():
        row.active = False
        db.session.commit()
        flash("Account archived. Historical transactions were retained.", "success")
    return redirect(url_for("accounts.index"))


@bp.route("/transfer", methods=["GET", "POST"])
@login_required
def transfer():
    form = TransferForm()
    choices = account_choices(current_user.id)
    form.source_account_id.choices = choices
    form.destination_account_id.choices = choices
    if request.method == "GET":
        form.transfer_date.data = today()
    if form.validate_on_submit():
        # Serialize transfers from this account before checking its available balance.
        db.session.execute(db.update(Account).where(Account.id == form.source_account_id.data,
            Account.user_id == current_user.id).values(opening_balance_minor=Account.opening_balance_minor))
        source = db.session.scalar(
            db.select(Account).where(
                Account.id == form.source_account_id.data,
                Account.user_id == current_user.id,
                Account.active.is_(True),
            )
        )
        destination = db.session.scalar(
            db.select(Account).where(
                Account.id == form.destination_account_id.data,
                Account.user_id == current_user.id,
                Account.active.is_(True),
            )
        )
        if source is None or destination is None:
            form.source_account_id.errors.append("Choose your own active accounts.")
        elif source.id == destination.id:
            form.destination_account_id.errors.append("Source and destination must differ.")
        elif minor(form.amount.data) + minor(form.fee.data or 0) > 99999999999:
            form.amount.errors.append("Amount plus fee exceeds the supported transaction limit.")
        elif account_balance(source) < minor(form.amount.data) + minor(form.fee.data or 0):
            form.amount.errors.append("The source account does not have enough balance.")
        else:
            source_amount = minor(form.amount.data)
            rate = Decimal("1")
            destination_amount = (
                minor(form.destination_amount.data)
                if form.destination_amount.data is not None
                else 0
            )
            if source.currency != destination.currency:
                try:
                    rate, _, _ = exchange_rate(source.currency, destination.currency)
                except ValueError as error:
                    form.destination_amount.errors.append(str(error))
                if not form.destination_amount.errors and not destination_amount:
                    destination_amount = int((Decimal(source_amount) * rate).quantize(Decimal("1")))
            else:
                if destination_amount and destination_amount != source_amount:
                    form.destination_amount.errors.append("Same-currency transfer amounts must match.")
                destination_amount = source_amount
            if not form.destination_amount.errors:
                fee = minor(form.fee.data or 0)
                fee_rate, fee_provider, fee_rate_date = Decimal('1'), 'same currency', form.transfer_date.data
                fee_base = fee
                if fee:
                    try:
                        fee_rate, fee_provider, fee_rate_date = exchange_rate(source.currency, 'INR')
                        fee_base = int((Decimal(fee) * fee_rate).quantize(Decimal('1')))
                        if not 0 < fee_base <= 99999999999:
                            raise ValueError('Converted fee is outside the supported range.')
                    except ValueError as error:
                        form.fee.errors.append(str(error))
                if destination_amount <= 0 or destination_amount > 99999999999:
                    form.destination_amount.errors.append("Destination amount is outside the supported range.")
                elif not form.fee.errors:
                    reference = secrets.token_hex(16)
                    category = transfer_category(current_user.id)
                    row = Transfer(
                        user_id=current_user.id,
                        source_account_id=source.id,
                        destination_account_id=destination.id,
                        amount_minor=source_amount,
                        destination_amount_minor=destination_amount,
                        fee_minor=minor(form.fee.data or 0),
                        exchange_rate=rate,
                        currency=source.currency,
                        destination_currency=destination.currency,
                        transfer_date=form.transfer_date.data,
                        notes=form.notes.data or "",
                        reference=reference,
                    )
                    db.session.add(row)
                    db.session.flush()
                    db.session.add_all(
                        [
                            Transaction(
                                user_id=current_user.id,
                                category_id=category.id,
                                kind="expense",
                                amount_minor=source_amount,
                                date=row.transfer_date,
                                payment_method="Account transfer",
                                notes=f"Transfer to {destination.name}: {row.notes}".strip(),
                                account_id=source.id,
                                entry_type="transfer",
                                transfer_id=row.id,
                                currency=source.currency,
                                base_amount_minor=source_amount if source.currency == 'INR' else None,
                            ),
                            Transaction(
                                user_id=current_user.id,
                                category_id=category.id,
                                kind="income",
                                amount_minor=destination_amount,
                                date=row.transfer_date,
                                payment_method="Account transfer",
                                notes=f"Transfer from {source.name}: {row.notes}".strip(),
                                account_id=destination.id,
                                entry_type="transfer",
                                transfer_id=row.id,
                                currency=destination.currency,
                                base_amount_minor=destination_amount if destination.currency == 'INR' else None,
                            ),
                        ]
                    )
                    if fee:
                        fee_category = db.session.scalar(db.select(Category).where(
                            Category.user_id == current_user.id, Category.name == 'Transfer fees'))
                        if fee_category is None:
                            fee_category = Category(user_id=current_user.id, name='Transfer fees')
                            db.session.add(fee_category)
                            db.session.flush()
                        db.session.add(Transaction(user_id=current_user.id, category_id=fee_category.id,
                            kind='expense', amount_minor=fee, date=row.transfer_date,
                            payment_method='Transfer fee', notes=f'Transfer fee: {source.name} to {destination.name}',
                            account_id=source.id, entry_type='normal', transfer_id=row.id,
                            currency=source.currency, base_amount_minor=fee_base,
                            exchange_rate=fee_rate, exchange_rate_date=fee_rate_date,
                            exchange_rate_source=fee_provider))
                    db.session.commit()
                    flash("Transfer completed.", "success")
                    return redirect(url_for("accounts.index"))
    return render_template("accounts/transfer.html", title="Transfer money", form=form)


@bp.route("/adjust", methods=["GET", "POST"])
@login_required
def adjust():
    form = BalanceAdjustmentForm()
    form.account_id.choices = account_choices(current_user.id)
    if request.method == "GET":
        form.adjustment_date.data = today()
    if form.validate_on_submit():
        account = db.session.scalar(
            db.select(Account).where(
                Account.id == form.account_id.data,
                Account.user_id == current_user.id,
                Account.active.is_(True),
            )
        )
        if account is None:
            form.account_id.errors.append("Choose your own active account.")
        else:
            actual = minor(form.actual_balance.data)
            difference = actual - account_balance(account)
            if difference == 0:
                form.actual_balance.errors.append("The entered balance needs no adjustment.")
            else:
                category = transfer_category(current_user.id)
                db.session.add(
                    Transaction(
                        user_id=current_user.id,
                        category_id=category.id,
                        kind="income" if difference > 0 else "expense",
                        amount_minor=abs(difference),
                        date=form.adjustment_date.data,
                        payment_method="Balance adjustment",
                        notes=f"Balance adjustment: {form.reason.data}",
                        account_id=account.id,
                        entry_type="adjustment",
                        currency=account.currency,
                        base_amount_minor=abs(difference),
                    )
                )
                db.session.commit()
                flash("Balance adjustment recorded without rewriting history.", "success")
                return redirect(url_for("accounts.index"))
    return render_template("accounts/adjust.html", title="Adjust balance", form=form)


@bp.post("/refresh-rates")
@login_required
@limiter.limit("3 per minute")
def refresh_rates():
    currencies = [row.currency for row in db.session.scalars(
        db.select(Account).where(Account.user_id == current_user.id)
    )]
    try:
        count = refresh_exchange_rates(current_user.currency, currencies)
    except (OSError, ValueError, DecimalException):
        flash("Rates could not be refreshed. Existing cached rates remain available.", "warning")
    else:
        flash(f"Updated {count} exchange rate(s).", "success")
    return redirect(request.referrer or url_for("accounts.index"))
