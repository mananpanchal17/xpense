from calendar import monthrange, Calendar
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
from secrets import token_hex
from types import SimpleNamespace

from wtforms import HiddenField, DecimalField
from wtforms.validators import Optional

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..forms import (
    BaseForm,
    nonnegative_money_valid,
    CommitmentForm,
    CommitmentOccurrenceForm,
    ContributionForm,
    SavingsGoalForm,
)
from ..obligations import annual_cost, claim_occurrence, due_items, is_due_date
from ..models import (
    CreditCardBill,
    Commitment,
    CommitmentOccurrence,
    CommitmentPayment,
    GoalContribution,
    Notification,
    RecurringExpense,
    RecurringOccurrence,
    SavingsGoal,
    Transaction,
    Category,
)
from ..services import (
    financial_forecast,
    financial_health,
    financial_insights,
    generate_notifications,
    goal_metrics,
    amortization_schedule,
    calculate_emi,
    commitment_summary,
    prepayment_simulation,
    category_choices,
    log_event,
    minor,
    month_end,
    owned,
    today,
    account_choices,
    exchange_rate,
)

bp = Blueprint("insights", __name__)


def setup_commitment_form(form):
    form.category_id.choices = [(0, "No category")] + category_choices(current_user.id)
    form.account_id.choices = [(0, "No payment account")] + account_choices(current_user.id)


def save_commitment(row, form):
    row.name = form.name.data
    row.commitment_type = form.commitment_type.data
    row.amount_minor = minor(form.amount.data)
    row.start_date = form.start_date.data
    row.next_due = form.next_due.data
    row.end_date = form.end_date.data
    row.frequency = form.frequency.data
    row.category_id = form.category_id.data or None
    row.account_id = form.account_id.data or None
    row.payment_method = form.payment_method.data
    row.autopay = form.autopay.data
    row.reminder_days = int(form.reminder_days.data)
    row.notes = form.notes.data or ""
    row.lender = form.lender.data or None
    row.original_principal_minor = minor(form.original_principal.data) if form.original_principal.data else None
    row.outstanding_minor = minor(form.outstanding.data) if form.outstanding.data is not None else None
    row.annual_rate = form.annual_rate.data
    row.tenure_months = form.tenure_months.data
    row.processing_fee_minor = minor(form.processing_fee.data) if form.processing_fee.data else 0
    row.loan_reference = form.loan_reference.data or None


@bp.route("/commitments/add", methods=["GET", "POST"])
@bp.route("/commitments", methods=["GET", "POST"])
@login_required
def commitments():
    form = CommitmentForm()
    setup_commitment_form(form)
    if request.method == "GET":
        form.start_date.data = today()
        form.next_due.data = today()
        form.frequency.data = "monthly"
        form.commitment_type.data = "bill"
    if form.validate_on_submit():
        if form.commitment_type.data == "loan" and (
            not form.original_principal.data or not form.tenure_months.data
        ):
            form.original_principal.errors.append("Loans need an original principal and tenure.")
        else:
            row = Commitment(user_id=current_user.id)
            save_commitment(row, form)
            db.session.add(row)
            log_event(current_user.id, "Commitment created")
            db.session.commit()
            flash("Commitment added.", "success")
            return redirect(url_for("insights.commitments"))
    if request.path.endswith('/add') or request.method == 'POST':
        if request.args.get('type') == 'loan' and request.method == 'GET':
            form.commitment_type.data = 'loan'
        return render_template('planning/form.html', title='Add commitment', form=form,
                               back_url=url_for('insights.commitments'), back_label='Commitments')
    summary = commitment_summary(current_user.id)
    from ..services import emi_schedule
    emis = emi_schedule(current_user.id)
    sort = request.args.get('sort', 'due')
    if sort == 'name':
        emis.sort(key=lambda row: (row.commitment.name.casefold(), row.due_date))
    elif sort == 'status':
        emis.sort(key=lambda row: (row.due_date >= today(), row.due_date, row.id))
    generate_notifications(current_user.id)
    return render_template(
        "commitments/index.html",
        title="Commitments",
        form=form,
        summary=summary,
        emis=emis,
        action_form=BaseForm(),
        legacy_bills=db.session.scalars(
            db.select(CreditCardBill)
            .where(CreditCardBill.user_id == current_user.id, CreditCardBill.paid.is_(False))
            .order_by(CreditCardBill.due_date)
        ).all(),
        legacy_rules=db.session.scalars(
            db.select(RecurringExpense)
            .where(RecurringExpense.user_id == current_user.id)
            .order_by(RecurringExpense.next_due)
        ).all(),
    )


