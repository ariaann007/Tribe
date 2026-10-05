from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import payroll, services
from .forms import (
    AdminLeaveForm, AdvanceForm, CreateLoginForm, DepartmentForm, EmployeeForm, EmploymentRecordForm, LeaveRequestForm,
    NewEmployeeForm, NewRunForm, PayslipInputsForm, ResetPasswordForm,
)
from .models import (
    Advance, AuditEvent, Department, Employee, EmploymentRecord, LeaveRequest, Office, Payslip, PayrollRun,
    UserSettings, audit,
)
from .permissions import (
    admin_required, can_decide_leave, employee_of, employee_required, is_hr_admin, is_team_lead,
)

Status = Payslip.Status


def _error_text(exc):
    if hasattr(exc, "message_dict"):
        return " ".join(m for msgs in exc.message_dict.values() for m in msgs)
    return " ".join(exc.messages)


@login_required
def home(request):
    if is_hr_admin(request.user):
        return redirect("dashboard")
    if employee_of(request.user) is None:
        raise PermissionDenied
    return redirect("me")


# ---- Admin dashboard ------------------------------------------------------

@admin_required
def dashboard(request):
    today = timezone.localdate()
    rows, totals = services.current_cost_summary(today)
    runs = list(PayrollRun.objects.all()[:12])
    history = [(run, services.run_totals(run)) for run in runs]
    active = Employee.objects.exclude(date_left__lt=today)
    return render(request, "hr/dashboard.html", {
        "dept_rows": rows,
        "totals": totals,
        "history": history,
        "anniversaries": services.upcoming_anniversaries(30, today),
        "warnings": services.compliance_warnings(today),
        "headcount_uk": active.filter(office=Office.UK).count(),
        "headcount_in": active.filter(office=Office.INDIA).count(),
        "pending_leave": LeaveRequest.objects.filter(status=LeaveRequest.Status.PENDING).count(),
    })


# ---- Employees (admin) ----------------------------------------------------

@admin_required
def employee_list(request):
    office = request.GET.get("office", "")
    show_left = request.GET.get("left") == "1"
    qs = Employee.objects.select_related("department", "team_lead")
    if office in Office.values:
        qs = qs.filter(office=office)
    if not show_left:
        qs = qs.exclude(date_left__lt=timezone.localdate())
    return render(request, "hr/employee_list.html", {
        "employees": qs, "office": office, "show_left": show_left, "offices": Office.choices,
    })


@admin_required
def departments(request):
    form = DepartmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        dept = form.save()
        audit(request.user, "department.created", dept)
        messages.success(request, f"{dept} added.")
        return redirect("departments")
    rows = [(d, d.employee_set.exclude(date_left__lt=timezone.localdate()).count()) for d in Department.objects.all()]
    return render(request, "hr/departments.html", {"form": form, "rows": rows})


@admin_required
def employee_detail(request, pk):
    e = get_object_or_404(Employee, pk=pk)
    ctx = {
        "e": e,
        "records": e.employment_records.select_related("department", "created_by"),
        "leave": e.leave_requests.all()[:20],
        "payslips": e.payslips.select_related("run").exclude(status=Status.SUPERSEDED),
        "advances": e.advances.all(),
        "anniversary": e.next_anniversary(),
    }
    if e.is_india:
        ctx["balance"] = services.leave_balance(e)
        ctx["advance_outstanding"] = services.advance_outstanding(e)
        ctx["current_basic"] = e.current_basic()
    return render(request, "hr/employee_detail.html", ctx)


