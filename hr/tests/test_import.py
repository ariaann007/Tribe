import tempfile
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from openpyxl import Workbook

from hr.management.commands.import_staff import COLUMNS
from hr.models import Employee, Office


def workbook(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(COLUMNS)
    ws.append(["Employee ID"] + [""] * (len(COLUMNS) - 1))  # friendly heading row
    for row in rows:
        ws.append([row.get(c, "") for c in COLUMNS])
    path = Path(tempfile.mkdtemp()) / "staff.xlsx"
    wb.save(path)
    return str(path)


ROWS = [
    {"employee_id": "2001", "full_name": "Lead Person", "office": "India", "designation": "Team Lead",
     "department": "Support", "date_joined": "01/04/2022", "salary": "38000", "pf_enrolled": "Yes",
     "esic_enrolled": "No", "role": "Team lead", "opening_leave_balance": "3"},
    {"employee_id": "2002", "full_name": "Staff Person", "office": "India", "designation": "Associate",
     "department": "Support", "date_joined": date(2024, 3, 4), "salary": 19500, "pf_enrolled": "No",
     "esic_enrolled": "Yes", "team_lead_id": "2001", "uan": 102220276659},
    {"employee_id": "U9", "full_name": "UK Person", "office": "UK", "designation": "Adviser",
     "date_joined": "2023-02-06", "salary": "35000", "uan": "ignored"},
]


class ImportStaff(TestCase):
    def test_preview_saves_nothing(self):
        call_command("import_staff", workbook(ROWS), stdout=open(tempfile.mktemp(), "w"))
        self.assertEqual(Employee.objects.count(), 0)

    def test_commit_creates_staff_history_and_team_lead(self):
        call_command("import_staff", workbook(ROWS), "--commit", stdout=open(tempfile.mktemp(), "w"))
        staff = Employee.objects.get(employee_code="2002")
        self.assertEqual(staff.team_lead.employee_code, "2001")
        self.assertEqual(staff.basic_on(date(2026, 10, 1)), D("19500"))
        self.assertEqual(staff.uan, "102220276659")
        self.assertTrue(staff.esic_enrolled)
        self.assertEqual(Employee.objects.get(employee_code="2001").opening_leave_balance, D("3"))
        uk = Employee.objects.get(employee_code="U9")
        self.assertEqual((uk.office, uk.uan), (Office.UK, ""))
        self.assertEqual(uk.employment_records.get().annual_salary_gbp, D("35000"))
        # Re-running skips existing people.
        call_command("import_staff", workbook(ROWS), "--commit", stdout=open(tempfile.mktemp(), "w"))
        self.assertEqual(Employee.objects.count(), 3)

    def test_rows_with_gaps_are_still_imported(self):
        gappy = ROWS + [
            {"employee_id": "2003", "full_name": "No Date Or Salary", "office": "India", "pf_enrolled": "maybe",
             "date_joined": "sometime"},
            {"employee_id": "", "full_name": "No ID"},
            {"employee_id": "2004", "full_name": "No Office", "team_lead_id": "9999"},
        ]
        call_command("import_staff", workbook(gappy), "--commit", stdout=open(tempfile.mktemp(), "w"))
        self.assertEqual(Employee.objects.count(), 5)  # 3 good + 2 with gaps; the row without an ID is skipped
        gap = Employee.objects.get(employee_code="2003")
        self.assertIsNone(gap.date_joined)
        self.assertFalse(gap.pf_enrolled)
        self.assertFalse(gap.employment_records.exists())  # no salary yet
        self.assertEqual(gap.next_anniversary(), (None, None))
        no_office = Employee.objects.get(employee_code="2004")
        self.assertEqual((no_office.office, no_office.team_lead), (Office.INDIA, None))

        from hr import services
        messages = [w.message for w in services.compliance_warnings() if w.employee == gap]
        self.assertIn("Joining date missing. Add it with Edit details.", messages)
        self.assertTrue(any("No salary record" in m for m in messages))
        # Payroll still includes them (and skips them only for lack of salary).
        admin_user = None
        run, skipped = services.create_run(2026, 9, admin_user)
        self.assertIn(gap, skipped)


class OwnStaffListLayout(TestCase):
    """A sheet laid out like the company's Google Sheets staff list."""

    def make(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["", "Source: August 2026 payroll sheet"])
        ws.append(["Name", "Role", "Salary per month", "Salary per month", "Office", "Start date",
                   "Reporting under", "Years of service", "PF"])
        ws.append(["Lead Person", "Team Lead India", 42000, None, "India", "16/12/2024", "Direction of Operations",
                   "1 yrs", "Yes"])
        ws.append(["Head Person", "Head of Support", 28500, None, "India", "19/12/2022", "Team Lead India", "", "Yes"])
        ws.append(["Staff Person", "Associate", 19500, None, "India", "17/02/2025", "Head of Support", "", "No"])
        ws.append(["UK Person", "Operations Executive", None, 2083.30, "UK", "04/02/2022", "", "", "No"])
        ws.append([])
        ws.append(["Data Analysis", ""])
        ws.append(["Total count", 13])
        ws.append(["India Staff Budget"])
        path = Path(tempfile.mkdtemp()) / "Staff List.xlsx"
        wb.save(path)
        return str(path)

    def test_reads_own_layout(self):
        from hr import importer

        result = importer.read_workbook(self.make())
        names = [p["full_name"] for _, p in result.rows]
        self.assertEqual(names, ["Lead Person", "Head Person", "Staff Person", "UK Person"])
        self.assertEqual(len(result.skipped), 3)  # Data Analysis, Total count, India Staff Budget
        p = {r["full_name"]: r for _, r in result.rows}
        self.assertEqual(p["Lead Person"]["employee_code"], "IN-001")
        self.assertEqual(p["Staff Person"]["team_lead_code"], p["Head Person"]["employee_code"])
        self.assertEqual(p["Head Person"]["team_lead_code"], p["Lead Person"]["employee_code"])
        self.assertEqual(p["Lead Person"]["team_lead_code"], "")  # Director isn't in the file
        self.assertEqual(p["Head Person"]["role"], "team_lead")
        self.assertEqual(p["UK Person"]["salary"], D("2083.30") * 12)
        self.assertTrue(p["Lead Person"]["pf_enrolled"])

        call_command("import_staff", self.make(), "--commit", stdout=open(tempfile.mktemp(), "w"))
        self.assertEqual(Employee.objects.count(), 4)
        uk = Employee.objects.get(full_name="UK Person")
        self.assertEqual(uk.employment_records.get().annual_salary_gbp, D("24999.60"))
        # Uploading the same list again matches people by name and adds nobody.
        again = importer.read_workbook(self.make())
        self.assertEqual(again.to_create, [])
