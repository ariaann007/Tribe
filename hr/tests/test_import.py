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

    def test_any_bad_row_blocks_the_whole_import(self):
        bad = ROWS + [{"employee_id": "2003", "full_name": "No Date", "office": "India", "designation": "X",
                       "salary": "1000"}]
        with self.assertRaises(CommandError):
            call_command("import_staff", workbook(bad), "--commit", stdout=open(tempfile.mktemp(), "w"))
        self.assertEqual(Employee.objects.count(), 0)
