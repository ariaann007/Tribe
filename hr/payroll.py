"""India payroll rules for Denizns Ventures Pvt Ltd.

Pure functions only (no database access) so the rules can be tested in
isolation. Every amount is a Decimal; never pass floats in here.

Rules agreed with management (see README "Payroll rules"):
- Salary is Basic only. Pay period runs from the 26th to the 25th.
- Unpaid leave (LOP) = Basic / 23 x unpaid days.
- Unpaid days = leave taken - paid leave applied (+ days not employed).
- PF: 12% of earned Basic, wage capped at 15,000 (max 1,800). Employer pays the same.
- ESIC: 0.75% employee / 3.25% employer of earned Basic, for staff whose
  Basic was <= 21,000 at the start of the ESIC contribution period.
- Bonus, incentives, overtime and other additions are paid in full and are
  not part of the PF/ESIC wage (matches the existing salary sheet).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

WORKING_DAYS = Decimal("23")
MONTHLY_LEAVE_ACCRUAL = Decimal("1.5")

PF_RATE = Decimal("0.12")
PF_WAGE_CEILING = Decimal("15000")

ESIC_EMPLOYEE_RATE = Decimal("0.0075")
ESIC_EMPLOYER_RATE = Decimal("0.0325")
ESIC_WAGE_LIMIT = Decimal("21000")
# ESIC rules say contributions are rounded up to the next rupee. Set to False
# to keep paise, as the old spreadsheet did.
ESIC_ROUND_UP = True

# Annual income above which TDS normally becomes payable under the new regime.
TDS_ANNUAL_THRESHOLD = Decimal("1200000")
TDS_WARNING_FROM = Decimal("1000000")

PERIOD_START_DAY = 26

ZERO = Decimal("0")


def money(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def whole_rupee(value):
    return Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def esic_amount(wage, rate, round_up=None):
    round_up = ESIC_ROUND_UP if round_up is None else round_up
    raw = wage * rate
    if round_up:
        return raw.quantize(Decimal("1"), rounding=ROUND_CEILING)
    return money(raw)


@dataclass
class PayInputs:
    basic: Decimal
    leave_taken: Decimal = ZERO
    paid_leave_applied: Decimal = ZERO
    days_not_employed: Decimal = ZERO
    bonus: Decimal = ZERO
    incentives: Decimal = ZERO
    overtime: Decimal = ZERO
    other_additions: Decimal = ZERO
    other_deductions: Decimal = ZERO
    advance_recovery: Decimal = ZERO
    pf_applies: bool = False
    esic_applies: bool = False


@dataclass
class PayResult:
    lop_days: Decimal
    lop_amount: Decimal
    earned_basic: Decimal
    pf_wage: Decimal
    pf_employee: Decimal
    pf_employer: Decimal
    esic_wage: Decimal
    esic_employee: Decimal
    esic_employer: Decimal
    total_earnings: Decimal
    total_deductions: Decimal
    net_pay: Decimal
    employer_cost: Decimal


def calculate(inputs, esic_round_up=None):
    if inputs.paid_leave_applied > inputs.leave_taken:
        raise ValueError("Paid leave applied cannot be more than leave taken.")

    unpaid_leave = inputs.leave_taken - inputs.paid_leave_applied
    lop_days = min(unpaid_leave + inputs.days_not_employed, WORKING_DAYS)
    lop_amount = min(money(inputs.basic * lop_days / WORKING_DAYS), inputs.basic)
    earned_basic = inputs.basic - lop_amount

    pf_wage = min(earned_basic, PF_WAGE_CEILING) if inputs.pf_applies else ZERO
    pf_employee = whole_rupee(pf_wage * PF_RATE)
    pf_employer = pf_employee

    esic_wage = earned_basic if inputs.esic_applies else ZERO
    esic_employee = esic_amount(esic_wage, ESIC_EMPLOYEE_RATE, esic_round_up)
    esic_employer = esic_amount(esic_wage, ESIC_EMPLOYER_RATE, esic_round_up)

    total_earnings = (
        inputs.basic + inputs.bonus + inputs.incentives + inputs.overtime + inputs.other_additions
    )
    total_deductions = (
        lop_amount + pf_employee + esic_employee + inputs.other_deductions + inputs.advance_recovery
    )
    net_pay = total_earnings - total_deductions
    employer_cost = total_earnings - lop_amount + pf_employer + esic_employer

    return PayResult(
        lop_days=lop_days,
        lop_amount=lop_amount,
        earned_basic=earned_basic,
        pf_wage=pf_wage,
        pf_employee=pf_employee,
        pf_employer=pf_employer,
        esic_wage=esic_wage,
        esic_employee=esic_employee,
        esic_employer=esic_employer,
        total_earnings=money(total_earnings),
        total_deductions=money(total_deductions),
        net_pay=money(net_pay),
        employer_cost=money(employer_cost),
    )


def estimated_employer_contributions(basic, pf_enrolled, esic_applies):
    """Monthly employer PF + ESIC for a full month with no unpaid leave."""
    result = calculate(PayInputs(basic=basic, pf_applies=pf_enrolled, esic_applies=esic_applies))
    return result.pf_employer + result.esic_employer


# ---- Pay periods (26th to 25th) -------------------------------------------

def period_ending(year, month):
    """The pay period that ends on the 25th of the given month."""
    end = date(year, month, PERIOD_START_DAY - 1)
    start = (end.replace(day=1) - timedelta(days=1)).replace(day=PERIOD_START_DAY)
    return start, end


def period_containing(day):
    if day.day >= PERIOD_START_DAY:
        nxt = (day.replace(day=1) + timedelta(days=32)).replace(day=1)
        return period_ending(nxt.year, nxt.month)
    return period_ending(day.year, day.month)


def esic_contribution_period_start(day):
    """ESIC contribution periods run April-September and October-March."""
    if 4 <= day.month <= 9:
        return date(day.year, 4, 1)
    if day.month >= 10:
        return date(day.year, 10, 1)
    return date(day.year - 1, 10, 1)


def round_to_half(value):
    return (Decimal(value) * 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / 2


def suggested_days_not_employed(period_start, period_end, date_joined, date_left):
    """Working days outside employment, scaled from calendar days to 23."""
    total = (period_end - period_start).days + 1
    first = max(period_start, date_joined)
    last = min(period_end, date_left) if date_left else period_end
    employed = max((last - first).days + 1, 0)
    outside = total - employed
    if outside <= 0:
        return ZERO
    return round_to_half(WORKING_DAYS * outside / total)