@admin_required
@transaction.atomic
def employee_new(request):
    form = NewEmployeeForm(request.POST or None)
    form.fields["department"].queryset = Department.objects.all()
    if request.method == "POST" and form.is_valid():
        e = form.save(commit=False)
        e.designation = form.cleaned_data["designation"]
        e.department = form.cleaned_data["department"]
        e.save()
        EmploymentRecord.objects.create(
            employee=e, effective_from=e.date_joined, designation=e.designation, department=e.department,
            weekly_hours=form.cleaned_data["weekly_hours"], basic_monthly=form.cleaned_data["basic_monthly"],
            reason="Joined", created_by=request.user,
        )
        audit(request.user, "employee.created", e)
        messages.success(request, f"{e.full_name} added.")
        return redirect("employee_detail", pk=e.pk)
    return render(request, "hr/form.html", {"form": form, "title": "Add employee", "back": "employee_list"})


@admin_required
def employee_edit(request, pk):
    e = get_object_or_404(Employee, pk=pk)
    form = EmployeeForm(request.POST or None, instance=e)
    form.fields["office"].disabled = True
    form.fields["team_lead"].queryset = form.fields["team_lead"].queryset.exclude(pk=e.pk)
    if request.method == "POST" and form.is_valid():
        changed = ", ".join(form.changed_data)
        form.save()
        audit(request.user, "employee.updated", e, changed)
        messages.success(request, "Details saved.")
        return redirect("employee_detail", pk=e.pk)
    return render(request, "hr/form.html", {
        "form": form, "title": f"Edit {e.full_name}", "back_url": e.pk,
        "intro": "Salary, hours, role and department changes are recorded separately as dated changes, "
                 "so the history is kept.",
    })


@admin_required
def employee_delete(request, pk):
    e = get_object_or_404(Employee, pk=pk)
    blockers = []
    if e.payslips.exists():
        blockers.append(
            "They have payslips. Payroll records must be kept, so set a leaving date instead. "
            "That removes them from the active staff list and from future payroll."
        )
    if employee_of(request.user) == e:
        blockers.append("You can't delete your own record.")
    if e.team_members.exists():
        blockers.append(
            f"They are team lead for {e.team_members.count()} person(s). Give those people a new team lead first."
        )
    if request.method == "POST" and not blockers:
        name = str(e)
        with transaction.atomic():
            user = e.user
            e.delete()
            if user:
                user.delete()
            audit(request.user, "employee.deleted", name)
        messages.success(request, f"{name} deleted.")
        return redirect("employee_list")
    return render(request, "hr/employee_delete.html", {
        "e": e, "blockers": blockers,
        "counts": {
            "employment records": e.employment_records.count(),
            "leave requests": e.leave_requests.count(),
            "advances": e.advances.count(),
        },
    })


@admin_required
@transaction.atomic
def employment_change_new(request, pk):
    e = get_object_or_404(Employee, pk=pk)
    current = e.record_on(timezone.localdate())
    initial = {}
    if current:
        initial = {
            "designation": current.designation, "department": current.department,
            "weekly_hours": current.weekly_hours, "basic_monthly": current.basic_monthly,
        }
    form = EmploymentRecordForm(request.POST or None, instance=EmploymentRecord(employee=e), initial=initial)
    form.fields["department"].queryset = Department.objects.filter(office=e.office)
    if e.office == Office.UK:
        del form.fields["basic_monthly"]
    if request.method == "POST" and form.is_valid():
        record = form.save(commit=False)
        record.created_by = request.user
        record.save()
        latest = e.record_on(timezone.localdate())
        if latest:
            e.designation = latest.designation
            e.department = latest.department
            e.save(update_fields=["designation", "department"])
        audit(request.user, "employee.change_recorded", e, f"from {record.effective_from}: {record.reason}")
        messages.success(request, "Change recorded.")
        return redirect("employee_detail", pk=e.pk)
    return render(request, "hr/form.html", {
        "form": form, "title": f"Record a change for {e.full_name}", "back_url": e.pk,
        "intro": "Use this for a pay rise, new role, department move or change of hours. "
                 "The previous values stay in the history.",
    })


