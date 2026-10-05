from datetime import date
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from hr import services
from hr.models import (
    AuditEvent, Department, Employee, EmploymentRecord, LeaveRequest, Office, Payslip, Role, UserSettings,
)

PASSWORD = "test-pass-for-unit-tests"


def make_user(username):
    user = get_user_model().objects.create_user(username=username, password=PASSWORD)
    UserSettings.objects.create(user=user, must_change_password=False)
    return user


def make_employee(code, office=Office.INDIA, basic="19500", role=Role.EMPLOYEE, login=True, **kw):
    dept, _ = Department.objects.get_or_create(name="Support", office=office)
    e = Employee.objects.create(
        employee_code=code, full_name=f"Person {code}", office=office, role=role,
        date_joined=kw.pop("date_joined", date(2025, 1, 1)), department=dept, designation="Associate",
        user=make_user(f"u{code}") if login else None, **kw,
    )
    EmploymentRecord.objects.create(
        employee=e, effective_from=e.date_joined, designation="Associate", department=dept,
        basic_monthly=D(basic) if office == Office.INDIA else None,
    )
    return e


class PayrollFlow(TestCase):
    def setUp(self):
        self.admin = make_employee("A1", office=Office.UK, role=Role.ADMIN)
        self.lead = make_employee("L1", role=Role.TEAM_LEAD, basic="40000")
        self.staff = make_employee("S9", esic_enrolled=True, team_lead=self.lead, opening_leave_balance=D("1"))
        self.client.login(username="uA1", password=PASSWORD)

    def test_full_month(self):
        LeaveRequest.objects.create(
            employee=self.staff, start_date=date(2026, 9, 1), end_date=date(2026, 9, 3), days=D("3"),
            status=LeaveRequest.Status.APPROVED,
        )
        run, skipped = services.create_run(2026, 9, self.admin.user)
        self.assertEqual(skipped, [])
        slip = run.current_payslips().get(employee=self.staff)
        # 1 carried + 1.5 accrued = 2.5 paid days available; 3 taken -> 0.5 unpaid.
        self.assertEqual(slip.paid_leave_applied, D("2.5"))
        self.assertEqual(slip.lop_days, D("0.5"))
        self.assertEqual(slip.status, Payslip.Status.DRAFT)

        # Employee can't see a draft.
        self.client.login(username="uS9", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("payslip_view", args=[slip.pk])).status_code, 404)

        self.client.login(username="uA1", password=PASSWORD)
        resp = self.client.post(reverse("payslip_edit", args=[slip.pk]), {
            "paid_leave_applied": "2.5", "days_not_employed": "0", "bonus": "1000", "incentives": "0",
            "overtime": "0", "other_additions": "0", "other_deductions": "0", "other_deductions_note": "",
            "advance_recovery": "0", "admin_note": "",
        })
        self.assertEqual(resp.status_code, 302)
        slip.refresh_from_db()
        self.assertEqual(slip.status, Payslip.Status.READY)
        self.assertEqual(slip.bonus, D("1000"))

        self.client.post(reverse("payslip_action", args=[slip.pk]), {"action": "approve"})
        self.client.post(reverse("payroll_run", args=[run.pk]), {"action": "publish"})
        slip.refresh_from_db()
        self.assertEqual(slip.status, Payslip.Status.PUBLISHED)
        self.assertEqual(slip.leave_balance_after, D("0"))
        self.assertEqual(services.leave_balance(self.staff), D("0"))

        # Locked once published.
        slip.bonus = D("5")
        with self.assertRaises(ValidationError):
            slip.save()

        # Employee now sees it.
        self.client.login(username="uS9", password=PASSWORD)
        self.assertContains(self.client.get(reverse("payslip_view", args=[slip.pk])), "Person S9")

    def test_cannot_apply_more_paid_leave_than_available(self):
        LeaveRequest.objects.create(
            employee=self.staff, start_date=date(2026, 9, 1), end_date=date(2026, 9, 5), days=D("5"),
            status=LeaveRequest.Status.APPROVED,
        )
        run, _ = services.create_run(2026, 9, self.admin.user)
        slip = run.current_payslips().get(employee=self.staff)
        slip.paid_leave_applied = D("4")
        with self.assertRaises(ValidationError):
            services.confirm_inputs(slip, self.admin.user)

    def test_correction_keeps_original(self):
        run, _ = services.create_run(2026, 9, self.admin.user)
        slip = run.current_payslips().get(employee=self.staff)
        services.confirm_inputs(slip, self.admin.user)
        services.approve(slip, self.admin.user)
        services.publish_approved(run, self.admin.user)
        slip.refresh_from_db()

        new = services.start_correction(slip, self.admin.user)
        new.bonus = D("500")
        services.confirm_inputs(new, self.admin.user)
        services.approve(new, self.admin.user)
        services.publish_approved(run, self.admin.user)
        slip.refresh_from_db()
        new.refresh_from_db()
        self.assertEqual(slip.status, Payslip.Status.SUPERSEDED)
        self.assertEqual(new.status, Payslip.Status.PUBLISHED)
        self.assertEqual(new.version, 2)
        self.assertEqual(run.current_payslips().filter(employee=self.staff).count(), 1)

    def test_publish_blocked_if_leave_changes_after_approval(self):
        run, _ = services.create_run(2026, 9, self.admin.user)
        slip = run.current_payslips().get(employee=self.staff)
        services.confirm_inputs(slip, self.admin.user)
        services.approve(slip, self.admin.user)
        LeaveRequest.objects.create(
            employee=self.staff, start_date=date(2026, 9, 10), end_date=date(2026, 9, 10), days=D("1"),
            status=LeaveRequest.Status.APPROVED,
        )
        published, blocked = services.publish_approved(run, self.admin.user)
        self.assertEqual(published, [])
        self.assertEqual(len(blocked), 1)

    def test_advance_recovery_suggested(self):
        self.staff.advances.create(date_given=date(2026, 8, 1), amount=D("5000"), monthly_recovery=D("2000"))
        run, _ = services.create_run(2026, 9, self.admin.user)
        slip = run.current_payslips().get(employee=self.staff)
        self.assertEqual(slip.advance_recovery, D("2000"))

    def test_uk_staff_not_in_payroll(self):
        run, _ = services.create_run(2026, 9, self.admin.user)
        self.assertFalse(run.payslips.filter(employee=self.admin).exists())


