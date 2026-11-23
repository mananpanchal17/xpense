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
from .obligations import step
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
    from .models import InvestmentRedemption
    query = db.select(Investment).where(Investment.user_id == user_id)
    if request.args.get("type"):
        query = query.where(Investment.investment_type == request.args["type"])
    if request.args.get("status"):
        query = query.where(Investment.status == request.args["status"])
    rows = db.session.scalars(query.order_by(Investment.name)).all()
    items = []
    totals = dict.fromkeys(("invested", "value", "gain", "interest", "dividends", "unconverted_count"), 0)
    totals["allocation"] = {}
    closed_totals = dict.fromkeys(("invested", "value", "gain", "interest", "dividends", "unconverted_count"), 0)
    redemptions = {row.investment_id: row for row in db.session.scalars(
        db.select(InvestmentRedemption).where(InvestmentRedemption.user_id == user_id))}
    rates = {}
    for row in rows:
        gain = row.current_value_minor - row.amount_invested_minor
        item = {"investment": row, "gain_minor": gain,
                "return_percent": Decimal(gain) * 100 / row.amount_invested_minor,
                "redemption": redemptions.get(row.id), "is_closed": row.status in ('closed', 'redeemed')}
        bucket = closed_totals if item['is_closed'] else totals
        try:
            if row.currency not in rates:
                rates[row.currency] = exchange_rate(row.currency, "INR")[0]
            rate = rates[row.currency]
            converted = {key: int((Decimal(value) * rate).quantize(Decimal("1"))) for key, value in {
                "invested": row.amount_invested_minor, "value": row.current_value_minor,
                "interest": row.interest_earned_minor, "dividends": row.dividend_income_minor}.items()}
            converted["gain"] = converted["value"] - converted["invested"]
            for key, value in converted.items():
                bucket[key] += value
            if not item['is_closed']:
                totals["allocation"][row.investment_type] = totals["allocation"].get(row.investment_type, 0) + converted["value"]
            item["inr_value"] = converted["value"]
            item["inr_invested"] = converted["invested"]
            item["inr_gain"] = converted["gain"]
        except ValueError:
            bucket["unconverted_count"] += 1
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
    closed_totals['return_percent'] = Decimal(closed_totals['gain']) * 100 / closed_totals['invested'] if closed_totals['invested'] else 0
    return {"items": items, "totals": totals, 'closed_totals': closed_totals}


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


