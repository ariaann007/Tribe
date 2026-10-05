from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("setup/", views.first_run_setup, name="first_run_setup"),
    path("me/", views.me, name="me"),
    path("me/leave/new/", views.leave_new, name="leave_new"),
    path("me/leave/<int:pk>/cancel/", views.leave_cancel, name="leave_cancel"),
    path("team/", views.team, name="team"),
    path("leave/", views.leave_approvals, name="leave_approvals"),
    path("leave/record/", views.leave_record, name="leave_record"),
    path("leave/<int:pk>/decide/", views.leave_decide, name="leave_decide"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("departments/", views.departments, name="departments"),
    path("employees/", views.employee_list, name="employee_list"),
    path("employees/new/", views.employee_new, name="employee_new"),
    path("employees/<int:pk>/", views.employee_detail, name="employee_detail"),
    path("employees/<int:pk>/edit/", views.employee_edit, name="employee_edit"),
    path("employees/<int:pk>/delete/", views.employee_delete, name="employee_delete"),
    path("employees/<int:pk>/history/<int:record_pk>/edit/", views.employment_record_edit, name="employment_record_edit"),
    path("employees/<int:pk>/history/<int:record_pk>/delete/", views.employment_record_delete, name="employment_record_delete"),
    path("employees/<int:pk>/change/", views.employment_change_new, name="employment_change_new"),
    path("employees/<int:pk>/login/", views.employee_login_create, name="employee_login_create"),
    path("employees/<int:pk>/password/", views.employee_password_reset, name="employee_password_reset"),
    path("employees/<int:pk>/advance/", views.advance_new, name="advance_new"),
    path("payroll/", views.payroll_runs, name="payroll_runs"),
    path("payroll/<int:pk>/", views.payroll_run, name="payroll_run"),
    path("payslips/<int:pk>/", views.payslip_view, name="payslip_view"),
    path("payslips/<int:pk>/edit/", views.payslip_edit, name="payslip_edit"),
    path("payslips/<int:pk>/action/", views.payslip_action, name="payslip_action"),
    path("audit/", views.audit_log, name="audit_log"),
]