class Permissions(TestCase):
    def setUp(self):
        self.admin = make_employee("A1", office=Office.UK, role=Role.ADMIN)
        self.lead = make_employee("L1", role=Role.TEAM_LEAD, basic="40000")
        self.staff = make_employee("S1", team_lead=self.lead)
        self.other = make_employee("S2")

    def login(self, e):
        self.client.login(username=e.user.username, password=PASSWORD)

    def test_employee_blocked_from_admin_pages(self):
        self.login(self.staff)
        for name, args in [("dashboard", []), ("employee_list", []), ("payroll_runs", []),
                           ("employee_detail", [self.other.pk]), ("audit_log", [])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403, name)

    def test_employee_cannot_see_someone_elses_payslip(self):
        run, _ = services.create_run(2026, 9, self.admin.user)
        slip = run.current_payslips().get(employee=self.other)
        services.confirm_inputs(slip, self.admin.user)
        services.approve(slip, self.admin.user)
        services.publish_approved(run, self.admin.user)
        self.login(self.staff)
        self.assertEqual(self.client.get(reverse("payslip_view", args=[slip.pk])).status_code, 404)

    def test_team_lead_sees_no_salaries(self):
        self.login(self.lead)
        resp = self.client.get(reverse("team"))
        self.assertContains(resp, "Person S1")
        self.assertNotContains(resp, "19,500")
        self.assertNotContains(resp, "19500")
        self.assertEqual(self.client.get(reverse("employee_detail", args=[self.staff.pk])).status_code, 403)

    def test_team_lead_approves_only_own_team(self):
        mine = LeaveRequest.objects.create(employee=self.staff, start_date=date(2026, 9, 1), end_date=date(2026, 9, 1), days=1)
        theirs = LeaveRequest.objects.create(employee=self.other, start_date=date(2026, 9, 1), end_date=date(2026, 9, 1), days=1)
        self.login(self.lead)
        self.client.post(reverse("leave_decide", args=[mine.pk]), {"decision": "approve"})
        self.assertEqual(self.client.post(reverse("leave_decide", args=[theirs.pk]), {"decision": "approve"}).status_code, 403)
        mine.refresh_from_db()
        self.assertEqual(mine.status, LeaveRequest.Status.APPROVED)

    def test_nobody_approves_own_leave(self):
        own = LeaveRequest.objects.create(employee=self.admin, start_date=date(2026, 9, 1), end_date=date(2026, 9, 1), days=1)
        self.login(self.admin)
        self.assertEqual(self.client.post(reverse("leave_decide", args=[own.pk]), {"decision": "approve"}).status_code, 403)

    def test_uk_salary_rejected(self):
        record = EmploymentRecord(employee=self.admin, effective_from=date(2026, 1, 1), designation="X", basic_monthly=D("1"))
        with self.assertRaises(ValidationError):
            record.full_clean()

    def test_leave_cannot_cross_pay_period(self):
        leave = LeaveRequest(employee=self.staff, start_date=date(2026, 9, 24), end_date=date(2026, 9, 27), days=D("3"))
        with self.assertRaises(ValidationError):
            leave.full_clean()

    def test_temporary_password_forces_change(self):
        UserSettings.objects.filter(user=self.staff.user).update(must_change_password=True)
        self.login(self.staff)
        self.assertRedirects(self.client.get(reverse("me")), reverse("password_change"))