@bp.route("/commitments/<int:record_id>/edit", methods=["GET", "POST"])
@login_required
def edit_commitment(record_id):
    row = owned(Commitment, record_id)
    form = CommitmentForm(obj=row)
    setup_commitment_form(form)
    if request.method == "GET":
        form.amount.data = Decimal(row.amount_minor) / 100
        form.original_principal.data = Decimal(row.original_principal_minor or 0) / 100 or None
        form.outstanding.data = Decimal(row.outstanding_minor or 0) / 100 or None
        form.processing_fee.data = Decimal(row.processing_fee_minor or 0) / 100 or None
    if form.validate_on_submit():
        save_commitment(row, form)
        db.session.commit()
        flash("Commitment updated.", "success")
        return redirect(url_for("insights.commitments"))
    return render_template("expenses/form.html", form=form, title="Edit commitment")


@bp.post("/commitments/<int:record_id>/delete")
@login_required
def delete_commitment(record_id):
    row = owned(Commitment, record_id)
    if BaseForm().validate_on_submit():
        db.session.delete(row)
        db.session.commit()
        flash("Commitment deleted.", "success")
    return redirect(url_for("insights.commitments"))


class PaymentForm(CommitmentOccurrenceForm):
    payment_token = HiddenField()
    late_fee = DecimalField("Late fee", validators=[Optional(), nonnegative_money_valid])


def _owned_occurrence(row, occurrence_id):
    occurrence = db.session.scalar(db.select(CommitmentOccurrence).where(
        CommitmentOccurrence.id == occurrence_id, CommitmentOccurrence.commitment_id == row.id))
    if occurrence is None:
        abort(404)
    return occurrence


def _due_date_or_404(row, value):
    try:
        due = date.fromisoformat(value)
    except ValueError:
        abort(404)
    stored = db.session.scalar(db.select(CommitmentOccurrence.id).where(
        CommitmentOccurrence.commitment_id == row.id, CommitmentOccurrence.due_date == due))
    if stored is None and not is_due_date(row, due):
        abort(404)
    return due


@bp.app_template_global()
def commitment_due_url(item, action="pay"):
    """Id link for a stored occurrence, date link for one not stored yet."""
    if item.id is not None:
        endpoint = "insights.pay_commitment_occurrence" if action == "pay" else "insights.commitment_occurrence_action"
        args = {"record_id": item.commitment_id, "occurrence_id": item.id}
    else:
        endpoint = "insights.pay_commitment_due" if action == "pay" else "insights.commitment_due_action"
        args = {"record_id": item.commitment_id, "due": item.due_date.isoformat()}
    if action != "pay":
        args["action"] = action
    return url_for(endpoint, **args)


@bp.route("/commitments/<int:record_id>/occurrence/<int:occurrence_id>/pay", methods=["GET", "POST"])
@login_required
def pay_commitment_occurrence(record_id, occurrence_id):
    row = owned(Commitment, record_id)
    return _payment_page(row, _owned_occurrence(row, occurrence_id))


@bp.route("/commitments/<int:record_id>/due/<due>/pay", methods=["GET", "POST"])
@login_required
def pay_commitment_due(record_id, due):
    row = owned(Commitment, record_id)
    due_date = _due_date_or_404(row, due)
    form = PaymentForm()
    if request.method == "POST" and form.validate_on_submit():
        occurrence = claim_occurrence(row, due_date)
        db.session.commit()
        return _payment_page(row, occurrence, form)
    # Reading the form never stores a row; the occurrence is created when the payment is recorded.
    preview = db.session.scalar(db.select(CommitmentOccurrence).where(
        CommitmentOccurrence.commitment_id == row.id, CommitmentOccurrence.due_date == due_date))
    if preview is None:
        preview = SimpleNamespace(expected_minor=row.amount_minor, actual_minor=0)
    return _payment_page(row, preview, form)


