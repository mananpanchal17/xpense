"""Database tables. All money values are integer minor units (paise/cents)."""

import secrets
from datetime import datetime, timezone
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from .extensions import db


def utcnow():
    # Store naive UTC consistently in SQLite; dates use the configured local zone.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(254), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="INR")
    session_token = db.Column(
        db.String(64), unique=True, nullable=False, default=lambda: secrets.token_hex(32)
    )
    failed_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    categories = db.relationship("Category", backref="user", cascade="all, delete-orphan")
    transactions = db.relationship("Transaction", backref="user", cascade="all, delete-orphan")
    budgets = db.relationship("Budget", backref="user", cascade="all, delete-orphan")
    recurring_rules = db.relationship(
        "RecurringExpense", backref="user", cascade="all, delete-orphan"
    )
    credit_card_bills = db.relationship(
        "CreditCardBill", backref="user", cascade="all, delete-orphan"
    )
    savings_goals = db.relationship("SavingsGoal", backref="user", cascade="all, delete-orphan")
    notifications = db.relationship(
        "Notification", backref="user", cascade="all, delete-orphan"
    )
    commitments = db.relationship("Commitment", backref="user", cascade="all, delete-orphan")
    accounts = db.relationship("Account", backref="user", cascade="all, delete-orphan")
    transfers = db.relationship("Transfer", backref="user", cascade="all, delete-orphan")
    investments = db.relationship("Investment", backref="user", cascade="all, delete-orphan")
    logs = db.relationship("ActivityLog", backref="user", cascade="all, delete-orphan")
    assistant_conversations = db.relationship(
        "AssistantConversation", backref="user", cascade="all, delete-orphan"
    )

    def get_id(self):
        # Rotating this invalidates every existing session and remember-me cookie.
        return self.session_token

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        self.session_token = secrets.token_hex(32)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Category(db.Model):
    __table_args__ = (db.UniqueConstraint("user_id", "name"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    # SQLite compares names case-insensitively; Postgres has no NOCASE collation.
    name = db.Column(db.String(60).with_variant(db.String(60, collation="NOCASE"), "sqlite"), nullable=False)


class Transaction(db.Model):
    __table_args__ = (
        db.CheckConstraint("amount_minor > 0 AND amount_minor <= 99999999999"),
        db.CheckConstraint("kind IN ('expense', 'income')"),
        db.UniqueConstraint("recurring_id", "occurrence_date", name="unique_recurring_occurrence"),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=False)
    kind = db.Column(db.String(10), nullable=False)
    amount_minor = db.Column(db.BigInteger, nullable=False)
    date = db.Column(db.Date, nullable=False, index=True)
    payment_method = db.Column(db.String(20), nullable=False)
    notes = db.Column(db.String(500), nullable=False, default="")
    recurring_id = db.Column(db.Integer, db.ForeignKey("recurring_expense.id"))
    occurrence_date = db.Column(db.Date)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), index=True)
    entry_type = db.Column(db.String(12), nullable=False, default="normal")
    transfer_id = db.Column(db.Integer, db.ForeignKey("transfer.id"), index=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), index=True)
    refund_of_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), index=True)
    currency = db.Column(db.String(3))
    base_amount_minor = db.Column(db.BigInteger)
    exchange_rate = db.Column(db.Numeric(20, 10))
    exchange_rate_date = db.Column(db.Date)
    exchange_rate_source = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    category = db.relationship("Category")
    account = db.relationship("Account", back_populates="transactions")


class Budget(db.Model):
    __table_args__ = (
        db.UniqueConstraint("user_id", "category_id", "month"),
        db.CheckConstraint("amount_minor > 0"),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=False)
    month = db.Column(db.Date, nullable=False)  # Always day 1.
    amount_minor = db.Column(db.BigInteger, nullable=False)
    category = db.relationship("Category")