class PagesLoad(TestCase):
    def test_admin_pages(self):
        admin = make_employee("A1", office=Office.UK, role=Role.ADMIN)
        staff = make_employee("S1")
        run, _ = services.create_run(2026, 9, admin.user)
        slip = run.current_payslips().first()
        self.client.login(username="uA1", password=PASSWORD)
        pages = [
            ("dashboard", []), ("employee_list", []), ("employee_new", []), ("departments", []),
            ("employee_detail", [staff.pk]), ("employee_edit", [staff.pk]), ("employment_change_new", [staff.pk]),
            ("employment_change_new", [admin.pk]), ("advance_new", [staff.pk]), ("payroll_runs", []),
            ("payroll_run", [run.pk]), ("payslip_edit", [slip.pk]), ("payslip_view", [slip.pk]),
            ("leave_approvals", []), ("leave_record", []), ("audit_log", []), ("me", []),
        ]
        for name, args in pages:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)

    def test_add_employee_through_form(self):
        make_employee("A1", office=Office.UK, role=Role.ADMIN)
        dept = Department.objects.create(name="Accounts", office=Office.INDIA)
        self.client.login(username="uA1", password=PASSWORD)
        resp = self.client.post(reverse("employee_new"), {
            "employee_code": "3001", "full_name": "New Starter", "office": "IN", "role": "employee",
            "date_joined": "2026-10-01", "designation": "Assistant", "department": dept.pk,
            "basic_monthly": "16000", "opening_leave_balance": "0",
        })
        self.assertEqual(resp.status_code, 302)
        e = Employee.objects.get(employee_code="3001")
        self.assertEqual(e.basic_on(date(2026, 10, 1)), D("16000"))
        self.assertEqual(e.employment_records.count(), 1)


class DeleteEmployee(TestCase):
    def setUp(self):
        self.admin = make_employee("A1", office=Office.UK, role=Role.ADMIN)
        self.client.login(username="uA1", password=PASSWORD)

    def test_delete_mistake_record(self):
        e = make_employee("X1")
        user_id = e.user_id
        resp = self.client.post(reverse("employee_delete", args=[e.pk]))
        self.assertRedirects(resp, reverse("employee_list"))
        self.assertFalse(Employee.objects.filter(pk=e.pk).exists())
        self.assertFalse(get_user_model().objects.filter(pk=user_id).exists())

    def test_cannot_delete_someone_with_payslips(self):
        e = make_employee("X2")
        services.create_run(2026, 9, self.admin.user)
        self.assertContains(self.client.post(reverse("employee_delete", args=[e.pk])), "set a leaving date")
        self.assertTrue(Employee.objects.filter(pk=e.pk).exists())

    def test_cannot_delete_self(self):
        self.client.post(reverse("employee_delete", args=[self.admin.pk]))
        self.assertTrue(Employee.objects.filter(pk=self.admin.pk).exists())

    def test_staff_list_shows_actions(self):
        e = make_employee("X3")
        resp = self.client.get(reverse("employee_list"))
        self.assertContains(resp, reverse("employee_edit", args=[e.pk]))
        self.assertContains(resp, reverse("employee_delete", args=[e.pk]))

    def test_non_admin_cannot_delete(self):
        e = make_employee("X4")
        other = make_employee("X5")
        self.client.login(username="uX5", password=PASSWORD)
        self.assertEqual(self.client.post(reverse("employee_delete", args=[e.pk])).status_code, 403)
        self.assertTrue(Employee.objects.filter(pk=e.pk).exists())