@admin_required
def employee_login_create(request, pk):
    e = get_object_or_404(Employee, pk=pk)
    if e.user_id:
        messages.info(request, "This employee already has a login.")
        return redirect("employee_detail", pk=e.pk)
    form = CreateLoginForm(request.POST or None, initial={"username": e.employee_code})
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = get_user_model().objects.create_user(
                username=form.cleaned_data["username"], email=e.email,
                password=form.cleaned_data["temporary_password"],
            )
            UserSettings.objects.create(user=user, must_change_password=True)
            e.user = user
            e.save(update_fields=["user"])
            audit(request.user, "login.created", e)
        messages.success(request, "Login created. Give the temporary password to the employee privately.")
        return redirect("employee_detail", pk=e.pk)
    return render(request, "hr/form.html", {"form": form, "title": f"Create login for {e.full_name}", "back_url": e.pk})


@admin_required
def employee_password_reset(request, pk):
    e = get_object_or_404(Employee, pk=pk, user__isnull=False)
    form = ResetPasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        e.user.set_password(form.cleaned_data["temporary_password"])
        e.user.save()
        UserSettings.objects.update_or_create(user=e.user, defaults={"must_change_password": True})
        audit(request.user, "login.password_reset", e)
        messages.success(request, "Temporary password set. They must change it when they next log in.")
        return redirect("employee_detail", pk=e.pk)
    return render(request, "hr/form.html", {"form": form, "title": f"Reset password for {e.full_name}", "back_url": e.pk})


@admin_required
def advance_new(request, pk):
    e = get_object_or_404(Employee, pk=pk, office=Office.INDIA)
    form = AdvanceForm(request.POST or None, instance=Advance(employee=e))
    if request.method == "POST" and form.is_valid():
        advance = form.save(commit=False)
        advance.created_by = request.user
        advance.save()
        audit(request.user, "advance.created", e, f"{advance.amount} at {advance.monthly_recovery}/month")
        messages.success(request, "Advance recorded. It will be suggested as a deduction on each payslip.")
        return redirect("employee_detail", pk=e.pk)
    return render(request, "hr/form.html", {"form": form, "title": f"Record an advance for {e.full_name}", "back_url": e.pk})


# ---- My page (every employee) ---------------------------------------------

@employee_required
def me(request):
    e = employee_of(request.user)
    ctx = {"e": e, "anniversary": e.next_anniversary()}
    if e.is_india:
        ctx.update({
            "balance": services.leave_balance(e),
            "leave": e.leave_requests.all()[:20],
            "payslips": e.payslips.filter(status=Status.PUBLISHED).select_related("run").order_by("-run__period_end"),
            "accrual": payroll.MONTHLY_LEAVE_ACCRUAL,
        })
    return render(request, "hr/me.html", ctx)


@employee_required
def leave_new(request):
    e = employee_of(request.user)
    if not e.is_india:
        raise PermissionDenied
    form = LeaveRequestForm(request.POST or None, instance=LeaveRequest(employee=e))
    if request.method == "POST" and form.is_valid():
        leave = form.save(commit=False)
        if services.period_is_closed_for(e, leave.start_date):
            form.add_error(None, "Payroll for that period is already approved. Please contact HR.")
        else:
            leave.save()
            audit(request.user, "leave.requested", leave)
            messages.success(request, "Leave request sent for approval.")
            return redirect("me")
    return render(request, "hr/form.html", {
        "form": form, "title": "Request leave", "back": "me",
        "intro": f"Your paid leave balance is {services.leave_balance(e)} days, plus "
                 f"{payroll.MONTHLY_LEAVE_ACCRUAL} days added on your next payslip. "
                 "Leave beyond your balance is unpaid.",
    })


@employee_required
@require_POST
def leave_cancel(request, pk):
    leave = get_object_or_404(LeaveRequest, pk=pk, employee=employee_of(request.user))
    if leave.status != LeaveRequest.Status.PENDING:
        messages.error(request, "Only pending requests can be cancelled.")
    else:
        leave.status = LeaveRequest.Status.CANCELLED
        leave.save()
        audit(request.user, "leave.cancelled", leave)
        messages.success(request, "Request cancelled.")
    return redirect("me")


