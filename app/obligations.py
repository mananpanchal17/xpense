"""Obligations: the one answer to "what is due between two dates".

Sources are credit-card bills, active recurring expense rules and active commitments
(loans/EMIs, subscriptions, rent, ...). Reading never writes: future dates are worked out
in memory and merged with rows already stored. A commitment occurrence row is created
only when the user acts on it (pay, skip, miss) via ``claim_occurrence``.

Interface:
    due_items(user_id, start, end, reference=None, kinds=ALL) -> [DueItem]
        start=None means "everything still on the books": all bills, stored rows of any
        date, and generated dates from each rule's next_due. ``end`` is exclusive and
        capped at reference + 400 days.
    annual_cost(user_id, kinds=("recurring", "commitment")) -> {annual, monthly, unconverted_count}
    step(frequency, due, anchor_day) -> next date or None
    claim_occurrence(commitment, due_date) -> stored CommitmentOccurrence or None
"""
import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import joinedload

from .extensions import db
from .models import (Commitment, CommitmentOccurrence, CreditCardBill, RecurringExpense,
    RecurringOccurrence)

ALL = ("bill", "recurring", "commitment")
MAX_DAYS_AHEAD = 400
MAX_PER_SOURCE = 1000
OUTSTANDING = ("upcoming", "overdue", "partial", "missed")
PER_YEAR = {"weekly": 52, "monthly": 12, "quarterly": 4, "yearly": 1, "once": 0}
_KIND_ORDER = {kind: index for index, kind in enumerate(ALL)}


def step(frequency, due, anchor_day):
    """Next due date. Month-based steps keep the anchor day (31 Jan -> 28 Feb -> 31 Mar)."""
    if frequency == "once":
        return None
    if frequency == "weekly":
        return due + timedelta(days=7)
    months = {"monthly": 1, "quarterly": 3, "yearly": 12}[frequency]
    year, month_index = divmod(due.year * 12 + due.month - 1 + months, 12)
    if year > 9998:
        return None
    month = month_index + 1
    return date(year, month, min(anchor_day, calendar.monthrange(year, month)[1]))


def _dates(frequency, first, anchor_day, start, end, until=None):
    due, count = first, 0
    while due is not None and due < end and (until is None or due <= until) and count < MAX_PER_SOURCE:
        if start is None or due >= start:
            yield due
            count += 1
        due = step(frequency, due, anchor_day)


@dataclass
class DueItem:
    kind: str                     # bill | recurring | commitment
    name: str
    due_date: date
    expected_minor: int
    currency: str
    status: str                   # paid | partial | skipped | missed | overdue | upcoming
    actual_minor: int = 0
    inr_minor: int | None = None  # None: no cached rate
    inr_rate: Decimal | None = None
    reminder_days: int = 7
    source: object = None         # the bill, rule or commitment
    occurrence: object = None     # stored occurrence row, if any
    payments: list = field(default_factory=list)

    @property
    def id(self):
        return self.occurrence.id if self.occurrence is not None else (self.source.id if self.kind == "bill" else None)

    @property
    def commitment(self):
        return self.source if self.kind == "commitment" else None

    @property
    def commitment_id(self):
        return self.source.id if self.kind == "commitment" else None

    @property
    def transaction_id(self):
        return getattr(self.occurrence, "transaction_id", None)

    @property
    def remaining_minor(self):
        return max(0, self.expected_minor - self.actual_minor) if self.outstanding else 0

    def to_inr(self, amount):
        """INR value of a native amount at this item's rate; 0 when no rate is cached."""
        if self.inr_rate is None:
            return 0
        return int((Decimal(amount) * self.inr_rate).quantize(Decimal("1")))

    @property
    def outstanding(self):
        return self.status in OUTSTANDING

    @property
    def is_overdue(self):
        return self.outstanding and self.status != "upcoming" and self._past

    _past: bool = False


class _Rates:
    """Cached INR rates per currency for one call; None when no rate is cached."""

    def __init__(self):
        self.rates = {"INR": Decimal("1")}

    def rate(self, currency):
        if currency not in self.rates:
            from .services import exchange_rate
            try:
                self.rates[currency] = exchange_rate(currency, "INR")[0]
            except ValueError:
                self.rates[currency] = None
        return self.rates[currency]

    def inr(self, amount, currency):
        rate = self.rate(currency)
        return None if rate is None else int((Decimal(amount) * rate).quantize(Decimal("1")))


def _status(stored, due, reference):
    if stored in ("paid", "partial", "skipped", "missed"):
        return stored
    return "overdue" if due < reference else "upcoming"


def _today():
    from . import services  # looked up at call time so tests can patch services.today
    return services.today()


def _make(kind, name, due, expected, currency, stored_status, reference, rates, **extra):
    status = _status(stored_status, due, reference)
    item = DueItem(kind=kind, name=name, due_date=due, expected_minor=expected, currency=currency,
        status=status, inr_minor=rates.inr(expected, currency), inr_rate=rates.rate(currency), **extra)
    item._past = due < reference
    return item


def _active_commitments(user_id):
    return db.session.scalars(db.select(Commitment).options(joinedload(Commitment.account)).where(
        Commitment.user_id == user_id, Commitment.status == "active",
        db.or_(Commitment.outstanding_minor.is_(None), Commitment.outstanding_minor > 0,
               Commitment.commitment_type != "loan"))).all()


