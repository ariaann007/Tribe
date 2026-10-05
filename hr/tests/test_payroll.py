"""Payroll maths, checked against the August 2026 salary sheet and a
26 Aug - 25 Sep 2026 payslip. Names and employee IDs are left out on purpose."""

from datetime import date
from decimal import Decimal as D

from django.test import SimpleTestCase

from hr import payroll
from hr.payroll import PayInputs, calculate


def pay(basic, round_up=False, **kw):
    return calculate(PayInputs(basic=D(basic), **{k: D(v) if not isinstance(v, bool) else v for k, v in kw.items()}),
                     esic_round_up=round_up)


class SalarySheetAugust2026(SimpleTestCase):
    """Rows from the sheet, using its own paise-based ESIC."""

    def test_low_basic_pf_and_esic(self):
        r = pay("8000", pf_applies=True, esic_applies=True)
        self.assertEqual(r.pf_employee, D("960"))
        self.assertEqual(r.esic_employee, D("60.00"))
        # The sheet showed 7,040 because ESIC was left out of its deductions total.
        self.assertEqual(r.net_pay, D("6980.00"))

    def test_pf_capped_with_other_deduction(self):
        r = pay("28500", pf_applies=True, other_deductions="5000")
        self.assertEqual(r.pf_employee, D("1800"))
        self.assertEqual(r.net_pay, D("21700.00"))

    def test_half_day_unpaid_leave_no_pf_esic(self):
        r = pay("25000", leave_taken="14.5")
        self.assertEqual(r.lop_amount, D("15760.87"))  # sheet: 15,760
        self.assertEqual(r.earned_basic, D("9239.13"))

    def test_pf_and_esic_18000(self):
        r = pay("18000", pf_applies=True, esic_applies=True)
        self.assertEqual((r.pf_employee, r.esic_employee, r.net_pay), (D("1800"), D("135.00"), D("16065.00")))

    def test_pf_and_esic_19500(self):
        r = pay("19500", pf_applies=True, esic_applies=True)
        self.assertEqual(r.net_pay, D("17553.75"))

    def test_pf_only(self):
        self.assertEqual(pay("42000", pf_applies=True).net_pay, D("40200.00"))

    def test_bonus_not_in_esic_wage(self):
        r = pay("19500", esic_applies=True, bonus="2000")
        self.assertEqual(r.esic_employee, D("146.25"))
        self.assertEqual(r.net_pay, D("21353.75"))

    def test_five_days_unpaid(self):
        self.assertEqual(pay("18000", leave_taken="5").lop_amount, D("3913.04"))

    def test_eighteen_days_unpaid(self):
        self.assertEqual(pay("25000", leave_taken="18").lop_amount, D("19565.22"))

    def test_sheet_gross_total(self):
        basics = [8000, 28500, 25000, 18000, 19500, 18000, 16500, 42000, 19500, 25250, 25500, 18000, 25000]
        self.assertEqual(sum(basics) + 2000, 290750)  # the sheet's formula showed 2,22,250


class ExistingPayslipSeptember2026(SimpleTestCase):
    def test_matches_existing_payslip(self):
        r = pay("19500", leave_taken="2", esic_applies=True)
        self.assertEqual(r.lop_amount, D("1695.65"))
        self.assertEqual(r.esic_employee, D("133.53"))
        self.assertEqual(r.total_deductions, D("1829.18"))
        self.assertEqual(r.net_pay, D("17670.82"))


class Rules(SimpleTestCase):
    def test_esic_rounds_up_to_rupee_by_default(self):
        r = pay("19500", round_up=True, esic_applies=True)
        self.assertEqual(r.esic_employee, D("147"))
        self.assertEqual(r.esic_employer, D("634"))  # 633.75 rounded up

    def test_paid_leave_reduces_unpaid_days(self):
        r = pay("23000", leave_taken="3", paid_leave_applied="1.5")
        self.assertEqual(r.lop_days, D("1.5"))
        self.assertEqual(r.lop_amount, D("1500.00"))

    def test_pf_on_earned_basic(self):
        r = pay("16000", leave_taken="2", pf_applies=True)
        self.assertEqual(r.pf_wage, D("14608.70"))
        self.assertEqual(r.pf_employee, D("1753"))

    def test_lop_never_exceeds_basic(self):
        r = pay("10000", leave_taken="30")
        self.assertEqual(r.lop_amount, D("10000"))
        self.assertEqual(r.net_pay, D("0.00"))

    def test_paid_cannot_exceed_taken(self):
        with self.assertRaises(ValueError):
            pay("10000", leave_taken="1", paid_leave_applied="2")

    def test_employer_cost(self):
        r = pay("18000", pf_applies=True, esic_applies=True)
        self.assertEqual(r.pf_employer, D("1800"))
        self.assertEqual(r.esic_employer, D("585.00"))
        self.assertEqual(r.employer_cost, D("20385.00"))


class Periods(SimpleTestCase):
    def test_period_ending(self):
        self.assertEqual(payroll.period_ending(2026, 9), (date(2026, 8, 26), date(2026, 9, 25)))
        self.assertEqual(payroll.period_ending(2027, 1), (date(2026, 12, 26), date(2027, 1, 25)))

    def test_period_containing(self):
        self.assertEqual(payroll.period_containing(date(2026, 8, 26))[1], date(2026, 9, 25))
        self.assertEqual(payroll.period_containing(date(2026, 9, 25))[1], date(2026, 9, 25))
        self.assertEqual(payroll.period_containing(date(2026, 12, 31))[1], date(2027, 1, 25))

    def test_esic_contribution_period(self):
        self.assertEqual(payroll.esic_contribution_period_start(date(2026, 9, 1)), date(2026, 4, 1))
        self.assertEqual(payroll.esic_contribution_period_start(date(2026, 11, 1)), date(2026, 10, 1))
        self.assertEqual(payroll.esic_contribution_period_start(date(2027, 2, 1)), date(2026, 10, 1))

    def test_days_not_employed_for_mid_period_joiner(self):
        start, end = payroll.period_ending(2026, 9)  # 31 days
        self.assertEqual(payroll.suggested_days_not_employed(start, end, date(2026, 9, 10), None), D("11"))
        self.assertEqual(payroll.suggested_days_not_employed(start, end, date(2025, 1, 1), None), D("0"))