class EditEmploymentHistory(TestCase):
    def setUp(self):
        self.admin = make_employee("A1", office=Office.UK, role=Role.ADMIN)
        self.client.login(username="uA1", password=PASSWORD)

    def test_edit_india_record_updates_salary_and_current_role(self):
        e = make_employee("H1", basic="18000")
        record = e.employment_records.get()
        resp = self.client.post(reverse("employment_record_edit", args=[e.pk, record.pk]), {
            "effective_from": "2025-01-01", "designation": "Senior Associate", "department": record.department_id,
            "weekly_hours": "40", "basic_monthly": "18500", "reason": "Joined",
        })
        self.assertRedirects(resp, reverse("employee_detail", args=[e.pk]))
        e.refresh_from_db()
        self.assertEqual(e.basic_on(date(2026, 1, 1)), D("18500"))
        self.assertEqual(e.designation, "Senior Associate")
        event = AuditEvent.objects.get(action="employee.history_edited")
        self.assertIn("18000.00 → 18500", event.detail)

    def test_admin_can_edit_own_history(self):
        record = self.admin.employment_records.get()
        resp = self.client.post(reverse("employment_record_edit", args=[self.admin.pk, record.pk]), {
            "effective_from": "2025-01-01", "designation": "HR Director", "department": record.department_id,
            "weekly_hours": "37.5", "reason": "Joined",
        })
        self.assertRedirects(resp, reverse("employee_detail", args=[self.admin.pk]))
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.designation, "HR Director")
        self.assertIsNone(self.admin.employment_records.get().basic_monthly)

    def test_delete_entry_but_not_the_last(self):
        e = make_employee("H2", basic="18000")
        first = e.employment_records.get()
        raise_ = EmploymentRecord.objects.create(
            employee=e, effective_from=date(2026, 4, 1), designation="Lead", department=first.department,
            basic_monthly=D("20000"), reason="Promotion",
        )
        self.client.post(reverse("employment_record_delete", args=[e.pk, raise_.pk]))
        self.assertEqual(e.employment_records.count(), 1)
        self.client.post(reverse("employment_record_delete", args=[e.pk, first.pk]))
        self.assertEqual(e.employment_records.count(), 1)

    def test_admin_cannot_remove_own_admin_role(self):
        resp = self.client.post(reverse("employee_edit", args=[self.admin.pk]), {
            "employee_code": "A1", "full_name": "Person A1", "role": "employee",
            "date_joined": "2025-01-01", "opening_leave_balance": "0",
        })
        self.assertContains(resp, "can&#x27;t remove your own admin role")
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, Role.ADMIN)

    def test_employee_cannot_edit_history(self):
        e = make_employee("H3")
        self.client.login(username="uH3", password=PASSWORD)
        record = e.employment_records.get()
        self.assertEqual(self.client.get(reverse("employment_record_edit", args=[e.pk, record.pk])).status_code, 403)


class EmploymentTimeline(TestCase):
    def test_shows_role_and_salary_changes_with_dates(self):
        admin = make_employee("A1", office=Office.UK, role=Role.ADMIN)
        e = make_employee("T1", basic="18000")
        first = e.employment_records.get()
        EmploymentRecord.objects.create(
            employee=e, effective_from=date(2026, 4, 1), designation="Senior Associate",
            department=first.department, basic_monthly=D("19500"), reason="Promotion",
        )
        EmploymentRecord.objects.create(
            employee=e, effective_from=date(2099, 1, 1), designation="Senior Associate",
            department=first.department, basic_monthly=D("19500"), weekly_hours=D("40"), reason="Hours change",
        )
        timeline = services.employment_timeline(e, today=date(2026, 10, 5))
        self.assertEqual([h.status for h in timeline], ["upcoming", "current", "past"])
        promo = timeline[1]
        self.assertEqual({c.label for c in promo.changes}, {"Role", "Salary"})
        salary = next(c for c in promo.changes if c.label == "Salary")
        self.assertEqual((salary.old, salary.new, salary.percent), (D("18000"), D("19500"), D("8.3")))
        self.assertTrue(timeline[2].is_first)

        self.client.login(username="uA1", password=PASSWORD)
        resp = self.client.get(reverse("employee_detail", args=[e.pk]))
        self.assertContains(resp, "Joined as Associate")
        self.assertContains(resp, "₹18,000")
        self.assertContains(resp, "₹19,500")
        self.assertContains(resp, "+8.3%")
        self.assertContains(resp, "01 Apr 2026")


class FutureDatedChanges(TestCase):
    def test_title_and_payslip_follow_history_by_date(self):
        admin = make_employee("A1", office=Office.UK, role=Role.ADMIN)
        e = make_employee("F1", basic="18000")
        first = e.employment_records.get()
        EmploymentRecord.objects.create(
            employee=e, effective_from=date(2026, 9, 1), designation="Team Lead",
            department=first.department, basic_monthly=D("20000"), reason="Promotion",
        )
        # Stored field is stale on purpose (as if the change was made outside the app).
        self.assertEqual(e.designation, "Associate")
        run, _ = services.create_run(2026, 9, admin.user)
        slip = run.current_payslips().get(employee=e)
        self.assertEqual((slip.designation, slip.basic), ("Team Lead", D("20000")))
        run_aug, _ = services.create_run(2026, 8, admin.user)
        old = run_aug.current_payslips().get(employee=e)
        self.assertEqual((old.designation, old.basic), ("Associate", D("18000")))
