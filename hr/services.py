"""Business operations that touch the database: payroll runs, leave
balances, compliance warnings and dashboard figures."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from . import payroll
from .models import (
    Advance, Employee, LeaveRequest, Office, Payslip, PayrollRun, audit,
)

ZERO = Decimal("0")
Status = Payslip.Status


# ---- Leave ----------------------------------------------------------------

def approved_leave_in(employee, period_start, period_end):
    total = LeaveRequest.objects.filter(
        employee=employee, status=LeaveRequest.Status.APPROVED,
        start_date__gte=period_start, start_date__lte=period_end,
    ).aggregate(total=Sum("days"))["total"]
    return total or ZERO


def last_published_payslip(employee, before_period_end=None):
    qs = Payslip.objects.filter(employee=employee, status=Status.PUBLISHED)
    if before_period_end:
        qs = qs.filter(run__period_end__lt=before_period_end)
    return qs.order_by("-run__period_end").first()


def leave_balance(employee):
    """Paid leave balance after the most recent published payslip."""
    last = last_published_payslip(employee)
    return last.leave_balance_after if last else employee.opening_leave_balance


def period_is_closed_for(employee, day):
    """True when a payslip for the period containing `day` is approved or published."""
    _, end = payroll.period_containing(day)
    return Payslip.objects.filter(
        employee=employee, run__period_end=end, status__in=[Status.APPROVED, Status.PUBLISHED],
    ).exists()


# ---- Advances -------------------------------------------------------------

def advance_outstanding(employee):
    given = employee.advances.aggregate(t=Sum("amount"))["t"] or ZERO
    recovered = Payslip.objects.filter(employee=employee, status=Status.PUBLISHED).aggregate(
        t=Sum("advance_recovery")
    )["t"] or ZERO
    return given - recovered


def suggested_advance_recovery(employee):
    outstanding = advance_outstanding(employee)
    if outstanding <= 0:
        return ZERO
    monthly = employee.advances.aggregate(t=Sum("monthly_recovery"))["t"] or ZERO
    return min(monthly, outstanding)


# ---- ESIC / PF applicability ---------------------------------------------

def esic_applies(employee, period_start):
    if not employee.esic_enrolled:
        return False
    cp_start = payroll.esic_contribution_period_start(period_start)
    basic = employee.basic_on(max(cp_start, employee.date_joined))
    if basic is None:
        basic = employee.basic_on(period_start)
    return basic is not None and basic <= payroll.ESIC_WAGE_LIMIT


# ---- Payroll runs ---------------------------------------------------------

def employees_in_period(period_start, period_end):
    return Employee.objects.filter(office=Office.INDIA, date_joined__lte=period_end).filter(
        Q(date_left__isnull=True) | Q(date_left__gte=period_start)
    )


def _fill_snapshot(slip, employee, run):
    slip.employee_code = employee.employee_code
    slip.employee_name = employee.full_name
    slip.designation = employee.designation
    slip.department = employee.department.name if employee.department else ""
    slip.business_unit = employee.business_unit
    slip.uan = employee.uan
    slip.pan = employee.pan
    slip.esic_ip_number = employee.esic_ip_number
    slip.aadhaar_last4 = employee.aadhaar_last4
    slip.basic = employee.basic_on(run.period_end)
    slip.pf_applies = employee.pf_enrolled
    slip.esic_applies = esic_applies(employee, run.period_start)
    previous = last_published_payslip(employee, before_period_end=run.period_end)
    slip.leave_balance_before = previous.leave_balance_after if previous else employee.opening_leave_balance
    slip.leave_accrued = payroll.MONTHLY_LEAVE_ACCRUAL
    slip.leave_taken = approved_leave_in(employee, run.period_start, run.period_end)


def _suggest_inputs(slip, employee, run):
    slip.paid_leave_applied = min(slip.leave_taken, slip.leave_available)
    slip.days_not_employed = payroll.suggested_days_not_employed(
        run.period_start, run.period_end, employee.date_joined, employee.date_left
    )
    slip.advance_recovery = suggested_advance_recovery(employee)


@transaction.atomic
def create_run(year, month, user):
    start, end = payroll.period_ending(year, month)
    if PayrollRun.objects.filter(period_end=end).exists():
        raise ValidationError("A payroll run for this period already exists.")
    run = PayrollRun.objects.create(period_start=start, period_end=end, created_by=user)
    skipped = sync_run(run)
    audit(user, "payroll.run_created", run)
    return run, skipped


def sync_run(run):
    """Add draft payslips for anyone missing and refresh existing drafts.

    Returns the employees skipped because they have no salary record.
    """
    skipped = []
    for employee in employees_in_period(run.period_start, run.period_end):
        if employee.basic_on(run.period_end) is None:
            skipped.append(employee)
            continue
        slip = run.current_payslips().filter(employee=employee).first()
        if slip is None:
            slip = Payslip(run=run, employee=employee)
            _fill_snapshot(slip, employee, run)
            _suggest_inputs(slip, employee, run)
        elif slip.status == Status.DRAFT:
            _fill_snapshot(slip, employee, run)
            slip.paid_leave_applied = min(slip.leave_taken, slip.leave_available)
        else:
            continue
        slip.apply_calculation()
        slip.save()
    return skipped


def validate_inputs(slip):
    errors = {}
    if slip.paid_leave_applied < 0 or slip.paid_leave_applied > slip.leave_taken:
        errors["paid_leave_applied"] = f"Must be between 0 and leave taken ({slip.leave_taken})."
    elif slip.paid_leave_applied > slip.leave_available:
        errors["paid_leave_applied"] = f"Only {slip.leave_available} paid days are available."
    if slip.days_not_employed < 0 or slip.days_not_employed > payroll.WORKING_DAYS:
        errors["days_not_employed"] = "Must be between 0 and 23."
    for field in ("bonus", "incentives", "overtime", "other_additions", "other_deductions", "advance_recovery"):
        if getattr(slip, field) < 0:
            errors[field] = "Cannot be negative."
    if slip.advance_recovery > advance_outstanding(slip.employee):
        errors["advance_recovery"] = "More than the outstanding advance."
    if slip.other_deductions > 0 and not slip.other_deductions_note:
        errors["other_deductions_note"] = "Say what the deduction is for."
    if errors:
        raise ValidationError(errors)


def confirm_inputs(slip, user):
    if slip.status not in (Status.DRAFT, Status.READY):
        raise ValidationError("Only unapproved payslips can be edited.")
    validate_inputs(slip)
    slip.apply_calculation()
    if slip.net_pay < 0:
        raise ValidationError("Net pay would be negative. Reduce the deductions.")
    slip.status = Status.READY
    slip.save()
    audit(user, "payslip.inputs_confirmed", slip, f"net {slip.net_pay}")


def approve(slip, user):
    if slip.status != Status.READY:
        raise ValidationError("Only payslips marked ready can be approved.")
    slip.status = Status.APPROVED
    slip.approved_by = user
    slip.approved_at = timezone.now()
    slip.save()
    audit(user, "payslip.approved", slip, f"net {slip.net_pay}")


def unapprove(slip, user):
    if slip.status != Status.APPROVED:
        raise ValidationError("Payslip is not approved.")
    slip.status = Status.READY
    slip.approved_by = None
    slip.approved_at = None
    slip.save()
    audit(user, "payslip.unapproved", slip)


def reopen(slip, user):
    """Send a ready payslip back to draft so leave and salary refresh."""
    if slip.status != Status.READY:
        raise ValidationError("Only ready payslips can be reopened.")
    slip.status = Status.DRAFT
    slip.save()
    sync_run(slip.run)
    audit(user, "payslip.reopened", slip)


def stale_reason(slip):
    """Why an approved payslip no longer matches current records, if it doesn't."""
    leave_now = approved_leave_in(slip.employee, slip.run.period_start, slip.run.period_end)
    if leave_now != slip.leave_taken:
        return f"Approved leave has changed from {slip.leave_taken} to {leave_now} days."
    if slip.employee.basic_on(slip.run.period_end) != slip.basic:
        return "Basic salary has changed."
    previous = last_published_payslip(slip.employee, before_period_end=slip.run.period_end)
    balance = previous.leave_balance_after if previous else slip.employee.opening_leave_balance
    if balance != slip.leave_balance_before:
        return "Opening leave balance has changed."
    return None


