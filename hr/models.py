from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from . import payroll

MONEY = {"max_digits": 12, "decimal_places": 2, "default": Decimal("0")}
DAYS = {"max_digits": 5, "decimal_places": 1, "default": Decimal("0")}


class Office(models.TextChoices):
    UK = "UK", "United Kingdom"
    INDIA = "IN", "India"


class Role(models.TextChoices):
    EMPLOYEE = "employee", "Employee"
    TEAM_LEAD = "team_lead", "Team lead"
    ADMIN = "admin", "Admin"


class Department(models.Model):
    name = models.CharField(max_length=100)
    office = models.CharField(max_length=2, choices=Office.choices)

    class Meta:
        ordering = ["office", "name"]
        unique_together = [("name", "office")]

    def __str__(self):
        return f"{self.name} ({self.get_office_display()})"


class Employee(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="employee",
    )
    employee_code = models.CharField("Employee ID", max_length=20, unique=True)
    full_name = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    office = models.CharField(max_length=2, choices=Office.choices)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.EMPLOYEE)
    business_unit = models.CharField(max_length=100, blank=True, help_text="e.g. Calicut")
    team_lead = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="team_members",
        help_text="Approves this person's leave.",
    )
    date_joined = models.DateField()
    probation_end_date = models.DateField(null=True, blank=True)
    date_left = models.DateField(null=True, blank=True)

    # Current role, kept in step with the latest EmploymentRecord.
    designation = models.CharField(max_length=100, blank=True)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL)

    # India statutory details.
    uan = models.CharField("UAN number", max_length=20, blank=True)
    pan = models.CharField("PAN number", max_length=10, blank=True)
    esic_ip_number = models.CharField("ESIC insurance number", max_length=20, blank=True)
    aadhaar_last4 = models.CharField(
        "Aadhaar (last 4 digits only)", max_length=4, blank=True,
        help_text="Never store the full Aadhaar number.",
    )
    pf_enrolled = models.BooleanField("Enrolled in PF", default=False)
    esic_enrolled = models.BooleanField("Enrolled in ESIC", default=False)
    opening_leave_balance = models.DecimalField(
        "Paid leave balance at go-live", help_text="Balance before the first payslip in this system.",
        **DAYS,
    )

    class Meta:
        ordering = ["office", "employee_code"]

    def __str__(self):
        return f"{self.employee_code} {self.full_name}"

    def clean(self):
        if self.aadhaar_last4 and not (self.aadhaar_last4.isdigit() and len(self.aadhaar_last4) == 4):
            raise ValidationError({"aadhaar_last4": "Enter exactly the last 4 digits."})
        if self.date_left and self.date_left < self.date_joined:
            raise ValidationError({"date_left": "Leaving date is before joining date."})
        if self.department and self.department.office != self.office:
            raise ValidationError({"department": "Department belongs to a different office."})

    @property
    def is_india(self):
        return self.office == Office.INDIA

    @property
    def is_active(self):
        return self.date_left is None or self.date_left >= timezone.localdate()

    def on_probation(self, on=None):
        on = on or timezone.localdate()
        return bool(self.probation_end_date and self.probation_end_date > on)

    def record_on(self, on):
        return self.employment_records.filter(effective_from__lte=on).order_by("-effective_from", "-id").first()

    def basic_on(self, on):
        record = self.record_on(on)
        return record.basic_monthly if record else None

    @property
    def current_designation(self):
        """Designation from the history entry in force today (handles future-dated changes)."""
        record = self.record_on(timezone.localdate())
        return record.designation if record else self.designation

    @property
    def current_department(self):
        record = self.record_on(timezone.localdate())
        return record.department if record else self.department

    def current_basic(self):
        return self.basic_on(timezone.localdate())

    def next_anniversary(self, today=None):
        today = today or timezone.localdate()
        try:
            anniversary = self.date_joined.replace(year=today.year)
        except ValueError:  # joined on 29 February
            anniversary = date(today.year, 3, 1)
        if anniversary < today:
            try:
                anniversary = self.date_joined.replace(year=today.year + 1)
            except ValueError:
                anniversary = date(today.year + 1, 3, 1)
        return anniversary, anniversary.year - self.date_joined.year


