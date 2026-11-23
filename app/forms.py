"""Shared server-side validation; browser validation is only a convenience."""

from decimal import Decimal
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed
from wtforms import (
    StringField,
    PasswordField,
    BooleanField,
    SelectField,
    DecimalField,
    IntegerField,
    DateField,
    TextAreaField,
    SubmitField,
)
from wtforms.validators import DataRequired, InputRequired, Email, Length, EqualTo, Optional, ValidationError, NumberRange

PAYMENTS = [(x, x) for x in ["Cash", "UPI", "Debit card", "Credit card", "Bank transfer", "Other"]]
CURRENCIES = [
    ("INR", "INR - Indian rupee"),
    ("USD", "USD - US dollar"),
    ("EUR", "EUR - Euro"),
    ("GBP", "GBP - Pound sterling"),
    ("JPY", "JPY - Japanese yen"),
    ("AED", "AED - UAE dirham"),
    ("CAD", "CAD - Canadian dollar"),
    ("AUD", "AUD - Australian dollar"),
    ("SGD", "SGD - Singapore dollar"),
    ("CHF", "CHF - Swiss franc"),
]


def money_valid(form, field):
    value = field.data
    if (
        value is None
        or not value.is_finite()
        or not Decimal("0.01") <= value <= Decimal("999999999.99")
    ):
        raise ValidationError("Enter an amount from 0.01 to 999,999,999.99.")
    if value != value.quantize(Decimal("0.01")):
        raise ValidationError("Use at most two decimal places.")


def nonnegative_money_valid(form, field):
    value = field.data
    if (
        value is None
        or not value.is_finite()
        or not Decimal("0.00") <= value <= Decimal("999999999.99")
    ):
        raise ValidationError("Enter an amount from 0 to 999,999,999.99.")
    if value != value.quantize(Decimal("0.01")):
        raise ValidationError("Use at most two decimal places.")


def plain_text(value):
    # Keep notes as text, not HTML; Jinja escapes them at output time.
    if isinstance(value, str):
        return "".join(c for c in value.strip() if c in "\n\t" or ord(c) >= 32)
    return value


def bounded_rate(minimum, maximum):
    def validate(form, field):
        value = field.data
        if value is None or not value.is_finite() or not minimum <= value <= maximum:
            raise ValidationError(f"Enter a finite percentage from {minimum} to {maximum}.")
    validate.minimum = minimum
    validate.maximum = maximum
    return validate


class BaseForm(FlaskForm):
    submit = SubmitField("Save")


class RegisterForm(BaseForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=80)], filters=[plain_text])
    email = StringField(
        "Email", validators=[DataRequired(), Email(), Length(max=254)], filters=[plain_text]
    )
    password = PasswordField("Password", validators=[DataRequired(), Length(min=10, max=128)])
    confirm = PasswordField("Confirm password", validators=[DataRequired(), EqualTo("password")])


class LoginForm(BaseForm):
    email = StringField(
        "Email", validators=[DataRequired(), Email(), Length(max=254)], filters=[plain_text]
    )
    password = PasswordField("Password", validators=[DataRequired(), Length(max=128)])
    remember = BooleanField("Remember me for 14 days")


class EmailForm(BaseForm):
    email = StringField(
        "Email", validators=[DataRequired(), Email(), Length(max=254)], filters=[plain_text]
    )


class ResetForm(BaseForm):
    password = PasswordField("New password", validators=[DataRequired(), Length(min=10, max=128)])
    confirm = PasswordField(
        "Confirm new password", validators=[DataRequired(), EqualTo("password")]
    )


class ProfileForm(BaseForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=80)], filters=[plain_text])
    email = StringField(
        "Email", validators=[DataRequired(), Email(), Length(max=254)], filters=[plain_text]
    )
    currency = SelectField("Base currency for converted totals", choices=CURRENCIES)
    current_password = PasswordField(
        "Current password to confirm changes", validators=[DataRequired(), Length(max=128)]
    )
    password = PasswordField(
        "New password (optional)", validators=[Optional(), Length(min=10, max=128)]
    )
    confirm = PasswordField("Confirm new password", validators=[EqualTo("password")])


