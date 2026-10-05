"""Read and import staff from an onboarding spreadsheet (.xlsx).

Used by the "Import staff" page and the import_staff management command.

Imports are forgiving: anything missing or unreadable is left blank and
reported as a warning, so the rest of the file still goes in. Only rows
without an employee ID or name are skipped. Gaps then show up under
"Needs attention" on the dashboard.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .models import Department, Employee, EmploymentRecord, Office, Role, audit

COLUMNS = [
    "employee_id", "full_name", "office", "designation", "department", "date_joined", "salary",
    "pf_enrolled", "esic_enrolled", "uan", "pan", "esic_ip_number", "aadhaar_last4",
    "email", "phone", "business_unit", "team_lead_id", "probation_end_date", "opening_leave_balance",
    "weekly_hours", "role",
]
ESSENTIAL_COLUMNS = ["employee_id", "full_name"]
OFFICES = {"india": Office.INDIA, "in": Office.INDIA, "uk": Office.UK, "united kingdom": Office.UK}
ROLES = {"": Role.EMPLOYEE, "employee": Role.EMPLOYEE, "team lead": Role.TEAM_LEAD, "team_lead": Role.TEAM_LEAD,
         "admin": Role.ADMIN}
YES = {"y", "yes", "true", "1"}
NO = {"", "n", "no", "false", "0"}

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


def text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def parse_date(value, label):
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
    raise ValueError(f"{label} '{raw}' isn't a date (use DD/MM/YYYY)")


def parse_decimal(value, label):
    raw = text(value).replace(",", "").replace("₹", "").replace("£", "")
    if raw == "":
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"{label} '{raw}' isn't a number")


def parse_bool(value, label):
    raw = text(value).lower()
    if raw in YES:
        return True
    if raw in NO:
        return False
    raise ValueError(f"{label} '{text(value)}' should be Yes or No")


class WorkbookError(Exception):
    """The file can't be read at all (not a spreadsheet, or no ID/name columns)."""


@dataclass
class ImportResult:
    rows: list = field(default_factory=list)       # [(row_number, parsed_dict)]
    warnings: list = field(default_factory=list)   # [(row_number or None, message)]
    skipped: list = field(default_factory=list)    # [(row_number, reason)]
    existing: set = field(default_factory=set)

    @property
    def to_create(self):
        return [(n, p) for n, p in self.rows if p["employee_code"] not in self.existing]

    def warnings_for(self, number):
        return [m for n, m in self.warnings if n == number]


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


def _normal(header):
    """'Start date (DD/MM/YYYY)' -> 'start date'."""
    h = re.sub(r"\(.*?\)", "", text(header).lower())
    h = re.sub(r"[^a-z0-9 ]+", " ", h)
    return re.sub(r"\s+", " ", h).strip()


# Common column names people use in their own staff lists -> our columns.
ALIASES = {
    "employee id": "employee_id", "emp id": "employee_id", "id": "employee_id", "employee code": "employee_id",
    "staff id": "employee_id",
    "name": "full_name", "full name": "full_name", "employee name": "full_name", "staff name": "full_name",
    "role": "designation", "designation": "designation", "job title": "designation", "title": "designation",
    "position": "designation",
    "start date": "date_joined", "date joined": "date_joined", "joining date": "date_joined", "doj": "date_joined",
    "date of joining": "date_joined", "joined": "date_joined",
    "salary": "salary", "salary per month": "salary", "monthly salary": "salary", "basic": "salary",
    "basic pay": "salary", "annual salary": "salary",
    "office": "office", "location": "office", "country": "office",
    "department": "department", "dept": "department", "team": "department",
    "reporting under": "reporting_under", "reports to": "reporting_under", "manager": "reporting_under",
    "line manager": "reporting_under",
    "pf": "pf_enrolled", "pf enrolled": "pf_enrolled", "esic": "esic_enrolled", "esic enrolled": "esic_enrolled",
    "email": "email", "email address": "email", "phone": "phone", "mobile": "phone",
}
ALIASES.update({_normal(c): c for c in COLUMNS})
ALIASES.update({_normal(h): c for c, h in HEADINGS.items()})
MONTHLY_SALARY_HEADERS = {"salary per month", "monthly salary"}


def _load_rows(source, filename):
    if (filename or str(source)).lower().endswith(".csv"):
        import csv
        import io

        raw = source.read() if hasattr(source, "read") else open(source, "rb").read()
        return [tuple(r) for r in csv.reader(io.StringIO(raw.decode("utf-8-sig", errors="replace")))]
    from openpyxl import load_workbook

    try:
        sheet = load_workbook(source, data_only=True, read_only=True).worksheets[0]
    except FileNotFoundError:
        raise WorkbookError("File not found.")
    except Exception:
        raise WorkbookError("That isn't a readable spreadsheet. Upload an Excel (.xlsx) or CSV file.")
    return list(sheet.iter_rows(values_only=True))