# ---- Leave approvals (admin and team leads) -------------------------------

@login_required
def leave_approvals(request):
    user = request.user
    if is_hr_admin(user):
        qs = LeaveRequest.objects.all()
    elif is_team_lead(user):
        qs = LeaveRequest.objects.filter(employee__team_lead=employee_of(user))
    else:
        raise PermissionDenied
    qs = qs.select_related("employee", "decided_by")
    return render(request, "hr/leave_approvals.html", {
        "pending": qs.filter(status=LeaveRequest.Status.PENDING),
        "recent": qs.exclude(status=LeaveRequest.Status.PENDING)[:30],
        "is_admin": is_hr_admin(user),
    })


@login_required
@require_POST
def leave_decide(request, pk):
    leave = get_object_or_404(LeaveRequest, pk=pk)
    if not can_decide_leave(request.user, leave):
        raise PermissionDenied
    decision = request.POST.get("decision")
    if leave.status != LeaveRequest.Status.PENDING or decision not in ("approve", "reject"):
        messages.error(request, "This request can't be changed.")
        return redirect("leave_approvals")
    if decision == "approve" and services.period_is_closed_for(leave.employee, leave.start_date):
        messages.error(request, "Payroll for that period is already approved. Unapprove the payslip first.")
        return redirect("leave_approvals")
    leave.status = LeaveRequest.Status.APPROVED if decision == "approve" else LeaveRequest.Status.REJECTED
    leave.decided_by = request.user
    leave.decided_at = timezone.now()
    leave.save()
    audit(request.user, f"leave.{leave.status}", leave)
    messages.success(request, f"Leave {leave.get_status_display().lower()} for {leave.employee.full_name}.")
    return redirect("leave_approvals")


@admin_required
def leave_record(request):
    """Admin records leave on someone's behalf. It is approved straight away."""
    form = AdminLeaveForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        leave = form.save(commit=False)
        if services.period_is_closed_for(leave.employee, leave.start_date):
            form.add_error(None, "Payroll for that period is already approved for this employee.")
        else:
            leave.status = LeaveRequest.Status.APPROVED
            leave.decided_by = request.user
            leave.decided_at = timezone.now()
            leave.save()
            audit(request.user, "leave.recorded", leave)
            messages.success(request, "Leave recorded and approved.")
            return redirect("leave_approvals")
    return render(request, "hr/form.html", {"form": form, "title": "Record leave for an employee", "back": "leave_approvals"})


@login_required
def team(request):
    lead = employee_of(request.user)
    if not (lead and (is_team_lead(request.user) or is_hr_admin(request.user))):
        raise PermissionDenied
    members = [(m, services.leave_balance(m) if m.is_india else None)
               for m in lead.team_members.exclude(date_left__lt=timezone.localdate())]
    return render(request, "hr/team.html", {"members": members})


# ---- Payroll (admin) ------------------------------------------------------

@admin_required
def payroll_runs(request):
    runs = [(run, services.run_totals(run)) for run in PayrollRun.objects.all()]
    today = timezone.localdate()
    _, end = payroll.period_containing(today)
    form = NewRunForm(request.POST or None, initial={"month": end.month, "year": end.year})
    if request.method == "POST" and form.is_valid():
        try:
            run, skipped = services.create_run(form.cleaned_data["year"], form.cleaned_data["month"], request.user)
        except ValidationError as exc:
            messages.error(request, _error_text(exc))
        else:
            for e in skipped:
                messages.warning(request, f"{e} has no salary record and was left out.")
            return redirect("payroll_run", pk=run.pk)
    return render(request, "hr/payroll_runs.html", {"runs": runs, "form": form})


