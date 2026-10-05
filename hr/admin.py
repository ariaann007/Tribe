"""Raw database access for superusers. Day-to-day work happens in the main app,
which enforces the payroll rules; payslips and the audit log are read-only here."""

from django.contrib import admin

from .models import (
    Advance, AuditEvent, Department, Employee, EmploymentRecord, LeaveRequest, Payslip, PayrollRun,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ["name", "office"]


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ["employee_code", "full_name", "office", "role", "designation", "date_joined", "date_left"]
    list_filter = ["office", "role"]
    search_fields = ["employee_code", "full_name", "email"]


@admin.register(EmploymentRecord)
class EmploymentRecordAdmin(ReadOnlyAdmin):
    list_display = ["employee", "effective_from", "designation", "basic_monthly", "reason"]


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ["employee", "start_date", "end_date", "days", "status"]
    list_filter = ["status"]


@admin.register(Advance)
class AdvanceAdmin(ReadOnlyAdmin):
    list_display = ["employee", "date_given", "amount", "monthly_recovery"]


@admin.register(PayrollRun)
class PayrollRunAdmin(ReadOnlyAdmin):
    list_display = ["period_end", "period_start", "created_at"]


@admin.register(Payslip)
class PayslipAdmin(ReadOnlyAdmin):
    list_display = ["employee_code", "employee_name", "run", "version", "status", "net_pay"]
    list_filter = ["status", "run"]


@admin.register(AuditEvent)
class AuditEventAdmin(ReadOnlyAdmin):
    list_display = ["at", "actor", "action", "target"]
    list_filter = ["action"]
