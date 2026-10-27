from datetime import date
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from hr import services
from hr.models import LeaveRequest, Office, Role
from hr.tests.test_flow import PASSWORD, make_employee


class PayslipDocument(TestCase):
    def setUp(self):
        self.admin = make_employee("A1", office=Office.UK, role=Role.ADMIN)
        self.staff = make_employee("P1", esic_enrolled=True, pf_enrolled=True, basic="19500",
                                   opening_leave_balance=D("0.5"))
        LeaveRequest.objects.create(employee=self.staff, start_date=date(2026, 9, 1), end_date=date(2026, 9, 3),
                                    days=D("3"), status=LeaveRequest.Status.APPROVED)
        run, _ = services.create_run(2026, 9, self.admin.user)
        self.slip = run.current_payslips().get(employee=self.staff)
        self.slip.bonus = D("1000")
        services.confirm_inputs(self.slip, self.admin.user)
        self.client.login(username="uA1", password=PASSWORD)

    def test_shows_only_lines_that_apply_and_net_in_words(self):
        page = self.client.get(reverse("payslip_view", args=[self.slip.pk]))
        self.assertContains(page, "Basic salary")
        self.assertContains(page, "Bonus")
        self.assertNotContains(page, "Overtime")
        self.assertContains(page, "Unpaid leave (1 day)")
        self.assertContains(page, "Provident Fund (12%)")
        self.assertContains(page, "Rupees")
        self.assertNotContains(page, "DRAFT")

    def test_tables_padded_to_equal_height(self):
        page = self.client.get(reverse("payslip_view", args=[self.slip.pk]))
        earnings, deductions = len(page.context["earnings"]), len(page.context["deductions"])
        self.assertEqual(earnings + len(page.context["earnings_pad"]),
                         deductions + len(page.context["deductions_pad"]))

    def test_no_watermark_before_or_after_publishing(self):
        self.assertNotContains(self.client.get(reverse("payslip_view", args=[self.slip.pk])), "slip-watermark")
        services.approve(self.slip, self.admin.user)
        services.publish_approved(self.slip.run, self.admin.user)
        self.assertNotContains(self.client.get(reverse("payslip_view", args=[self.slip.pk])), "slip-watermark")
