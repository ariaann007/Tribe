"""Read and import staff from an onboarding spreadsheet (.xlsx).

Used by the "Import staff" page and the import_staff management command.
Nothing is saved unless every row is valid. Existing employee IDs are skipped.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction

from .models import Department, Employee, EmploymentRecord, Office, Role, audit

COLUMNS = [
    "employee_id", "full_name", "office", "designation", "department", "date_joined", "salary",
    "pf_enrolled", "esic_enrolled", "uan", "pan", "esic_ip_number", "aadhaar_last4",
    "email", "phone", "business_unit", "team_lead_id", "probation_end_date", "opening_leave_balance",
    "weekly_hours", "role",
]
REQUIRED = ["employee_id", "full_name", "office", "designation", "date_joined"]
OFFICES = {"india": Office.INDIA, "in": Office.INDIA, "uk": Office.UK, "united kingdom": Office.UK}
ROLES = {"": Role.EMPLOYEE, "employee": Role.EMPLOYEE, "team lead": Role.TEAM_LEAD, "team_lead": Role.TEAM_LEAD,
         "admin": Role.ADMIN}
YES = {"y", "yes", "true", "1"}
NO = {"", "n", "no", "false", "0"}


def text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def parse_date(value, field):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = text(value)
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"{field}: '{raw}' is not a date (use DD/MM/YYYY)")


def parse_decimal(value, field):
    raw = text(value).replace(",", "").replace("₹", "").replace("£", "")
    if raw == "":
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"{field}: '{raw}' is not a number")


def parse_bool(value, field):
    raw = text(value).lower()
    if raw in YES:
        return True
    if raw in NO:
        return False
    raise ValueError(f"{field}: use Yes or No")



class WorkbookError(Exception):
    """The file can't be read at all (wrong format or missing columns)."""


HEADINGS = {
    "employee_id": "Employee ID", "full_name": "Full name", "office": "Office (India/UK)",
    "designation": "Designation", "department": "Department", "date_joined": "Date joined (DD/MM/YYYY)",
    "salary": "Salary (India: monthly Basic INR / UK: annual GBP)", "pf_enrolled": "PF enrolled (Yes/No)",
    "esic_enrolled": "ESIC enrolled (Yes/No)", "uan": "UAN", "pan": "PAN", "esic_ip_number": "ESIC insurance no.",
    "aadhaar_last4": "Aadhaar last 4", "email": "Email", "phone": "Phone", "business_unit": "Business unit",
    "team_lead_id": "Team lead's employee ID", "probation_end_date": "Probation ends",
    "opening_leave_balance": "Paid leave balance now (days)", "weekly_hours": "Weekly hours",
    "role": "System role (Employee/Team lead/Admin)",
}