class DeleteAccountForm(BaseForm):
    password = PasswordField("Current password", validators=[DataRequired(), Length(max=128)])
    confirmation = StringField("Type DELETE to confirm", validators=[DataRequired()])

    def validate_confirmation(self, field):
        if field.data != "DELETE":
            raise ValidationError("Type DELETE exactly.")


class TransactionForm(BaseForm):
    kind = SelectField("Type", choices=[("expense", "Expense"), ("income", "Income")])
    amount = DecimalField("Amount", validators=[DataRequired(), money_valid])
    category_id = SelectField("Category", coerce=int, validators=[DataRequired()])
    account_id = SelectField("Account (optional)", coerce=int, validators=[Optional()])
    date = DateField("Date", validators=[DataRequired()])
    payment_method = SelectField("Payment method", choices=PAYMENTS)
    notes = TextAreaField("Notes", validators=[Length(max=500)], filters=[plain_text])

    def validate_date(self, field):
        if not 1900 <= field.data.year <= 9998:
            raise ValidationError("Use a year between 1900 and 9998.")


class CategoryForm(BaseForm):
    name = StringField(
        "Category name", validators=[DataRequired(), Length(max=60)], filters=[plain_text]
    )


class BudgetForm(BaseForm):
    category_id = SelectField("Category", coerce=int, validators=[DataRequired()])
    month = StringField("Month (YYYY-MM)", validators=[DataRequired()])
    amount = DecimalField("Monthly limit", validators=[DataRequired(), money_valid])

    def validate_month(self, field):
        import re
        from datetime import date

        if not re.fullmatch(r"\d{4}-\d{2}", field.data):
            raise ValidationError("Use YYYY-MM, e.g. 2026-10.")
        try:
            parsed = date.fromisoformat(field.data + "-01")
            if not 1900 <= parsed.year <= 9998:
                raise ValueError()
        except ValueError:
            raise ValidationError("Enter a valid month.")


class RecurringForm(BaseForm):
    category_id = SelectField("Category", coerce=int, validators=[DataRequired()])
    account_id = SelectField("Account (optional)", coerce=int, validators=[Optional()])
    amount = DecimalField("Amount", validators=[DataRequired(), money_valid])
    next_due = DateField("First / next due date", validators=[DataRequired()])
    frequency = SelectField(
        "Repeat", choices=[("weekly", "Weekly"), ("monthly", "Monthly"), ("yearly", "Yearly")]
    )
    payment_method = SelectField("Payment method", choices=PAYMENTS)
    notes = TextAreaField("Notes", validators=[Length(max=500)], filters=[plain_text])
    active = BooleanField("Active", default=True)

    def validate_next_due(self, field):
        from .services import today
        from datetime import timedelta

        if not today() - timedelta(days=366) <= field.data <= today() + timedelta(days=3660):
            raise ValidationError("Use a date within the last year or next 10 years.")


class CreditCardBillForm(BaseForm):
    card_name = StringField(
        "Card name", validators=[DataRequired(), Length(max=80)], filters=[plain_text]
    )
    amount = DecimalField("Bill amount", validators=[DataRequired(), money_valid])
    due_date = DateField("Due date", validators=[DataRequired()])
    notes = TextAreaField("Notes", validators=[Length(max=500)], filters=[plain_text])

    def validate_due_date(self, field):
        if not 1900 <= field.data.year <= 9998:
            raise ValidationError("Use a year between 1900 and 9998.")


class SavingsGoalForm(BaseForm):
    name = StringField(
        "Goal name", validators=[DataRequired(), Length(max=100)], filters=[plain_text]
    )
    target_amount = DecimalField("Target amount", validators=[DataRequired(), money_valid])
    target_date = DateField("Target date", validators=[DataRequired()])
    description = TextAreaField("Description", validators=[Length(max=500)], filters=[plain_text])

    def validate_target_date(self, field):
        if not 1900 <= field.data.year <= 9998:
            raise ValidationError("Use a year between 1900 and 9998.")


class ContributionForm(BaseForm):
    amount = DecimalField("Contribution or withdrawal", validators=[DataRequired(), money_valid])
    action = SelectField(
        "Action", choices=[("add", "Add contribution"), ("withdraw", "Withdraw savings")]
    )
    contribution_date = DateField("Date", validators=[DataRequired()])
    note = StringField("Note", validators=[Length(max=300)], filters=[plain_text])