def _find_header(rows):
    """The first row (within the top 10) that names a person column and at least one other known column."""
    for index, row in enumerate(rows[:10]):
        mapped = [ALIASES.get(_normal(h)) for h in row]
        if "designation" not in mapped:
            # In most staff lists "Role" is the job title, not the system role.
            mapped = ["designation" if m == "role" and _normal(h) == "role" else m for m, h in zip(mapped, row)]
        if "full_name" in mapped and len([m for m in mapped if m]) >= 2:
            return index, mapped, [_normal(h) for h in row]
    raise WorkbookError("Couldn't find a heading row with a Name column. Use the template, or add a 'Name' heading.")


def read_workbook(source, filename=None):
    """source: a path or a file-like object. Accepts our template or a typical staff list."""
    rows = _load_rows(source, filename)
    if not rows:
        raise WorkbookError("The spreadsheet is empty.")
    header_index, mapped, raw_headers = _find_header(rows)

    result = ImportResult()
    missing = [c for c in COLUMNS if c not in mapped and not (c == "team_lead_id" and "reporting_under" in mapped)]
    if missing:
        result.warnings.append((None, "Not in the file, left blank: " + ", ".join(HEADINGS[c] for c in missing) + "."))

    seen = set()
    for number, values in enumerate(rows[header_index + 1:], start=header_index + 2):
        row, monthly = {}, False
        for column, raw_header, value in zip(mapped, raw_headers, values):
            if column and text(value) and not text(row.get(column)):  # first non-empty wins (duplicate headers)
                row[column] = value
                if column == "salary" and raw_header in MONTHLY_SALARY_HEADERS:
                    monthly = True
        if not any(text(v) for v in row.values()):
            continue
        if text(row.get("employee_id")).lower() == "employee id":  # friendly heading row in our template
            continue
        name = text(row.get("full_name"))
        if not name:
            result.skipped.append((number, "no name"))
            continue
        details = ("office", "date_joined", "salary", "employee_id", "designation", "department")
        if not any(text(row.get(k)) for k in details) or text(row.get("designation")).replace(".", "").isdigit():
            result.skipped.append((number, f"'{name}' doesn't look like a staff row"))
            continue
        code = text(row.get("employee_id"))
        if code and code in seen:
            result.skipped.append((number, f"employee ID {code} appears earlier in the file"))
            continue
        if code:
            seen.add(code)
        parsed, warnings = parse_row(row)
        if monthly and parsed["office"] == Office.UK and parsed["salary"] is not None:
            parsed["salary"] = parsed["salary"] * 12
            warnings.append(f"UK salary given per month; recorded as £{parsed['salary']:,.2f} a year")
        parsed["reporting_under"] = text(row.get("reporting_under"))
        result.rows.append((number, parsed))
        result.warnings.extend((number, w) for w in warnings)

    _assign_codes(result)
    _resolve_team_leads(result)
    return result


def _assign_codes(result):
    """Match people without an ID to existing staff by name, or give them a new ID."""
    taken = set(Employee.objects.values_list("employee_code", flat=True)) | {
        p["employee_code"] for _, p in result.rows if p["employee_code"]
    }
    by_name = {e.full_name.strip().lower(): e.employee_code for e in Employee.objects.all()}
    counters = {}
    for number, p in result.rows:
        if p["employee_code"]:
            continue
        existing = by_name.get(p["full_name"].lower())
        if existing:
            p["employee_code"] = existing
            continue
        prefix = "IN" if p["office"] == Office.INDIA else "UK"
        n = counters.get(prefix, 0)
        while True:
            n += 1
            code = f"{prefix}-{n:03d}"
            if code not in taken:
                break
        counters[prefix] = n
        taken.add(code)
        p["employee_code"] = code
        result.warnings.append((number, f"no Employee ID; given {code} (change it with Edit details)"))
    codes = [p["employee_code"] for _, p in result.rows]
    result.existing = set(Employee.objects.filter(employee_code__in=codes).values_list("employee_code", flat=True))