def blank_template():
    """An empty onboarding workbook as bytes."""
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Staff"
    ws.append(COLUMNS)
    ws.append([HEADINGS[c] for c in COLUMNS])
    for cell in ws[1]:
        cell.font = Font(color="999999", size=8)
    for cell in ws[2]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="4A2264")
    for i in range(1, len(COLUMNS) + 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = 18
    for r in range(3, 203):
        for c in ("employee_id", "uan", "pan", "esic_ip_number", "aadhaar_last4", "phone", "team_lead_id"):
            ws.cell(row=r, column=COLUMNS.index(c) + 1).number_format = "@"
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


@dataclass
class ImportResult:
    rows: list = field(default_factory=list)       # [(row_number, parsed_dict)]
    errors: list = field(default_factory=list)
    existing: set = field(default_factory=set)

    @property
    def to_create(self):
        return [(n, p) for n, p in self.rows if p["employee_code"] not in self.existing]

    @property
    def ok(self):
        return not self.errors


def read_workbook(source):
    """source: a path or a file-like object."""
    from openpyxl import load_workbook

    try:
        sheet = load_workbook(source, data_only=True, read_only=True).worksheets[0]
    except FileNotFoundError:
        raise WorkbookError("File not found.")
    except Exception:
        raise WorkbookError("That isn't a readable .xlsx spreadsheet.")
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        raise WorkbookError("The spreadsheet is empty.")
    header = [text(h).lower() for h in rows[0]]
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        raise WorkbookError(f"Missing columns: {', '.join(missing)}. Use the template.")

    result = ImportResult()
    for number, values in enumerate(rows[1:], start=2):
        row = dict(zip(header, values))
        if not any(text(v) for v in row.values()):
            continue
        if text(row.get("employee_id")).lower() == "employee id":  # friendly heading row
            continue
        try:
            result.rows.append((number, parse_row(row)))
        except ValueError as exc:
            result.errors.append(f"Row {number}: {exc}")

    codes = [p["employee_code"] for _, p in result.rows]
    for code in sorted({c for c in codes if codes.count(c) > 1}):
        result.errors.append(f"Employee ID {code} appears more than once.")
    result.existing = set(Employee.objects.filter(employee_code__in=codes).values_list("employee_code", flat=True))
    known_leads = set(codes) | set(Employee.objects.values_list("employee_code", flat=True))
    for number, p in result.rows:
        if p["team_lead_code"] and p["team_lead_code"] not in known_leads:
            result.errors.append(f"Row {number}: team_lead_id {p['team_lead_code']} is not a known employee ID.")
    return result


def parse_row(row):
    for field in REQUIRED:
        if not text(row.get(field)):
            raise ValueError(f"{field} is required")
    office = OFFICES.get(text(row["office"]).lower())
    if office is None:
        raise ValueError("office must be India or UK")
    role = ROLES.get(text(row.get("role")).lower())
    if role is None:
        raise ValueError("role must be Employee, Team lead or Admin")
    salary = parse_decimal(row.get("salary"), "salary")
    if office == Office.INDIA and salary is None:
        raise ValueError("salary (monthly Basic in INR) is required for India staff")
    aadhaar = text(row.get("aadhaar_last4"))
    if aadhaar and not (aadhaar.isdigit() and len(aadhaar) == 4):
        raise ValueError("aadhaar_last4 must be exactly the last 4 digits")
    india = office == Office.INDIA
    return {
        "employee_code": text(row["employee_id"]),
        "full_name": text(row["full_name"]),
        "office": office,
        "role": role,
        "designation": text(row["designation"]),
        "department": text(row.get("department")),
        "date_joined": parse_date(row["date_joined"], "date_joined"),
        "probation_end_date": parse_date(row.get("probation_end_date"), "probation_end_date"),
        "salary": salary,
        "weekly_hours": parse_decimal(row.get("weekly_hours"), "weekly_hours"),
        "email": text(row.get("email")),
        "phone": text(row.get("phone")),
        "business_unit": text(row.get("business_unit")),
        "team_lead_code": text(row.get("team_lead_id")),
        "pf_enrolled": parse_bool(row.get("pf_enrolled"), "pf_enrolled") if india else False,
        "esic_enrolled": parse_bool(row.get("esic_enrolled"), "esic_enrolled") if india else False,
        "uan": text(row.get("uan")) if india else "",
        "pan": text(row.get("pan")).upper() if india else "",
        "esic_ip_number": text(row.get("esic_ip_number")) if india else "",
        "aadhaar_last4": aadhaar if india else "",
        "opening_leave_balance": (parse_decimal(row.get("opening_leave_balance"), "opening_leave_balance")
                                  or Decimal("0")) if india else Decimal("0"),
    }



@transaction.atomic
def create(rows, actor=None):
    created = {}
    for _, p in rows:
        department = None
        if p["department"]:
            department, _ = Department.objects.get_or_create(name=p["department"], office=p["office"])
        e = Employee(
            employee_code=p["employee_code"], full_name=p["full_name"], office=p["office"], role=p["role"],
            designation=p["designation"], department=department, date_joined=p["date_joined"],
            probation_end_date=p["probation_end_date"], email=p["email"], phone=p["phone"],
            business_unit=p["business_unit"], pf_enrolled=p["pf_enrolled"], esic_enrolled=p["esic_enrolled"],
            uan=p["uan"], pan=p["pan"], esic_ip_number=p["esic_ip_number"], aadhaar_last4=p["aadhaar_last4"],
            opening_leave_balance=p["opening_leave_balance"],
        )
        e.full_clean(exclude=["team_lead", "user"])
        e.save()
        record = EmploymentRecord(
            employee=e, effective_from=p["date_joined"], designation=p["designation"], department=department,
            weekly_hours=p["weekly_hours"],
            basic_monthly=p["salary"] if p["office"] == Office.INDIA else None,
            annual_salary_gbp=p["salary"] if p["office"] == Office.UK else None,
            reason="Imported at go-live (salary as at import; earlier history not recorded)",
        )
        record.full_clean(exclude=["created_by"])
        record.save()
        audit(actor, "employee.imported", e)
        created[e.employee_code] = (e, p["team_lead_code"])
    for e, lead_code in created.values():
        if lead_code:
            e.team_lead = Employee.objects.get(employee_code=lead_code)
            e.save(update_fields=["team_lead"])
    return created