def commitment_summary(user_id, reference=None):
    """Commitment totals in INR from the Obligations module; occurrence amounts stay native."""
    from .obligations import annual_cost, due_items

    reference = reference or today()
    month = reference.replace(day=1)
    end = month_end(month)
    commitments = db.session.scalars(
        db.select(Commitment).where(
            Commitment.user_id == user_id, Commitment.status != "cancelled"
        )
    ).all()
    occurrences = due_items(user_id, month, end, reference=reference, kinds=("commitment",))
    overdue = [item for item in due_items(user_id, None, reference, reference=reference, kinds=("commitment",))
               if item.outstanding]
    upcoming = [item for item in occurrences if item.due_date >= reference and item.outstanding]
    paid = [item for item in occurrences if item.actual_minor > 0]

    def paid_inr(item):
        if item.payments:
            return sum(payment.base_amount_minor for payment in item.payments)
        transaction = item.occurrence.transaction if item.occurrence is not None else None
        if transaction and transaction.exchange_rate is not None:
            return int((Decimal(item.actual_minor) * transaction.exchange_rate).quantize(Decimal("1")))
        return item.to_inr(item.actual_minor)

    cost = annual_cost(user_id, kinds=("commitment",))
    return {
        "commitments": commitments,
        "occurrences": occurrences,
        "currency": "INR",
        "unconverted_count": cost["unconverted_count"],
        "due_month": sum(item.to_inr(item.expected_minor) for item in occurrences),
        "paid_month": sum(paid_inr(item) for item in paid),
        "remaining": sum(item.to_inr(item.remaining_minor) for item in upcoming),
        "overdue": sum(item.to_inr(item.remaining_minor) for item in overdue),
        "upcoming": upcoming,
        "overdue_items": overdue,
        "next": upcoming[0] if upcoming else None,
        "monthly_fixed": cost["monthly_fixed"],
        "annual_recurring": cost["annual"],
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
    # Known payments still owed this month: unpaid bills, recurring rules and commitments,
    # due from today to month end, plus this month's overdue ones. Paid items are already spent.
    from .obligations import due_items
    owed = [item for item in due_items(user_id, month, end, reference=reference) if item.outstanding]
    recurring_total = sum(item.to_inr(item.remaining_minor) for item in owed)
    unconverted_recurring_count = sum(item.inr_rate is None for item in owed)
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
        "method": "Current daily pace, three-month average, and known unpaid bills, recurring payments and commitments.",
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
    if current['income'] == 0 and current['expense'] == 0:
        return {'score': 0, 'status': 'Not enough data', 'has_data': False,
                'factors': {}, 'explanation': 'No income or spending is recorded for this month.',
                'suggestion': 'Record activity or select a month with transactions to review your financial patterns.'}
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


def emi_schedule(user_id, reference=None):
    """Unpaid active loan instalments, including arrears and the next 90 days. Never writes."""
    from .obligations import due_items

    reference = reference or today()
    return [item for item in due_items(user_id, None, reference + timedelta(days=90),
                                       reference=reference, kinds=("commitment",))
            if item.outstanding and item.commitment.commitment_type == "loan"]


def generate_notifications(user_id, reference=None):
    reference = reference or today()
    created = 0
    for occurrence in emi_schedule(user_id, reference):
        loan = occurrence.commitment
        if occurrence.due_date > reference + timedelta(days=occurrence.reminder_days or 0):
            continue
        overdue = occurrence.due_date < reference
        kind = 'emi_overdue' if overdue else 'emi_due'
        created += bool(create_notification(
            user_id, kind, f"{loan.name} EMI {'is overdue' if overdue else 'is due soon'}",
            f"Due {occurrence.due_date}. Review the payment in Commitments.",
            f'{kind}:{loan.id}:{occurrence.due_date}'))
    month = reference.replace(day=1)
    for item in budget_status(user_id, month):
        if item["percent"] >= 100:
            notification = create_notification(user_id, "budget_exceeded", f"{item['budget'].category.name} budget exceeded", "Review this month's spending and budget.", f"budget-exceeded:{item['budget'].id}:{month}")
        elif item["percent"] >= 80:
            notification = create_notification(user_id, "budget_warning", f"{item['budget'].category.name} budget at 80%", "You are approaching this month's spending limit.", f"budget-warning:{item['budget'].id}:{month}")
        else:
            notification = None
        created += bool(notification)
    # Bills and recurring payments remind 7 days ahead; overdue ones remind too.
    from .obligations import due_items
    for item in due_items(user_id, None, reference + timedelta(days=8), reference=reference,
                          kinds=("bill", "recurring")):
        if not item.outstanding:
            continue
        if item.kind == "bill":
            bill = item.source
            if item.due_date < reference:
                kind, title = "bill_overdue", f"{bill.card_name} bill is overdue"
            else:
                kind, title = "bill_due", f"{bill.card_name} bill is due soon"
            created += bool(create_notification(user_id, kind, title, f"Due {bill.due_date}.", f"{kind}:{bill.id}:{bill.due_date}"))
        else:
            rule = item.source
            created += bool(
                create_notification(
                    user_id,
                    "recurring_due",
                    f"{rule.category.name} recurring payment is due",
                    f"Expected {rule.amount_minor / 100:,.2f} on {item.due_date}.",
                    f"recurring-due:{rule.id}:{item.due_date}",
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
            following = step(rule.frequency, due, rule.anchor_day)
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