def _resolve_team_leads(result):
    """team_lead_id, or 'Reporting under' as an ID, a name or a job title from the file or the system."""
    in_file = {p["employee_code"]: p for _, p in result.rows}
    by_title, by_name = {}, {}
    for _, p in result.rows:
        if p["designation"]:
            by_title.setdefault(p["designation"].lower(), p["employee_code"])
        by_name.setdefault(p["full_name"].lower(), p["employee_code"])
    for e in Employee.objects.all():
        if e.current_designation:
            by_title.setdefault(e.current_designation.lower(), e.employee_code)
        by_name.setdefault(e.full_name.lower(), e.employee_code)
    known = set(in_file) | set(Employee.objects.values_list("employee_code", flat=True))

    leads = set()
    for number, p in result.rows:
        ref = p["team_lead_code"] or p.pop("reporting_under", "")
        p.pop("reporting_under", None)
        if not ref:
            continue
        key = ref.lower()
        code = ref if ref in known else by_name.get(key) or by_title.get(key)
        if code and code != p["employee_code"]:
            p["team_lead_code"] = code
            leads.add(code)
        else:
            p["team_lead_code"] = ""
            result.warnings.append((number, f"reports to '{ref}', who isn't in the file or the system; left blank"))
    for code in leads:  # people others report to can approve their leave
        lead = in_file.get(code)
        if lead and lead["role"] == Role.EMPLOYEE and not lead.get("role_given"):
            lead["role"] = Role.TEAM_LEAD


def parse_row(row):
    """Return (parsed, warnings). Unreadable values become blank with a warning."""
    warnings = []

    def lenient(parser, key, label, default=None):
        try:
            value = parser(row.get(key), label)
        except ValueError as exc:
            warnings.append(f"{exc}; left blank")
            return default
        return default if value is None else value

    office = OFFICES.get(text(row.get("office")).lower())
    if office is None:
        warnings.append("office missing or not India/UK; set to India")
        office = Office.INDIA
    india = office == Office.INDIA

    role_given = bool(text(row.get("role")))
    role = ROLES.get(text(row.get("role")).lower())
    if role is None:
        warnings.append(f"role '{text(row.get('role'))}' not recognised; set to Employee")
        role = Role.EMPLOYEE

    designation = text(row.get("designation"))
    if not designation:
        warnings.append("no designation")
    date_joined = lenient(parse_date, "date_joined", "joining date")
    if date_joined is None:
        warnings.append("no joining date")
    salary = lenient(parse_decimal, "salary", "salary")
    if india and salary is None:
        warnings.append("no salary; add one before running payroll")

    aadhaar = text(row.get("aadhaar_last4")) if india else ""
    if aadhaar and not (aadhaar.isdigit() and len(aadhaar) == 4):
        warnings.append("Aadhaar should be exactly the last 4 digits; left blank")
        aadhaar = ""

    parsed = {
        "employee_code": text(row.get("employee_id")),
        "full_name": text(row.get("full_name")),
        "office": office,
        "role": role,
        "role_given": role_given,
        "designation": designation,
        "department": text(row.get("department")),
        "date_joined": date_joined,
        "probation_end_date": lenient(parse_date, "probation_end_date", "probation end date"),
        "salary": salary,
        "weekly_hours": lenient(parse_decimal, "weekly_hours", "weekly hours"),
        "email": text(row.get("email")),
        "phone": text(row.get("phone")),
        "business_unit": text(row.get("business_unit")),
        "team_lead_code": text(row.get("team_lead_id")),
        "pf_enrolled": lenient(parse_bool, "pf_enrolled", "PF enrolled", False) if india else False,
        "esic_enrolled": lenient(parse_bool, "esic_enrolled", "ESIC enrolled", False) if india else False,
        "uan": text(row.get("uan")) if india else "",
        "pan": text(row.get("pan")).upper()[:10] if india else "",
        "esic_ip_number": text(row.get("esic_ip_number")) if india else "",
        "aadhaar_last4": aadhaar,
        "opening_leave_balance": lenient(parse_decimal, "opening_leave_balance", "leave balance", Decimal("0"))
        if india else Decimal("0"),
    }
    if parsed["email"] and "@" not in parsed["email"]:
        warnings.append(f"email '{parsed['email']}' doesn't look valid; left blank")
        parsed["email"] = ""
    return parsed, warnings


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
        india = p["office"] == Office.INDIA
        # India staff need a salary for a history entry; without one the dashboard flags them instead.
        if p["salary"] is not None or not india:
            joined_known = p["date_joined"] is not None
            record = EmploymentRecord(
                employee=e, effective_from=p["date_joined"] or timezone.localdate(),
                designation=p["designation"] or "Not set", department=department, weekly_hours=p["weekly_hours"],
                basic_monthly=p["salary"] if india else None,
                annual_salary_gbp=p["salary"] if not india else None,
                created_by=actor if getattr(actor, "is_authenticated", False) else None,
                reason="Imported (salary as at import; earlier history not recorded)" if joined_known
                else "Imported (joining date unknown; dated from import)",
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