class EmploymentRecord(models.Model):
    """One dated change to salary, hours or role. Never edited, only added."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="employment_records")
    effective_from = models.DateField()
    designation = models.CharField(max_length=100)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL)
    weekly_hours = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    basic_monthly = models.DecimalField(
        "Monthly Basic (INR)", max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="India staff only. UK salaries are not stored in this system.",
    )
    reason = models.CharField(max_length=200, blank=True, help_text="e.g. Joined, Promotion, Annual increment")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from", "-id"]

    def __str__(self):
        return f"{self.employee} from {self.effective_from}"

    def clean(self):
        if self.employee_id is None:
            return
        if self.employee.office == Office.UK and self.basic_monthly is not None:
            raise ValidationError({"basic_monthly": "UK salaries are not stored in this system."})
        if self.employee.office == Office.INDIA and self.basic_monthly is None:
            raise ValidationError({"basic_monthly": "Basic salary is required for India staff."})


class LeaveRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="leave_requests")
    start_date = models.DateField()
    end_date = models.DateField()
    days = models.DecimalField(max_digits=4, decimal_places=1, help_text="Use .5 for half days.")
    reason = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "-id"]

    def __str__(self):
        return f"{self.employee} {self.start_date} ({self.days} days)"

    def clean(self):
        if not (self.start_date and self.end_date and self.days is not None):
            return
        if self.end_date < self.start_date:
            raise ValidationError("End date is before start date.")
        if self.days <= 0 or (self.days * 2) % 1 != 0:
            raise ValidationError({"days": "Enter a whole or half number of days, e.g. 1 or 1.5."})
        if self.days > (self.end_date - self.start_date).days + 1:
            raise ValidationError({"days": "More days than the dates cover."})
        if payroll.period_containing(self.start_date) != payroll.period_containing(self.end_date):
            raise ValidationError(
                "Leave can't cross the pay period boundary (25th/26th). Split it into two requests."
            )


class Advance(models.Model):
    """A salary advance or loan, recovered through payslip deductions."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="advances")
    date_given = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    monthly_recovery = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_given"]

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": "Amount must be more than zero."})
        if self.monthly_recovery is not None and self.monthly_recovery <= 0:
            raise ValidationError({"monthly_recovery": "Monthly recovery must be more than zero."})