class RecurringExpense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=False)
    amount_minor = db.Column(db.BigInteger, nullable=False)
    payment_method = db.Column(db.String(20), nullable=False)
    notes = db.Column(db.String(500), nullable=False, default="")
    frequency = db.Column(db.String(10), nullable=False)  # weekly, monthly, yearly
    next_due = db.Column(db.Date, nullable=False)
    anchor_day = db.Column(db.Integer, nullable=False)  # Jan 31 -> Feb 28 -> Mar 31
    active = db.Column(db.Boolean, nullable=False, default=True)
    status = db.Column(db.String(12), nullable=False, default="active")
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), index=True)
    account = db.relationship("Account")
    category = db.relationship("Category")
    occurrences = db.relationship(
        "RecurringOccurrence", back_populates="recurring", cascade="all, delete-orphan"
    )


class CreditCardBill(db.Model):
    __table_args__ = (db.CheckConstraint("amount_minor > 0"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    card_name = db.Column(db.String(80), nullable=False)
    amount_minor = db.Column(db.BigInteger, nullable=False)
    due_date = db.Column(db.Date, nullable=False, index=True)
    paid = db.Column(db.Boolean, nullable=False, default=False)
    notes = db.Column(db.String(500), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Commitment(db.Model):
    __table_args__ = (
        db.CheckConstraint("amount_minor > 0"),
        db.CheckConstraint(
            "commitment_type IN ('loan', 'credit_card', 'subscription', 'utility', "
            "'rent', 'insurance', 'education', 'tax', 'maintenance', 'bill', 'one_time', 'custom')"
        ),
        db.CheckConstraint("status IN ('active', 'paused', 'cancelled')"),
        db.CheckConstraint("frequency IN ('weekly', 'monthly', 'quarterly', 'yearly', 'once')"),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), index=True)
    name = db.Column(db.String(120), nullable=False)
    commitment_type = db.Column(db.String(20), nullable=False, index=True)
    amount_minor = db.Column(db.BigInteger, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    next_due = db.Column(db.Date, nullable=False, index=True)
    end_date = db.Column(db.Date)
    frequency = db.Column(db.String(12), nullable=False, default="monthly")
    payment_method = db.Column(db.String(40), nullable=False, default="Other")
    autopay = db.Column(db.Boolean, nullable=False, default=False)
    reminder_days = db.Column(db.Integer, nullable=False, default=7)
    notes = db.Column(db.String(500), nullable=False, default="")
    status = db.Column(db.String(12), nullable=False, default="active", index=True)
    lender = db.Column(db.String(120))
    original_principal_minor = db.Column(db.BigInteger)
    outstanding_minor = db.Column(db.BigInteger)
    annual_rate = db.Column(db.Numeric(8, 4))
    tenure_months = db.Column(db.Integer)
    processing_fee_minor = db.Column(db.BigInteger)
    loan_reference = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    category = db.relationship("Category")
    account = db.relationship("Account")
    occurrences = db.relationship(
        "CommitmentOccurrence", back_populates="commitment", cascade="all, delete-orphan"
    )


class CommitmentOccurrence(db.Model):
    __table_args__ = (
        db.UniqueConstraint("commitment_id", "due_date", name="unique_commitment_due_date"),
        db.CheckConstraint("expected_minor > 0"),
        db.CheckConstraint("actual_minor >= 0"),
        db.CheckConstraint(
            "status IN ('scheduled', 'paid', 'partial', 'skipped', 'missed', 'overdue', 'cancelled')"
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    commitment_id = db.Column(
        db.Integer, db.ForeignKey("commitment.id"), nullable=False, index=True
    )
    due_date = db.Column(db.Date, nullable=False, index=True)
    expected_minor = db.Column(db.BigInteger, nullable=False)
    actual_minor = db.Column(db.BigInteger, nullable=False, default=0)
    paid_date = db.Column(db.Date)
    late_fee_minor = db.Column(db.BigInteger, nullable=False, default=0)
    status = db.Column(db.String(12), nullable=False, default="scheduled")
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"))
    notes = db.Column(db.String(500), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    commitment = db.relationship("Commitment", back_populates="occurrences")
    transaction = db.relationship("Transaction")


class SavingsGoal(db.Model):
    __table_args__ = (
        db.CheckConstraint("target_minor > 0"),
        db.CheckConstraint("saved_minor >= 0"),
        db.CheckConstraint("status IN ('active', 'completed', 'paused')"),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    target_minor = db.Column(db.BigInteger, nullable=False)
    saved_minor = db.Column(db.BigInteger, nullable=False, default=0)
    target_date = db.Column(db.Date, nullable=False, index=True)
    description = db.Column(db.String(500), nullable=False, default="")
    status = db.Column(db.String(12), nullable=False, default="active")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    contributions = db.relationship(
        "GoalContribution", backref="goal", cascade="all, delete-orphan"
    )


class GoalContribution(db.Model):
    __table_args__ = (db.CheckConstraint("amount_minor != 0"),)
    id = db.Column(db.Integer, primary_key=True)
    goal_id = db.Column(db.Integer, db.ForeignKey("savings_goal.id"), nullable=False, index=True)
    amount_minor = db.Column(db.BigInteger, nullable=False)
    contribution_date = db.Column(db.Date, nullable=False, index=True)
    note = db.Column(db.String(300), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Notification(db.Model):
    __table_args__ = (
        db.UniqueConstraint("user_id", "dedupe_key", name="unique_user_notification"),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    kind = db.Column(db.String(40), nullable=False)
    title = db.Column(db.String(140), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    dedupe_key = db.Column(db.String(180), nullable=False)
    read_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)


class RecurringOccurrence(db.Model):
    __table_args__ = (
        db.UniqueConstraint("recurring_id", "due_date", name="unique_recurring_due_date"),
    )
    id = db.Column(db.Integer, primary_key=True)
    recurring_id = db.Column(
        db.Integer, db.ForeignKey("recurring_expense.id"), nullable=False, index=True
    )
    due_date = db.Column(db.Date, nullable=False, index=True)
    status = db.Column(db.String(12), nullable=False, default="scheduled")
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    recurring = db.relationship("RecurringExpense", back_populates="occurrences")
    transaction = db.relationship("Transaction")


class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    event = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class AssistantConversation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    title = db.Column(db.String(120), nullable=False, default="Xpense AI")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    messages = db.relationship(
        "AssistantMessage", backref="conversation", cascade="all, delete-orphan",
        order_by="AssistantMessage.created_at",
    )


class AssistantMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(
        db.Integer, db.ForeignKey("assistant_conversation.id"), nullable=False, index=True
    )
    role = db.Column(db.String(16), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Account(db.Model):
    __table_args__ = (
        db.CheckConstraint("account_type IN ('bank', 'cash', 'wallet', 'credit_card', 'savings', 'current', 'prepaid', 'upi', 'custom')"),
        db.CheckConstraint("opening_balance_minor >= 0"),
        db.CheckConstraint("currency IS NOT NULL AND length(currency) = 3"),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    account_type = db.Column(db.String(20), nullable=False, index=True)
    provider = db.Column(db.String(100), nullable=False, default="")
    logo_key = db.Column(db.String(80))
    last_four = db.Column(db.String(4))
    opening_balance_minor = db.Column(db.BigInteger, nullable=False, default=0)
    currency = db.Column(db.String(3), nullable=False, default="INR")
    color = db.Column(db.String(20), nullable=False, default="teal")
    icon = db.Column(db.String(40), nullable=False, default="wallet2")
    include_in_total = db.Column(db.Boolean, nullable=False, default=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    linked_account_id = db.Column(db.Integer, db.ForeignKey("account.id"))
    notes = db.Column(db.String(500), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    transactions = db.relationship("Transaction", back_populates="account")
    linked_account = db.relationship("Account", remote_side=[id])


class Transfer(db.Model):
    __table_args__ = (
        db.UniqueConstraint("user_id", "reference", name="unique_transfer_reference"),
        db.CheckConstraint("amount_minor > 0"),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    source_account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False)
    destination_account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False)
    amount_minor = db.Column(db.BigInteger, nullable=False)
    destination_amount_minor = db.Column(db.BigInteger, nullable=False)
    fee_minor = db.Column(db.BigInteger, nullable=False, default=0)
    exchange_rate = db.Column(db.Numeric(20, 10), nullable=False, default=1)
    currency = db.Column(db.String(3), nullable=False)
    destination_currency = db.Column(db.String(3), nullable=False)
    transfer_date = db.Column(db.Date, nullable=False)
    notes = db.Column(db.String(500), nullable=False, default="")
    reference = db.Column(db.String(64), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    source_account = db.relationship("Account", foreign_keys=[source_account_id])
    destination_account = db.relationship("Account", foreign_keys=[destination_account_id])


class ExchangeRate(db.Model):
    __table_args__ = (
        db.UniqueConstraint("base_currency", "quote_currency", name="unique_exchange_pair"),
    )
    id = db.Column(db.Integer, primary_key=True)
    base_currency = db.Column(db.String(3), nullable=False, index=True)
    quote_currency = db.Column(db.String(3), nullable=False, index=True)
    rate = db.Column(db.Numeric(20, 10), nullable=False)
    provider = db.Column(db.String(80), nullable=False)
    fetched_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    user_provided = db.Column(db.Boolean, nullable=False, default=False)


class Investment(db.Model):
    __table_args__ = (
        db.CheckConstraint("amount_invested_minor > 0"),
        db.CheckConstraint("current_value_minor >= 0"),
        db.CheckConstraint(
            "status IN ('active', 'matured', 'closed', 'redeemed')"
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), index=True)
    name = db.Column(db.String(120), nullable=False)
    investment_type = db.Column(db.String(40), nullable=False, index=True)
    institution = db.Column(db.String(120), nullable=False, default="")
    currency = db.Column(db.String(3), nullable=False, default="INR")
    purchase_date = db.Column(db.Date, nullable=False, index=True)
    maturity_date = db.Column(db.Date, index=True)
    amount_invested_minor = db.Column(db.BigInteger, nullable=False)
    current_value_minor = db.Column(db.BigInteger, nullable=False)
    expected_return_rate = db.Column(db.Numeric(8, 4))
    interest_earned_minor = db.Column(db.BigInteger, nullable=False, default=0)
    dividend_income_minor = db.Column(db.BigInteger, nullable=False, default=0)
    reference = db.Column(db.String(80))
    notes = db.Column(db.String(500), nullable=False, default="")
    status = db.Column(db.String(12), nullable=False, default="active", index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    account = db.relationship("Account")


class InvestmentRedemption(db.Model):
    __table_args__ = (
        db.UniqueConstraint('investment_id', name='unique_investment_redemption'),
        db.CheckConstraint('amount_minor > 0 AND amount_minor <= 99999999999'),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    investment_id = db.Column(db.Integer, db.ForeignKey('investment.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'), nullable=False)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transaction.id'), nullable=False, unique=True)
    amount_minor = db.Column(db.BigInteger, nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    redemption_date = db.Column(db.Date, nullable=False)
    notes = db.Column(db.String(500), nullable=False, default='')
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    investment = db.relationship('Investment')
    account = db.relationship('Account')
    transaction = db.relationship('Transaction')


class ImportBatch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    token = db.Column(db.String(64), nullable=False, unique=True)
    payload = db.Column(db.Text, nullable=False)
    applied = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class CommitmentPayment(db.Model):
    """Immutable payment portions; the occurrence keeps cumulative legacy totals."""
    __table_args__ = (
        db.UniqueConstraint('occurrence_id', 'payment_token', name='unique_commitment_payment_token'),
        db.CheckConstraint('amount_minor > 0'),
        db.CheckConstraint('late_fee_minor >= 0'),
    )
    id = db.Column(db.Integer, primary_key=True)
    occurrence_id = db.Column(db.Integer, db.ForeignKey('commitment_occurrence.id'), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transaction.id'), unique=True)
    payment_token = db.Column(db.String(64), nullable=False)
    amount_minor = db.Column(db.BigInteger, nullable=False)
    late_fee_minor = db.Column(db.BigInteger, nullable=False, default=0)
    currency = db.Column(db.String(3), nullable=False)
    base_amount_minor = db.Column(db.BigInteger, nullable=False)
    base_late_fee_minor = db.Column(db.BigInteger, nullable=False, default=0)
    paid_date = db.Column(db.Date, nullable=False)
    notes = db.Column(db.String(500), nullable=False, default='')
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    occurrence = db.relationship('CommitmentOccurrence', backref=db.backref('payments', cascade='all, delete-orphan'))
    transaction = db.relationship('Transaction')
