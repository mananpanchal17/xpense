"""Dashboard charts, monthly/yearly summaries, and filtered CSV/PDF exports."""

import csv
import io
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from xml.sax.saxutils import escape
from flask import Blueprint, render_template, request, abort, Response, send_file, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from ..ledger import period_totals, reporting_amount, reporting_entries, report_kind, signed_value, category_rollup, unconverted_count, csv_safe
from ..extensions import db
from ..models import Transaction, Category, CreditCardBill, SavingsGoal, Notification, Investment
from ..services import (
    today,
    month_end,
    budget_status,
    filtered_transactions,
    log_event,
    financial_forecast,
    financial_health,
    financial_insights,
    generate_notifications,
    goal_metrics,
    commitment_summary,
    account_summary,
    investment_summary,
)

bp = Blueprint("reports", __name__)


def totals(start=None, end=None):
    return period_totals(current_user.id, start, end)


def selected_dashboard_month():
    try:
        month = date.fromisoformat(request.args.get('month', today().strftime('%Y-%m')) + '-01')
        if not 1900 <= month.year <= 9998:
            raise ValueError()
        return month
    except ValueError:
        abort(400)


@bp.get("/")
@login_required
def dashboard():
    start = selected_dashboard_month()
    reference = today() if start == today().replace(day=1) else month_end(start) - timedelta(days=1)
    generate_notifications(current_user.id)
    recent = db.session.scalars(
        db.select(Transaction)
        .where(Transaction.user_id == current_user.id, reporting_entries(), Transaction.date >= start, Transaction.date < month_end(start))
        .order_by(Transaction.date.desc(), Transaction.id.desc())
        .limit(6)
    ).all()
    goals = db.session.scalars(
        db.select(SavingsGoal)
        .where(SavingsGoal.user_id == current_user.id, SavingsGoal.status == "active")
        .order_by(SavingsGoal.target_date)
        .limit(3)
    ).all()
    upcoming_bills = db.session.scalars(
        db.select(CreditCardBill)
        .where(
            CreditCardBill.user_id == current_user.id,
            CreditCardBill.paid.is_(False),
            CreditCardBill.due_date <= today() + timedelta(days=14),
        )
        .order_by(CreditCardBill.due_date)
        .limit(5)
    ).all()
    commitment_data = commitment_summary(current_user.id)
    investment_data = investment_summary(current_user.id)
    return render_template(
        "reports/dashboard.html",
        title="Overview",
        summary=totals(start, month_end(start)),
        dashboard_month=start,
        has_period_activity=bool(recent),
        month_spend=totals(start, month_end(start))["expense"],
        budgets=budget_status(current_user.id, start),
        recent=recent,
        insights=financial_insights(current_user.id, reference)[:3],
        forecast=financial_forecast(current_user.id, reference),
        health=financial_health(current_user.id, reference),
        goals=[(goal, goal_metrics(goal)) for goal in goals],
        upcoming_bills=upcoming_bills,
        commitment_data=commitment_data,
        account_items=account_summary(current_user.id),
        investment_data=investment_data,
    )


@bp.get("/reports/chart-data")
@login_required
def chart_data():
    month = selected_dashboard_month()
    first = date(month.year - 1, month.month + 1, 1) if month.month < 12 else date(month.year, 1, 1)
    rows = db.session.scalars(
        db.select(Transaction).where(
            Transaction.user_id == current_user.id,
            reporting_entries(),
            Transaction.date >= first,
            Transaction.date < month_end(month),
        )
    ).all()
    monthly = defaultdict(lambda: {"income": 0, "expense": 0})
    for row in rows:
        monthly[row.date.strftime("%Y-%m")]["expense" if row.entry_type == "refund" else row.kind] += signed_value(row)
    labels = []
    cursor = first
    while cursor <= month:
        labels.append(cursor.strftime("%Y-%m"))
        cursor = month_end(cursor)
    categories = db.session.execute(
        db.select(Category.name, func.sum(reporting_amount()))
        .join(Transaction, Transaction.category_id == Category.id)
        .where(
            Transaction.user_id == current_user.id,
            reporting_entries(),
            report_kind() == "expense",
            Transaction.date >= month,
            Transaction.date < month_end(month),
        )
        .group_by(Category.id, Category.name)
        .order_by(Category.name)
    ).all()
    return jsonify(
        {
            "labels": labels,
            "income": [monthly[x]["income"] / 100 for x in labels],
            "expense": [monthly[x]["expense"] / 100 for x in labels],
            "category_labels": [r[0] for r in categories],
            "category_values": [r[1] / 100 for r in categories],
            "currency": "INR",
            "unconverted_count": unconverted_count(current_user.id, first, month_end(month)),
        }
    )