def _payment_page(row, occurrence, form=None):
    form = form or PaymentForm()
    if request.method == "GET":
        form.actual_amount.data = Decimal(max(0, occurrence.expected_minor - occurrence.actual_minor)) / 100
        form.paid_date.data = today()
        form.payment_token.data = token_hex(32)
    if form.validate_on_submit():
        # Lock before reading cumulative totals or the retry token, including on SQLite.
        db.session.execute(db.update(CommitmentOccurrence).where(
            CommitmentOccurrence.id == occurrence.id).values(actual_minor=CommitmentOccurrence.actual_minor)
            .execution_options(synchronize_session=False))
        db.session.refresh(occurrence)
        actual = minor(form.actual_amount.data)
        fee = minor(form.late_fee.data) if form.late_fee.data else 0
        token = sha256((form.payment_token.data or repr((actual, fee, form.paid_date.data.isoformat(), form.notes.data or ""))).encode()).hexdigest()
        duplicate = db.session.scalar(db.select(CommitmentPayment.id).where(
            CommitmentPayment.occurrence_id == occurrence.id, CommitmentPayment.payment_token == token))
        if duplicate or occurrence.status == "paid":
            db.session.rollback()
            flash("This payment has already been recorded.", "info")
            return redirect(url_for("insights.commitments"))
        if actual > max(0, occurrence.expected_minor - occurrence.actual_minor):
            form.actual_amount.errors.append("Payment cannot exceed the remaining amount.")
            db.session.rollback()
        else:
            currency = row.account.currency if row.account else "INR"
            try:
                rate, source, rate_date = exchange_rate(currency, "INR") if currency != "INR" else (Decimal("1"), "same currency", today())
                base_total = int((Decimal(actual + fee) * rate).quantize(Decimal("1")))
                base_principal = int((Decimal(actual) * rate).quantize(Decimal("1")))
                if actual + fee > 99999999999 or not 0 < base_total <= 99999999999:
                    raise ValueError("Payment including fees exceeds the supported amount range.")
            except ValueError as error:
                db.session.rollback()
                form.actual_amount.errors.append(str(error))
            else:
                # Adapt an old single payment without creating another ledger debit.
                if occurrence.actual_minor and not occurrence.payments:
                    old = occurrence.transaction
                    old_rate = old.exchange_rate if old and old.exchange_rate is not None else rate
                    old_base = int((Decimal(occurrence.actual_minor) * old_rate).quantize(Decimal("1")))
                    old_total = old.base_amount_minor if old and old.base_amount_minor is not None else int((Decimal(occurrence.actual_minor + occurrence.late_fee_minor) * old_rate).quantize(Decimal("1")))
                    db.session.add(CommitmentPayment(occurrence_id=occurrence.id,
                        transaction_id=occurrence.transaction_id, payment_token="legacy",
                        amount_minor=occurrence.actual_minor, late_fee_minor=occurrence.late_fee_minor,
                        currency=old.currency if old and old.currency else currency,
                        base_amount_minor=old_base, base_late_fee_minor=old_total-old_base,
                        paid_date=occurrence.paid_date or occurrence.due_date, notes=occurrence.notes))
                transaction = None
                if row.account:
                    category = row.category or db.session.scalar(db.select(Category).where(
                        Category.user_id == current_user.id, Category.name == "Loan payments"))
                    if category is None:
                        category = Category(user_id=current_user.id, name="Loan payments")
                        db.session.add(category); db.session.flush()
                    transaction = Transaction(user_id=current_user.id, category_id=category.id,
                        kind="expense", amount_minor=actual+fee, date=form.paid_date.data,
                        payment_method=row.payment_method, notes=f"{row.name} payment",
                        account_id=row.account.id, entry_type="normal", currency=currency,
                        base_amount_minor=base_total, exchange_rate=rate,
                        exchange_rate_date=rate_date, exchange_rate_source=source)
                    db.session.add(transaction); db.session.flush()
                    if occurrence.transaction_id is None:
                        occurrence.transaction_id = transaction.id
                db.session.add(CommitmentPayment(occurrence_id=occurrence.id,
                    transaction_id=transaction.id if transaction else None, payment_token=token,
                    amount_minor=actual, late_fee_minor=fee, currency=currency,
                    base_amount_minor=base_principal, base_late_fee_minor=base_total-base_principal,
                    paid_date=form.paid_date.data, notes=form.notes.data or ""))
                occurrence.actual_minor += actual
                occurrence.late_fee_minor += fee
                occurrence.paid_date = form.paid_date.data
                occurrence.notes = form.notes.data or ""
                occurrence.status = "paid" if occurrence.actual_minor >= occurrence.expected_minor else "partial"
                db.session.commit()
                flash("Commitment payment recorded.", "success")
                return redirect(url_for("insights.commitments"))
    return render_template("expenses/form.html", form=form, title="Record commitment payment")


