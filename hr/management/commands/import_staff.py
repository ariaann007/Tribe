"""Import staff from an onboarding spreadsheet (.xlsx).

    python manage.py import_staff "imports/staff.xlsx"           # preview only
    python manage.py import_staff "imports/staff.xlsx" --commit  # save

Admins can do the same from the "Import staff" page in the app.
"""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from hr import importer
from hr.importer import COLUMNS  # noqa: F401  (re-exported for tests)
from hr.models import Office


class Command(BaseCommand):
    help = "Import staff from an onboarding .xlsx (preview by default; --commit to save)."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--commit", action="store_true", help="Save the rows. Without this, only preview.")

    def handle(self, path, commit, **options):
        try:
            result = importer.read_workbook(path)
        except importer.WorkbookError as exc:
            raise CommandError(str(exc))
        self.print_preview(result)
        for number, reason in result.skipped:
            self.stdout.write(self.style.ERROR(f"  Row {number} skipped: {reason}"))
        if result.warnings:
            self.stdout.write(self.style.WARNING("\nImported with gaps (left blank):"))
            for number, message in result.warnings:
                self.stdout.write(f"  - {'Row ' + str(number) + ': ' if number else ''}{message}")
        if not commit:
            self.stdout.write(self.style.WARNING(
                f"\nPreview only: {len(result.to_create)} to add, {len(result.existing)} already in the system "
                "(skipped). Run again with --commit to save."
            ))
            return
        try:
            created = importer.create(result.to_create)
        except ValidationError as exc:
            raise CommandError(f"Nothing was saved: {exc}")
        self.stdout.write(self.style.SUCCESS(f"\nAdded {len(created)} staff. Skipped {len(result.existing)} existing."))

    def print_preview(self, result):
        self.stdout.write(f"{'ID':<8}{'Name':<30}{'Office':<7}{'Designation':<34}{'Joined':<12}"
                          f"{'Salary':>12}  PF  ESIC  Status")
        for _, p in result.rows:
            currency = "INR" if p["office"] == Office.INDIA else "GBP"
            salary = f"{currency} {p['salary']:,.0f}" if p["salary"] is not None else "-"
            status = "exists, skip" if p["employee_code"] in result.existing else "new"
            self.stdout.write(
                f"{p['employee_code']:<8}{p['full_name'][:29]:<30}{p['office']:<7}{p['designation'][:33]:<34}"
                f"{p['date_joined'].strftime('%d/%m/%Y') if p['date_joined'] else '-':<10}  {salary:>12}  {'Y' if p['pf_enrolled'] else '-':<3} "
                f"{'Y' if p['esic_enrolled'] else '-':<5} {status}"
            )