@transaction.atomic
def publish_approved(run, user):
    published, blocked = [], []
    for slip in run.current_payslips().filter(status=Status.APPROVED).select_related("employee"):
        reason = stale_reason(slip)
        if reason:
            blocked.append((slip, reason))
            continue
        if slip.supersedes_id:
            old = slip.supersedes
            old.status = Status.SUPERSEDED
            old.save()
        slip.status = Status.PUBLISHED
        slip.published_at = timezone.now()
        slip.save()
        audit(user, "payslip.published", slip, f"net {slip.net_pay}")
        published.append(slip)
    return published, blocked


@transaction.atomic
def start_correction(slip, user):
    if slip.status != Status.PUBLISHED:
        raise ValidationError("Only published payslips can be corrected.")
    if Payslip.objects.filter(
        employee=slip.employee, run__period_end__gt=slip.run.period_end,
        status__in=[Status.APPROVED, Status.PUBLISHED],
    ).exists():
        raise ValidationError(
            "A later payslip already exists for this person. Make the adjustment on the next payslip instead."
        )
    if slip.run.payslips.filter(supersedes=slip).exists():
        raise ValidationError("A correction for this payslip is already in progress.")
    new = Payslip.objects.get(pk=slip.pk)
    new.pk = None
    new.id = None
    new.version = slip.version + 1
    new.supersedes = slip
    new.status = Status.DRAFT
    new.approved_by = None
    new.approved_at = None
    new.published_at = None
    new.save()
    audit(user, "payslip.correction_started", new)
    return new