@bp.post("/commitments/<int:record_id>/occurrence/<int:occurrence_id>/<action>")
@login_required
def commitment_occurrence_action(record_id, occurrence_id, action):
    row = owned(Commitment, record_id)
    occurrence = _owned_occurrence(row, occurrence_id)
    if action not in ("skip", "miss"):
        abort(400)
    if BaseForm().validate_on_submit():
        occurrence.status = "skipped" if action == "skip" else "missed"
        db.session.commit()
    return redirect(url_for("insights.commitments"))


@bp.post("/commitments/<int:record_id>/due/<due>/<action>")
@login_required
def commitment_due_action(record_id, due, action):
    row = owned(Commitment, record_id)
    due_date = _due_date_or_404(row, due)
    if action not in ("skip", "miss"):
        abort(400)
    if BaseForm().validate_on_submit():
        claim_occurrence(row, due_date).status = "skipped" if action == "skip" else "missed"
        db.session.commit()
    return redirect(url_for("insights.commitments"))


@bp.route("/commitments/<int:record_id>/simulator", methods=["GET", "POST"])
@login_required
def commitment_simulator(record_id):
    row = owned(Commitment, record_id)
    result = None
    extra = None
    if row.commitment_type == "loan" and row.original_principal_minor and row.tenure_months:
        payment = calculate_emi(row.outstanding_minor or row.original_principal_minor, row.annual_rate, row.tenure_months)
        extra = request.form.get("extra_amount", "0") if request.method == "POST" else "0"
        try:
            extra_minor = minor(Decimal(extra))
            result = prepayment_simulation(
                row.outstanding_minor or row.original_principal_minor,
                row.annual_rate,
                row.tenure_months,
                payment,
                extra_minor,
            )
        except (ValueError, ArithmeticError):
            result = None
    return render_template(
        "commitments/simulator.html",
        title="Loan prepayment simulator",
        commitment=row,
        result=result,
        extra_amount=extra,
    )


@bp.get("/insights")
@login_required
def insights():
    return render_template(
        "insights/index.html",
        title="Insights",
        insights=financial_insights(current_user.id),
        forecast=financial_forecast(current_user.id),
        health=financial_health(current_user.id),
    )