class PayrollRun(models.Model):
    period_start = models.DateField()
    period_end = models.DateField(unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_end"]

    def __str__(self):
        return self.label

    @property
    def label(self):
        return f"{self.period_end:%B %Y} ({self.period_start:%d %b} – {self.period_end:%d %b %Y})"

    def current_payslips(self):
        """The newest version of each employee's payslip in this run."""
        return self.payslips.exclude(status=Payslip.Status.SUPERSEDED).filter(superseded_by__isnull=True)


class Payslip(models.Model):
    """A snapshot of one employee's pay for one run.

    Once published a payslip is locked; a correction creates a new version
    and marks this one superseded.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Needs admin input"
        READY = "ready", "Ready for approval"
        APPROVED = "approved", "Approved"
        PUBLISHED = "published", "Published"
        SUPERSEDED = "superseded", "Superseded"

    run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name="payslips")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payslips")
    version = models.PositiveIntegerField(default=1)
    supersedes = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="superseded_by"
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)

    # Employee details as they were when the payslip was made.
    employee_code = models.CharField(max_length=20)
    employee_name = models.CharField(max_length=150)
    designation = models.CharField(max_length=100, blank=True)
    department = models.CharField(max_length=100, blank=True)
    business_unit = models.CharField(max_length=100, blank=True)
    uan = models.CharField(max_length=20, blank=True)
    pan = models.CharField(max_length=10, blank=True)
    esic_ip_number = models.CharField(max_length=20, blank=True)
    aadhaar_last4 = models.CharField(max_length=4, blank=True)

    basic = models.DecimalField(max_digits=12, decimal_places=2)
    pf_applies = models.BooleanField(default=False)
    esic_applies = models.BooleanField(default=False)

    # Leave.
    leave_taken = models.DecimalField(**DAYS)
    paid_leave_applied = models.DecimalField(**DAYS)
    days_not_employed = models.DecimalField(**DAYS)
    leave_balance_before = models.DecimalField(**DAYS)
    leave_accrued = models.DecimalField(**DAYS)
    leave_balance_after = models.DecimalField(**DAYS)

    # Admin inputs.
    bonus = models.DecimalField(**MONEY)
    incentives = models.DecimalField(**MONEY)
    overtime = models.DecimalField(**MONEY)
    other_additions = models.DecimalField(**MONEY)
    other_deductions = models.DecimalField(**MONEY)
    other_deductions_note = models.CharField(max_length=200, blank=True)
    advance_recovery = models.DecimalField(**MONEY)
    admin_note = models.CharField(max_length=300, blank=True)

    # Calculated.
    lop_days = models.DecimalField(**DAYS)
    lop_amount = models.DecimalField(**MONEY)
    pf_employee = models.DecimalField(**MONEY)
    pf_employer = models.DecimalField(**MONEY)
    esic_employee = models.DecimalField(**MONEY)
    esic_employer = models.DecimalField(**MONEY)
    total_earnings = models.DecimalField(**MONEY)
    total_deductions = models.DecimalField(**MONEY)
    net_pay = models.DecimalField(**MONEY)
    employer_cost = models.DecimalField(**MONEY)

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["employee_code", "-version"]

    def __str__(self):
        return f"{self.employee_code} {self.run.label} v{self.version}"

    @property
    def is_locked(self):
        return self.status in (self.Status.PUBLISHED, self.Status.SUPERSEDED)

    @property
    def additions_total(self):
        return self.bonus + self.incentives + self.overtime + self.other_additions

    @property
    def leave_available(self):
        return self.leave_balance_before + self.leave_accrued

    def apply_calculation(self):
        result = payroll.calculate(payroll.PayInputs(
            basic=self.basic,
            leave_taken=self.leave_taken,
            paid_leave_applied=self.paid_leave_applied,
            days_not_employed=self.days_not_employed,
            bonus=self.bonus,
            incentives=self.incentives,
            overtime=self.overtime,
            other_additions=self.other_additions,
            other_deductions=self.other_deductions,
            advance_recovery=self.advance_recovery,
            pf_applies=self.pf_applies,
            esic_applies=self.esic_applies,
        ))
        for field in (
            "lop_days", "lop_amount", "pf_employee", "pf_employer", "esic_employee", "esic_employer",
            "total_earnings", "total_deductions", "net_pay", "employer_cost",
        ):
            setattr(self, field, getattr(result, field))
        self.leave_balance_after = self.leave_available - self.paid_leave_applied

    def save(self, *args, **kwargs):
        if self.pk:
            stored = Payslip.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if stored == self.Status.SUPERSEDED or (
                stored == self.Status.PUBLISHED and self.status != self.Status.SUPERSEDED
            ):
                raise ValidationError("A published payslip cannot be changed. Issue a correction instead.")
        super().save(*args, **kwargs)


class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=60)
    target = models.CharField(max_length=200)
    detail = models.TextField(blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.action} {self.target}"


class UserSettings(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="hr_settings")
    must_change_password = models.BooleanField(default=True)


def audit(actor, action, target, detail=""):
    AuditEvent.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action, target=str(target)[:200], detail=detail,
    )