@bp.get("/reports/<period>")
@login_required
def report(period):
    if period not in ["monthly", "yearly"]:
        abort(404)
    try:
        year = int(request.args.get("year", today().year))
        month = int(request.args.get("month", today().month))
        if not 1900 <= year <= 9998 or not 1 <= month <= 12:
            raise ValueError()
    except ValueError:
        abort(400)
    start = date(year, month if period == "monthly" else 1, 1)
    end = month_end(start) if period == "monthly" else date(year + 1, 1, 1)
    rows = category_rollup(current_user.id, start, end)
    month_rows = []
    if period == "yearly":
        cursor = start
        while cursor < end:
            month_rows.append((cursor.strftime("%B"), totals(cursor, month_end(cursor))))
            cursor = month_end(cursor)
    from datetime import timedelta

    return render_template(
        "reports/report.html",
        title=period.title() + " report",
        period=period,
        year=year,
        month=month,
        summary=totals(start, end),
        rows=rows,
        month_rows=month_rows,
        start=start,
        end=end - timedelta(days=1),
    )


@bp.get("/export/csv")
@login_required
def export_csv():
    rows = db.session.scalars(filtered_transactions(current_user.id)).all()
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["kind", "amount", "category", "date", "payment_method", "notes", "currency", "base_amount_minor", "exchange_rate", "exchange_rate_date", "exchange_rate_source", "entry_type"])
    for row in rows:
        writer.writerow(
            [
                row.kind,
                f"{Decimal(row.amount_minor) / 100:.2f}",
                csv_safe(row.category.name),
                row.date.isoformat(),
                row.payment_method,
                csv_safe(row.notes),
                row.currency or "INR",
                row.base_amount_minor if row.base_amount_minor is not None else (row.amount_minor if row.currency in (None, "", "INR") else ""),
                str(row.exchange_rate or ""),
                row.exchange_rate_date.isoformat() if row.exchange_rate_date else "",
                csv_safe(row.exchange_rate_source or ""),
                row.entry_type,
            ]
        )
    log_event(current_user.id, "Exported CSV")
    db.session.commit()
    return Response(
        "\ufeff" + buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )


@bp.get("/export/pdf")
@login_required
def export_pdf():
    rows = db.session.scalars(filtered_transactions(current_user.id).limit(2001)).all()
    if len(rows) > 2000:
        from flask import flash, redirect, url_for

        flash("PDF export is limited to 2,000 rows. Narrow the date range or use CSV.", "warning")
        return redirect(url_for("expenses.index", **request.args))
    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output, pagesize=landscape(A4), rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30
    )
    styles = getSampleStyleSheet()
    # DejaVu is used when available; bundling a Unicode font is described in README.
    import os
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    font_file = os.path.join(os.path.dirname(__file__), "..", "static", "fonts", "DejaVuSans.ttf")
    font = "Helvetica"
    if os.path.exists(font_file):
        if "DejaVuSans" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("DejaVuSans", font_file))
        font = "DejaVuSans"
    styles["BodyText"].fontName = font
    styles["BodyText"].fontSize = 8
    styles["BodyText"].leading = 11

    def paragraph(value):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), styles["BodyText"])

    income = sum(signed_value(r) for r in rows if r.kind == "income" and r.entry_type == "normal")
    expense = sum(signed_value(r) for r in rows if (r.kind == "expense" and r.entry_type == "normal") or r.entry_type == "refund")
    story = [
        Paragraph("Xpense | Transaction report", styles["Title"]),
        paragraph(
            f"Reporting currency: INR | Generated: {today()} | {len(rows)} records | Entries missing INR snapshots: {sum(1 for row in rows if row.base_amount_minor is None and row.currency not in (None, '', 'INR'))}"
        ),
        paragraph(
            f"Income: {Decimal(income)/100:,.2f} | Expenses: {Decimal(expense)/100:,.2f} | Balance: {Decimal(income-expense)/100:,.2f}"
        ),
        Spacer(1, 15),
    ]
    data = [[paragraph(x) for x in ["Date", "Type", "Category", "Amount", "Payment", "Notes"]]]
    for row in rows:
        data.append(
            [
                paragraph(x)
                for x in [
                    row.date,
                    row.kind.title(),
                    row.category.name,
                    f"INR {Decimal(signed_value(row))/100:,.2f} ({row.currency or 'INR'} {Decimal(row.amount_minor)/100:,.2f})",
                    row.payment_method,
                    row.notes,
                ]
            ]
        )
    table = Table(data, colWidths=[65, 55, 100, 90, 90, 375], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dceee9")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
            ]
        )
    )
    story.append(table)

    def footer(canvas, document):
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(810, 16, f"Page {document.page}")

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    output.seek(0)
    log_event(current_user.id, "Exported PDF")
    db.session.commit()
    return send_file(
        output, mimetype="application/pdf", as_attachment=True, download_name="expense-report.pdf"
    )