def lock_goal(goal):
    # Read balances only after the write lock so concurrent corrections cannot lose updates.
    result = db.session.execute(db.update(SavingsGoal).where(
        SavingsGoal.id == goal.id, SavingsGoal.user_id == current_user.id)
        .values(saved_minor=SavingsGoal.saved_minor).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        from flask import abort
        abort(404)
    db.session.refresh(goal)


def save_goal(row, form):
    row.name = form.name.data
    row.target_minor = minor(form.target_amount.data)
    row.target_date = form.target_date.data
    row.description = form.description.data or ""


@bp.route("/goals", methods=["GET", "POST"])
@login_required
def goals():
    form = SavingsGoalForm()
    if request.method == "GET":
        form.target_date.data = today()
    if form.validate_on_submit():
        goal = SavingsGoal(user_id=current_user.id)
        save_goal(goal, form)
        db.session.add(goal)
        log_event(current_user.id, "Savings goal created")
        db.session.commit()
        flash("Savings goal created.", "success")
        return redirect(url_for("insights.goals"))
    items = db.session.scalars(
        db.select(SavingsGoal)
        .where(SavingsGoal.user_id == current_user.id)
        .order_by(SavingsGoal.status, SavingsGoal.target_date)
    ).all()
    return render_template(
        "insights/goals.html",
        title="Savings goals",
        action_form=BaseForm(),
        form=form,
        goals=[(goal, goal_metrics(goal), sorted(goal.contributions, key=lambda item: item.contribution_date, reverse=True)) for goal in items],
    )


@bp.route("/goals/<int:record_id>/edit", methods=["GET", "POST"])
@login_required
def edit_goal(record_id):
    goal = owned(SavingsGoal, record_id)
    if request.method == "POST":
        lock_goal(goal)
    form = SavingsGoalForm(obj=goal)
    if request.method == "GET":
        form.target_amount.data = Decimal(goal.target_minor) / 100
    if form.validate_on_submit():
        save_goal(goal, form)
        goal.status = "completed" if goal.saved_minor >= goal.target_minor else "active"
        db.session.commit()
        flash("Savings goal updated.", "success")
        return redirect(url_for("insights.goals"))
    return render_template("expenses/form.html", form=form, title="Edit savings goal")


@bp.post("/goals/<int:record_id>/delete")
@login_required
def delete_goal(record_id):
    goal = owned(SavingsGoal, record_id)
    if BaseForm().validate_on_submit():
        db.session.delete(goal)
        log_event(current_user.id, "Savings goal deleted")
        db.session.commit()
        flash("Savings goal deleted.", "success")
    return redirect(url_for("insights.goals"))


@bp.post("/goals/<int:record_id>/contribute")
@login_required
def contribute_goal(record_id):
    goal = owned(SavingsGoal, record_id)
    if request.method == "POST":
        lock_goal(goal)
    form = ContributionForm()
    if form.validate_on_submit():
        amount = minor(form.amount.data)
        if form.action.data == "withdraw":
            amount = -amount
        if goal.saved_minor + amount < 0:
            form.amount.errors.append("This withdrawal is larger than the amount saved.")
        else:
            contribution = GoalContribution(
                goal_id=goal.id,
                amount_minor=amount,
                contribution_date=form.contribution_date.data,
                note=form.note.data or "",
            )
            goal.saved_minor += amount
            goal.status = "completed" if goal.saved_minor >= goal.target_minor else "active"
            db.session.add(contribution)
            log_event(current_user.id, "Savings goal contribution recorded")
            db.session.commit()
            flash("Savings goal updated.", "success")
            return redirect(url_for("insights.goals"))
    items = db.session.scalars(db.select(SavingsGoal).where(SavingsGoal.user_id == current_user.id)).all()
    return render_template(
        "insights/goals.html",
        title="Savings goals",
        action_form=BaseForm(),
        form=SavingsGoalForm(),
        goals=[(item, goal_metrics(item), sorted(item.contributions, key=lambda item: item.contribution_date, reverse=True)) for item in items],
        contribution_error=(goal, form),
    )


@bp.post("/goals/<int:record_id>/complete")
@login_required
def complete_goal(record_id):
    goal = owned(SavingsGoal, record_id)
    if request.method == "POST":
        lock_goal(goal)
    if BaseForm().validate_on_submit():
        if goal.saved_minor < goal.target_minor:
            db.session.rollback()
            flash("Add the remaining savings before completing this goal.", "warning")
        else:
            goal.status = "completed"
            db.session.commit()
            flash("Goal marked completed.", "success")
    return redirect(url_for("insights.goals"))


@bp.route("/goals/<int:goal_id>/contributions/<int:contribution_id>/edit", methods=["GET", "POST"])
@login_required
def edit_contribution(goal_id, contribution_id):
    goal = owned(SavingsGoal, goal_id)
    if request.method == "POST":
        lock_goal(goal)
    contribution = db.session.scalar(
        db.select(GoalContribution).where(
            GoalContribution.id == contribution_id, GoalContribution.goal_id == goal.id
        )
    )
    if contribution is None:
        from flask import abort

        abort(404)
    form = ContributionForm(obj=contribution)
    if request.method == "GET":
        form.amount.data = Decimal(abs(contribution.amount_minor)) / 100
        form.action.data = "add" if contribution.amount_minor > 0 else "withdraw"
    if form.validate_on_submit():
        replacement = minor(form.amount.data) * (1 if form.action.data == "add" else -1)
        if goal.saved_minor - contribution.amount_minor + replacement < 0:
            form.amount.errors.append("This correction would make savings negative.")
        else:
            goal.saved_minor += replacement - contribution.amount_minor
            contribution.amount_minor = replacement
            contribution.contribution_date = form.contribution_date.data
            contribution.note = form.note.data or ""
            goal.status = "completed" if goal.saved_minor >= goal.target_minor else "active"
            db.session.commit()
            flash("Contribution corrected.", "success")
            return redirect(url_for("insights.goals"))
    return render_template("expenses/form.html", form=form, title="Correct contribution")


@bp.post("/goals/<int:goal_id>/contributions/<int:contribution_id>/delete")
@login_required
def delete_contribution(goal_id, contribution_id):
    goal = owned(SavingsGoal, goal_id)
    if request.method == "POST":
        lock_goal(goal)
    contribution = db.session.scalar(
        db.select(GoalContribution).where(
            GoalContribution.id == contribution_id, GoalContribution.goal_id == goal.id
        )
    )
    if contribution is None:
        from flask import abort

        abort(404)
    if BaseForm().validate_on_submit():
        if goal.saved_minor - contribution.amount_minor < 0:
            db.session.rollback()
            flash("This deposit cannot be removed because its savings have already been withdrawn. Correct the withdrawal first.", "warning")
            return redirect(url_for("insights.goals"))
        goal.saved_minor -= contribution.amount_minor
        goal.status = "completed" if goal.saved_minor >= goal.target_minor else "active"
        db.session.delete(contribution)
        db.session.commit()
        flash("Contribution removed.", "success")
    return redirect(url_for("insights.goals"))


@bp.get("/notifications")
@login_required
def notifications():
    generate_notifications(current_user.id)
    items = db.session.scalars(
        db.select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(100)
    ).all()
    return render_template("insights/notifications.html", title="Notifications", notifications=items)


@bp.post("/notifications/<int:record_id>/read")
@login_required
def mark_notification_read(record_id):
    item = owned(Notification, record_id)
    if BaseForm().validate_on_submit():
        from ..models import utcnow

        item.read_at = utcnow()
        db.session.commit()
    return redirect(request.referrer or url_for("insights.notifications"))


@bp.post("/notifications/read-all")
@login_required
def mark_notifications_read():
    if BaseForm().validate_on_submit():
        from ..models import utcnow

        db.session.execute(
            db.update(Notification)
            .where(Notification.user_id == current_user.id, Notification.read_at.is_(None))
            .values(read_at=utcnow())
        )
        db.session.commit()
    return redirect(url_for("insights.notifications"))


@bp.post("/notifications/delete-old")
@login_required
def delete_old_notifications():
    if BaseForm().validate_on_submit():
        db.session.execute(
            db.delete(Notification).where(
                Notification.user_id == current_user.id, Notification.read_at.is_not(None)
            )
        )
        db.session.commit()
    return redirect(url_for("insights.notifications"))


@bp.get("/calendar")
@login_required
def calendar_view():
    selected = request.args.get("month", today().strftime("%Y-%m"))
    try:
        month = date.fromisoformat(selected + "-01")
        if not 1900 <= month.year <= 9998:
            raise ValueError("Unsupported calendar year")
    except ValueError:
        month = today().replace(day=1)
    end = month_end(month)
    reference = today()
    items = due_items(current_user.id, month, end, reference=reference)
    horizon = reference + timedelta(days=8)
    # Counters follow today, independent of the month shown in the grid.
    open_items = [item for item in due_items(current_user.id, None, horizon, reference=reference)
                  if item.outstanding]
    due_today = [item for item in open_items if item.due_date == reference]
    due_soon = [item for item in open_items if reference < item.due_date < horizon]
    overdue = [item for item in open_items if item.due_date < reference]
    recurring_cost = annual_cost(current_user.id, kinds=("recurring",))
    return render_template(
        "insights/calendar.html",
        title="Bill calendar",
        month=month,
        items=items,
        reference=reference,
        due_today=due_today,
        due_soon=due_soon,
        overdue=overdue,
        action_form=BaseForm(),
        recurring_monthly_total=recurring_cost["monthly"],
        recurring_annual_total=recurring_cost["annual"],
        days=range(1, monthrange(month.year, month.month)[1] + 1),
        weeks=Calendar(firstweekday=0).monthdatescalendar(month.year, month.month) if month.year < 9999 else [],
        previous_month=(month - timedelta(days=1)).strftime('%Y-%m') if month.year > 1 or month.month > 1 else None,
        next_month=end.strftime('%Y-%m') if end.year < 9999 else None,
    )


@bp.post("/calendar/occurrences/<int:record_id>/<action>")
@login_required
def occurrence_action(record_id, action):
    occurrence = db.session.scalar(
        db.select(RecurringOccurrence)
        .join(RecurringExpense)
        .where(
            RecurringOccurrence.id == record_id,
            RecurringExpense.user_id == current_user.id,
        )
    )
    if occurrence is None:
        from flask import abort

        abort(404)
    if action not in ("paid", "skip"):
        from flask import abort

        abort(400)
    if BaseForm().validate_on_submit():
        occurrence.status = "paid" if action == "paid" else "skipped"
        db.session.commit()
        flash("Occurrence updated.", "success")
    return redirect(request.referrer or url_for("insights.calendar_view"))