# ---- Compliance warnings --------------------------------------------------

@dataclass
class Warning:
    employee: Employee
    message: str
    level: str = "warn"  # "warn" or "info"


def compliance_warnings(today=None):
    today = today or timezone.localdate()
    warnings = []
    india = Employee.objects.filter(office=Office.INDIA).filter(
        Q(date_left__isnull=True) | Q(date_left__gte=today)
    )
    for e in india:
        basic = e.basic_on(today)
        if basic is None:
            warnings.append(Warning(e, "No salary record. Add one before running payroll."))
            continue
        if basic <= payroll.ESIC_WAGE_LIMIT and not e.esic_enrolled:
            warnings.append(Warning(
                e, "Basic is ₹21,000 or less but not enrolled in ESIC. ESIC applies from day one, "
                   "including probation, if the office is covered. Check with the accountant."
            ))
        if basic <= payroll.PF_WAGE_CEILING and not e.pf_enrolled:
            warnings.append(Warning(e, "Basic is ₹15,000 or less, so PF enrolment is normally mandatory."))
        if e.esic_enrolled and not e.esic_ip_number:
            warnings.append(Warning(e, "Enrolled in ESIC but the insurance (IP) number is missing."))
        if e.pf_enrolled and not e.uan:
            warnings.append(Warning(e, "Enrolled in PF but the UAN is missing."))
        if basic * 12 >= payroll.TDS_WARNING_FROM:
            warnings.append(Warning(
                e, f"Annual salary ₹{basic * 12:,.0f} is close to or above the ₹12 lakh TDS threshold. "
                   "TDS may need to be deducted."
            ))
        if e.probation_end_date and today - timedelta(days=30) <= e.probation_end_date <= today + timedelta(days=14):
            when = "ends" if e.probation_end_date >= today else "ended"
            warnings.append(Warning(
                e, f"Probation {when} on {e.probation_end_date:%d %b %Y}. Review PF and ESIC enrolment.", "info"
            ))
    return warnings


# ---- Dashboard ------------------------------------------------------------

def upcoming_anniversaries(days=30, today=None):
    today = today or timezone.localdate()
    upcoming = []
    for e in Employee.objects.filter(Q(date_left__isnull=True) | Q(date_left__gte=today)):
        when, years = e.next_anniversary(today)
        if years >= 1 and (when - today).days <= days:
            upcoming.append((when, years, e))
    upcoming.sort(key=lambda item: item[0])
    return upcoming


def current_cost_summary(today=None):
    """Monthly India staff cost from current salaries (full month, no leave)."""
    today = today or timezone.localdate()
    by_dept = defaultdict(lambda: {"headcount": 0, "basic": ZERO, "employer": ZERO})
    for e in Employee.objects.filter(office=Office.INDIA).filter(
        Q(date_left__isnull=True) | Q(date_left__gte=today)
    ).select_related("department"):
        basic = e.basic_on(today)
        if basic is None:
            continue
        name = e.department.name if e.department else "No department"
        employer = payroll.estimated_employer_contributions(
            basic, e.pf_enrolled, esic_applies(e, payroll.period_containing(today)[0])
        )
        row = by_dept[name]
        row["headcount"] += 1
        row["basic"] += basic
        row["employer"] += employer
    rows = []
    for name, row in sorted(by_dept.items()):
        row["name"] = name
        row["cost"] = row["basic"] + row["employer"]
        rows.append(row)
    totals = {
        "headcount": sum(r["headcount"] for r in rows),
        "basic": sum((r["basic"] for r in rows), ZERO),
        "employer": sum((r["employer"] for r in rows), ZERO),
    }
    totals["cost"] = totals["basic"] + totals["employer"]
    return rows, totals


def run_totals(run):
    slips = run.current_payslips()
    agg = slips.aggregate(
        earnings=Sum("total_earnings"), lop=Sum("lop_amount"), net=Sum("net_pay"),
        pf_employer=Sum("pf_employer"), esic_employer=Sum("esic_employer"), cost=Sum("employer_cost"),
    )
    agg = {k: v or ZERO for k, v in agg.items()}
    agg["count"] = slips.count()
    agg["published"] = slips.filter(status=Status.PUBLISHED).count()
    return agg
