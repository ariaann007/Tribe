"""Load fictional demo data for trying the system locally.

    python manage.py seed_demo

Never run this against the live database. All people below are made up.
"""

from datetime import date
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from hr.models import Department, Employee, EmploymentRecord, LeaveRequest, Office, Role, UserSettings

# Demo-only logins. Each user is asked to change it on first login.
DEMO_PASSWORDS = {
    "admin": "demo-admin-2026!",
    "teamlead": "demo-lead-2026!",
    "staff": "demo-staff-2026!",
}

INDIA_STAFF = [
    # code, name, designation, department, basic, pf, esic, joined, probation_end
    ("2001", "Meera Nair", "Customer Success Associate", "Customer Support Team", "19500", False, True, date(2024, 3, 4), None),
    ("2002", "Arjun Menon", "Compliance Officer", "Compliance", "25000", False, False, date(2023, 10, 16), None),
    ("2003", "Divya Krishnan", "Junior Accounts Assistant", "Accounts", "14000", True, True, date(2025, 10, 20), None),
    ("2004", "Rahul Varma", "Technical Support", "Technology", "28500", True, False, date(2022, 6, 1), None),
    ("2005", "Anu Thomas", "Customer Success Associate", "Customer Support Team", "18000", False, False, date(2026, 8, 1), date(2026, 10, 31)),
]

UK_STAFF = [
    ("U001", "Sam Taylor", "Operations Manager", "Operations", date(2021, 10, 18)),
    ("U002", "Priya Shah", "Immigration Adviser", "Immigration", date(2023, 2, 6)),
]


class Command(BaseCommand):
    help = "Load fictional demo staff, leave and logins (local use only)."

    @transaction.atomic
    def handle(self, *args, **options):
        if Employee.objects.exists():
            raise CommandError("There is already staff data. Demo data is only for an empty database.")
        User = get_user_model()

        def login(username, superuser=False):
            maker = User.objects.create_superuser if superuser else User.objects.create_user
            user = maker(username=username, password=DEMO_PASSWORDS[username], email="")
            UserSettings.objects.create(user=user, must_change_password=True)
            return user

        depts = {}
        for office, names in [
            (Office.INDIA, {row[3] for row in INDIA_STAFF}),
            (Office.UK, {row[3] for row in UK_STAFF}),
        ]:
            for name in names:
                depts[name] = Department.objects.create(name=name, office=office)

        admin = Employee.objects.create(
            employee_code="U000", full_name="Demo Admin", office=Office.UK, role=Role.ADMIN,
            date_joined=date(2020, 1, 6), designation="HR Director", user=login("admin", superuser=True),
        )
        EmploymentRecord.objects.create(employee=admin, effective_from=admin.date_joined, designation="HR Director", reason="Joined")

        for code, name, title, dept, joined in UK_STAFF:
            e = Employee.objects.create(
                employee_code=code, full_name=name, office=Office.UK, date_joined=joined,
                designation=title, department=depts[dept],
            )
            EmploymentRecord.objects.create(employee=e, effective_from=joined, designation=title,
                                            department=depts[dept], weekly_hours=D("37.5"), reason="Joined")

        lead = Employee.objects.create(
            employee_code="2000", full_name="Lakshmi Pillai", office=Office.INDIA, role=Role.TEAM_LEAD,
            business_unit="Calicut", date_joined=date(2021, 10, 12), designation="Team Lead",
            department=depts["Customer Support Team"], pf_enrolled=True, uan="100000000001",
            pan="ABCDE1234F", opening_leave_balance=D("4"), user=login("teamlead"),
        )
        EmploymentRecord.objects.create(employee=lead, effective_from=lead.date_joined, designation="Team Lead",
                                        department=lead.department, basic_monthly=D("38000"), reason="Joined")

        for code, name, title, dept, basic, pf, esic, joined, probation in INDIA_STAFF:
            e = Employee.objects.create(
                employee_code=code, full_name=name, office=Office.INDIA, business_unit="Calicut",
                date_joined=joined, probation_end_date=probation, designation=title, department=depts[dept],
                team_lead=lead, pf_enrolled=pf, esic_enrolled=esic, opening_leave_balance=D("2"),
                uan=f"10000000{code}" if pf else "", esic_ip_number=f"31000{code}" if esic else "",
                user=login("staff") if code == "2001" else None,
            )
            first_basic = D(basic) - D("1500") if joined.year < 2025 else D(basic)
            EmploymentRecord.objects.create(employee=e, effective_from=joined, designation=title,
                                            department=depts[dept], basic_monthly=first_basic, reason="Joined")
            if first_basic != D(basic):
                EmploymentRecord.objects.create(employee=e, effective_from=date(2026, 4, 1), designation=title,
                                                department=depts[dept], basic_monthly=D(basic), reason="Annual increment")

        meera = Employee.objects.get(employee_code="2001")
        LeaveRequest.objects.create(employee=meera, start_date=date(2026, 9, 8), end_date=date(2026, 9, 10),
                                    days=D("3"), reason="Family function", status=LeaveRequest.Status.APPROVED)
        LeaveRequest.objects.create(employee=meera, start_date=date(2026, 10, 14), end_date=date(2026, 10, 14),
                                    days=D("0.5"), reason="Appointment")
        arjun = Employee.objects.get(employee_code="2002")
        LeaveRequest.objects.create(employee=arjun, start_date=date(2026, 9, 15), end_date=date(2026, 9, 15),
                                    days=D("1"), reason="Unwell", status=LeaveRequest.Status.APPROVED)

        self.stdout.write(self.style.SUCCESS(
            "Demo data loaded. Logins: admin, teamlead, staff. "
            "Passwords are in hr/management/commands/seed_demo.py (DEMO_PASSWORDS)."
        ))