def _active_rules(user_id):
    return db.session.scalars(db.select(RecurringExpense).options(
        joinedload(RecurringExpense.account), joinedload(RecurringExpense.category)).where(
        RecurringExpense.user_id == user_id, RecurringExpense.active.is_(True),
        RecurringExpense.status == "active")).all()


def _in_range(column, start, end):
    return column < end if start is None else db.and_(column >= start, column < end)


def due_items(user_id, start, end, reference=None, kinds=ALL):
    reference = reference or _today()
    end = min(end, reference + timedelta(days=MAX_DAYS_AHEAD))
    rates = _Rates()
    items = []
    if "bill" in kinds:
        bills = db.session.scalars(db.select(CreditCardBill).where(
            CreditCardBill.user_id == user_id, _in_range(CreditCardBill.due_date, start, end),
            *( [CreditCardBill.paid.is_(False)] if start is None else []))).all()
        for bill in bills:
            items.append(_make("bill", bill.card_name, bill.due_date, bill.amount_minor, "INR",
                "paid" if bill.paid else None, reference, rates, source=bill,
                actual_minor=bill.amount_minor if bill.paid else 0))
    if "recurring" in kinds:
        for rule in _active_rules(user_id):
            currency = rule.account.currency if rule.account else "INR"
            stored = {row.due_date: row for row in db.session.scalars(db.select(RecurringOccurrence).where(
                RecurringOccurrence.recurring_id == rule.id, _in_range(RecurringOccurrence.due_date, start, end)))}
            dates = set(stored) | set(_dates(rule.frequency, rule.next_due, rule.anchor_day, start, end))
            for due in sorted(dates):
                row = stored.get(due)
                items.append(_make("recurring", rule.category.name, due, rule.amount_minor, currency,
                    row.status if row else None, reference, rates, source=rule, occurrence=row,
                    actual_minor=rule.amount_minor if row is not None and row.status == "paid" else 0))
    if "commitment" in kinds:
        for commitment in _active_commitments(user_id):
            currency = commitment.account.currency if commitment.account else "INR"
            stored = {row.due_date: row for row in db.session.scalars(db.select(CommitmentOccurrence).where(
                CommitmentOccurrence.commitment_id == commitment.id,
                CommitmentOccurrence.status != "cancelled",
                _in_range(CommitmentOccurrence.due_date, start, end)))}
            generated = _dates(commitment.frequency, commitment.next_due, commitment.next_due.day,
                start, end, commitment.end_date)
            for due in sorted(set(stored) | set(generated)):
                row = stored.get(due)
                items.append(_make("commitment", commitment.name, due,
                    row.expected_minor if row else commitment.amount_minor, currency,
                    row.status if row else None, reference, rates, source=commitment, occurrence=row,
                    actual_minor=row.actual_minor if row else 0, reminder_days=commitment.reminder_days,
                    payments=list(row.payments) if row else []))
    items.sort(key=lambda item: (item.due_date, _KIND_ORDER[item.kind], item.name))
    return items


def annual_cost(user_id, kinds=("recurring", "commitment")):
    """Yearly cost of repeating obligations in INR; weekly counts 52 times a year."""
    rates = _Rates()
    sources = []
    if "recurring" in kinds:
        sources += [(r.amount_minor, r.frequency, r.account) for r in _active_rules(user_id)]
    if "commitment" in kinds:
        sources += [(c.amount_minor, c.frequency, c.account) for c in _active_commitments(user_id)]
    annual, unconverted = 0, 0
    for amount, frequency, account in sources:
        value = rates.inr(amount * PER_YEAR[frequency], account.currency if account else "INR")
        if value is None:
            unconverted += 1
        else:
            annual += value
    return {"annual": annual, "monthly": round(annual / 12), "unconverted_count": unconverted,
            "monthly_fixed": sum(rates.inr(amount, account.currency if account else "INR") or 0
                                 for amount, frequency, account in sources if frequency == "monthly")}


def is_due_date(commitment, due_date):
    if due_date < commitment.next_due:
        return False
    return any(due == due_date for due in _dates(commitment.frequency, commitment.next_due,
        commitment.next_due.day, due_date, due_date + timedelta(days=1), commitment.end_date))


def claim_occurrence(commitment, due_date):
    """Stored occurrence for a real due date, created on first use; None if not a due date."""
    row = db.session.scalar(db.select(CommitmentOccurrence).where(
        CommitmentOccurrence.commitment_id == commitment.id, CommitmentOccurrence.due_date == due_date))
    if row is not None:
        return row
    if not is_due_date(commitment, due_date):
        return None
    from sqlalchemy.exc import IntegrityError
    try:
        with db.session.begin_nested():
            row = CommitmentOccurrence(commitment_id=commitment.id, due_date=due_date,
                expected_minor=commitment.amount_minor, actual_minor=0, late_fee_minor=0, status="scheduled")
            db.session.add(row)
        return row
    except IntegrityError:
        # Another request claimed the same date first; the unique constraint kept one row.
        return db.session.scalar(db.select(CommitmentOccurrence).where(
            CommitmentOccurrence.commitment_id == commitment.id, CommitmentOccurrence.due_date == due_date))