ACCOUNT_TYPES = [
    ("bank", "Bank account"),
    ("cash", "Cash"),
    ("wallet", "Digital wallet"),
    ("credit_card", "Credit card"),
    ("savings", "Savings account"),
    ("current", "Current account"),
    ("prepaid", "Prepaid card"),
    ("upi", "UPI-linked account"),
    ("custom", "Custom account"),
]


class AccountForm(BaseForm):
    name = StringField("Display name", validators=[DataRequired(), Length(max=100)], filters=[plain_text])
    account_type = SelectField("Account type", choices=ACCOUNT_TYPES, validators=[DataRequired()])
    provider = StringField("Bank or provider", validators=[Optional(), Length(max=100)], filters=[plain_text])
    logo_key = StringField("Logo key (optional)", validators=[Optional(), Length(max=80)], filters=[plain_text])
    last_four = StringField("Last four digits (optional)", validators=[Optional(), Length(min=4, max=4)], filters=[plain_text])
    opening_balance = DecimalField("Opening balance", validators=[Optional(), nonnegative_money_valid], default=0)
    currency = SelectField("Currency", choices=CURRENCIES, validators=[DataRequired()])
    color = SelectField("Colour", choices=[("teal", "Teal"), ("blue", "Blue"), ("violet", "Violet"), ("amber", "Amber")])
    icon = StringField("Icon name", validators=[Optional(), Length(max=40)], default="wallet2", filters=[plain_text])
    linked_account_id = SelectField("Linked bank account (optional)", coerce=int, validators=[Optional()])
    include_in_total = BooleanField("Include in total balance", default=True)
    active = BooleanField("Active", default=True)
    notes = TextAreaField("Notes", validators=[Length(max=500)], filters=[plain_text])

    def validate_last_four(self, field):
        if field.data and not field.data.isdigit():
            raise ValidationError("Use exactly four digits.")


class TransferForm(BaseForm):
    source_account_id = SelectField("From account", coerce=int, validators=[DataRequired()])
    destination_account_id = SelectField("To account", coerce=int, validators=[DataRequired()])
    amount = DecimalField("Source amount", validators=[DataRequired(), money_valid])
    destination_amount = DecimalField("Destination amount", validators=[Optional(), money_valid])
    fee = DecimalField("Fee", validators=[Optional(), nonnegative_money_valid], default=0)
    transfer_date = DateField("Transfer date", validators=[DataRequired()])
    notes = TextAreaField("Notes", validators=[Length(max=500)], filters=[plain_text])


class BalanceAdjustmentForm(BaseForm):
    account_id = SelectField("Account", coerce=int, validators=[DataRequired()])
    actual_balance = DecimalField("Actual balance", validators=[InputRequired(), nonnegative_money_valid])
    adjustment_date = DateField("Date", validators=[DataRequired()])
    reason = StringField("Reason", validators=[DataRequired(), Length(max=200)], filters=[plain_text])


class InvestmentRedemptionForm(BaseForm):
    amount = DecimalField("Proceeds received", validators=[InputRequired(), money_valid])
    redemption_date = DateField("Date received", validators=[DataRequired()])
    account_id = SelectField("Destination account", coerce=int, validators=[DataRequired()])
    notes = TextAreaField("Notes", validators=[Length(max=500)], filters=[plain_text])


class InvestmentForm(BaseForm):
    name = StringField("Investment name", validators=[DataRequired(), Length(max=120)], filters=[plain_text])
    investment_type = SelectField("Investment type", validators=[DataRequired()])
    institution = StringField("Institution / provider", validators=[Optional(), Length(max=120)], filters=[plain_text])
    account_id = SelectField("Funding account (optional)", coerce=int, validators=[Optional()])
    currency = SelectField("Currency", choices=CURRENCIES, validators=[DataRequired()])
    purchase_date = DateField("Start / purchase date", validators=[DataRequired()])
    amount_invested = DecimalField("Amount invested", validators=[DataRequired(), money_valid])
    current_value = DecimalField("Current value", validators=[Optional(), nonnegative_money_valid])
    maturity_date = DateField("Maturity date (optional)", validators=[Optional()])
    expected_return_rate = DecimalField("Expected return % (optional)", validators=[Optional(), bounded_rate(-100, 1000)])
    interest_earned = DecimalField("Interest earned", validators=[Optional(), nonnegative_money_valid], default=0)
    dividend_income = DecimalField("Dividend income", validators=[Optional(), nonnegative_money_valid], default=0)
    reference = StringField("Masked reference / folio", validators=[Optional(), Length(max=80)], filters=[plain_text])
    status = SelectField("Status", choices=[("active", "Active"), ("matured", "Matured"), ("closed", "Closed"), ("redeemed", "Redeemed")])
    notes = TextAreaField("Notes", validators=[Length(max=500)], filters=[plain_text])