@admin_required
def payroll_run(request, pk):
    run = get_object_or_404(PayrollRun, pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "refresh":
            skipped = services.sync_run(run)
            for e in skipped:
                messages.warning(request, f"{e} has no salary record and was left out.")
            messages.success(request, "Drafts refreshed with the latest leave and salary records.")
        elif action == "publish":
            published, blocked = services.publish_approved(run, request.user)
            if published:
                messages.success(request, f"{len(published)} payslip(s) published to employees.")
            for slip, reason in blocked:
                messages.error(request, f"{slip.employee_name} not published: {reason} Unapprove and review it.")
            if not published and not blocked:
                messages.info(request, "No approved payslips to publish.")
        return redirect("payroll_run", pk=run.pk)
    slips = run.current_payslips().select_related("employee", "supersedes")
    counts = {s: slips.filter(status=s).count() for s in Status.values}
    return render(request, "hr/payroll_run.html", {
        "run": run, "slips": slips, "counts": counts, "totals": services.run_totals(run),
    })


@admin_required
def payslip_edit(request, pk):
    slip = get_object_or_404(Payslip.objects.select_related("run", "employee"), pk=pk)
    editable = slip.status in (Status.DRAFT, Status.READY)
    form = PayslipInputsForm(request.POST or None, instance=slip)
    if not editable:
        for field in form.fields.values():
            field.disabled = True
    if request.method == "POST" and editable and form.is_valid():
        try:
            services.confirm_inputs(form.instance, request.user)
        except ValidationError as exc:
            if hasattr(exc, "error_dict"):
                for field, errs in exc.message_dict.items():
                    form.add_error(field if field in form.fields else None, errs)
            else:
                form.add_error(None, exc.messages)
            slip.refresh_from_db()
        else:
            messages.success(request, f"{slip.employee_name}: inputs confirmed. Review and approve.")
            return redirect("payslip_edit", pk=slip.pk)
    return render(request, "hr/payslip_edit.html", {
        "slip": slip, "form": form, "editable": editable,
        "outstanding": services.advance_outstanding(slip.employee),
        "stale": services.stale_reason(slip) if slip.status == Status.APPROVED else None,
        "next_slip": slip.run.current_payslips().filter(status=Status.DRAFT).exclude(pk=slip.pk).first(),
    })


@admin_required
@require_POST
def payslip_action(request, pk):
    slip = get_object_or_404(Payslip, pk=pk)
    action = request.POST.get("action")
    handlers = {
        "approve": services.approve,
        "unapprove": services.unapprove,
        "reopen": services.reopen,
        "correct": services.start_correction,
    }
    if action not in handlers:
        raise Http404
    try:
        result = handlers[action](slip, request.user)
    except ValidationError as exc:
        messages.error(request, _error_text(exc))
        return redirect("payslip_edit", pk=slip.pk)
    if action == "correct":
        messages.success(request, "Correction started as a new draft. The published version stays on record.")
        return redirect("payslip_edit", pk=result.pk)
    if action == "approve":
        nxt = slip.run.current_payslips().filter(status=Status.READY).first()
        messages.success(request, f"{slip.employee_name} approved.")
        if nxt:
            return redirect("payslip_edit", pk=nxt.pk)
        return redirect("payroll_run", pk=slip.run_id)
    return redirect("payslip_edit", pk=slip.pk)


@login_required
def payslip_view(request, pk):
    slip = get_object_or_404(Payslip.objects.select_related("run", "employee"), pk=pk)
    if not is_hr_admin(request.user):
        own = employee_of(request.user)
        if own is None or slip.employee_id != own.pk or slip.status not in (Status.PUBLISHED, Status.SUPERSEDED):
            raise Http404
    return render(request, "hr/payslip.html", {"slip": slip, "company": settings.COMPANY})


@admin_required
def audit_log(request):
    return render(request, "hr/audit_log.html", {"events": AuditEvent.objects.select_related("actor")[:300]})