COMMITMENT_TYPES = [
    ("loan", "Loan / EMI"),
    ("credit_card", "Credit-card payment"),
    ("subscription", "Subscription"),
    ("utility", "Utility bill"),
    ("rent", "Rent"),
    ("insurance", "Insurance"),
    ("education", "Education fee"),
    ("tax", "Tax"),
    ("maintenance", "Maintenance"),
    ("bill", "General recurring bill"),
    ("one_time", "One-time future payment"),
    ("custom", "Custom type"),
]


class CommitmentForm(BaseForm):
    name = StringField("Commitment name", validators=[DataRequired(), Length(max=120)], filters=[plain_text])
    commitment_type = SelectField("Type", choices=COMMITMENT_TYPES)
    amount = DecimalField("Amount / EMI", validators=[DataRequired(), money_valid])
    start_date = DateField("Start date", validators=[DataRequired()])
    next_due = DateField("Next due date", validators=[DataRequired()])
    end_date = DateField("End date (optional)", validators=[Optional()])
    frequency = SelectField(
        "Frequency",
        choices=[
            ("weekly", "Weekly"),
            ("monthly", "Monthly"),
            ("quarterly", "Quarterly"),
            ("yearly", "Yearly"),
            ("once", "One time"),
        ],
    )
    category_id = SelectField("Category", coerce=int, validators=[Optional()])
    account_id = SelectField("Payment account (optional)", coerce=int, validators=[Optional()])
    payment_method = SelectField("Payment method / account", choices=PAYMENTS)
    autopay = BooleanField("Autopay enabled")
    reminder_days = SelectField(
        "Remind me", choices=[("7", "7 days before"), ("3", "3 days before"), ("1", "1 day before")]
    )
    lender = StringField("Lender (loans)", validators=[Optional(), Length(max=120)], filters=[plain_text])
    original_principal = DecimalField("Original principal (loans)", validators=[Optional(), money_valid])
    outstanding = DecimalField("Outstanding balance (loans)", validators=[Optional(), nonnegative_money_valid])
    annual_rate = DecimalField("Annual interest rate % (loans)", validators=[Optional(), bounded_rate(0, 1000)])
    tenure_months = IntegerField("Original tenure in months (loans)", validators=[Optional(), NumberRange(min=1, max=1200)])
    processing_fee = DecimalField(
        "Processing fee (loans)", validators=[Optional(), nonnegative_money_valid]
    )
    loan_reference = StringField("Loan reference (optional)", validators=[Optional(), Length(max=80)], filters=[plain_text])
    notes = TextAreaField("Notes", validators=[Length(max=500)], filters=[plain_text])

    def validate_next_due(self, field):
        if not 1900 <= field.data.year <= 9998:
            raise ValidationError("Use a year between 1900 and 9998.")

    def validate_end_date(self, field):
        if field.data and self.next_due.data and field.data < self.next_due.data:
            raise ValidationError("End date cannot be before the next due date.")


class CommitmentOccurrenceForm(BaseForm):
    actual_amount = DecimalField("Actual paid amount", validators=[DataRequired(), money_valid])
    paid_date = DateField("Payment date", validators=[DataRequired()])
    late_fee = DecimalField("Late fee", validators=[Optional(), money_valid])
    notes = TextAreaField("Notes", validators=[Length(max=500)], filters=[plain_text])


class ImportForm(BaseForm):
    file = FileField(
        "CSV file (maximum 2 MB / 2,000 rows)", validators=[FileRequired(), FileAllowed(["csv"])]
    )
