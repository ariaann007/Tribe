from django import forms
from django.contrib.auth import get_user_model, password_validation

from .models import Advance, Department, Employee, EmploymentRecord, LeaveRequest, Office, Payslip

DATE = forms.DateInput(attrs={"type": "date"})

# Fields that only apply to India staff (statutory details and paid-leave balance).
INDIA_ONLY_FIELDS = [
    "uan", "pan", "esic_ip_number", "aadhaar_last4", "pf_enrolled", "esic_enrolled", "opening_leave_balance",
]

SALARY_LABELS = {
    Office.INDIA: ("Monthly Basic (₹ INR)", "Monthly Basic salary in Indian rupees."),
    Office.UK: ("Annual salary (£ GBP)", "Annual salary in pounds sterling."),
}


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = [
            "employee_code", "full_name", "email", "phone", "office", "role", "business_unit",
            "team_lead", "date_joined", "probation_end_date", "date_left",
            "uan", "pan", "esic_ip_number", "aadhaar_last4", "pf_enrolled", "esic_enrolled",
            "opening_leave_balance",
        ]
        widgets = {"date_joined": DATE, "probation_end_date": DATE, "date_left": DATE}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["team_lead"].queryset = Employee.objects.filter(role__in=["team_lead", "admin"])

    def limit_to_office(self, office):
        """Drop fields that don't apply to this office."""
        if office == Office.UK:
            for name in INDIA_ONLY_FIELDS:
                self.fields.pop(name, None)


class NewEmployeeForm(EmployeeForm):
    """Employee plus their first employment record."""

    designation = forms.CharField(max_length=100)
    department = forms.ModelChoiceField(Department.objects.all(), required=False)
    weekly_hours = forms.DecimalField(max_digits=4, decimal_places=1, required=False)
    salary = forms.DecimalField(
        label="Salary", max_digits=12, decimal_places=2, required=False, min_value=0,
        help_text="India: monthly Basic in ₹ INR. UK: annual salary in £ GBP.",
    )

    def clean(self):
        data = super().clean()
        office = data.get("office")
        salary = data.get("salary")
        if office == Office.INDIA and salary is None:
            self.add_error("salary", "Monthly Basic is required for India staff.")
        data["basic_monthly"] = salary if office == Office.INDIA else None
        data["annual_salary_gbp"] = salary if office == Office.UK else None
        if office == Office.UK:
            # India-only fields may have been filled before the office was switched; ignore them.
            for name in INDIA_ONLY_FIELDS:
                data.pop(name, None)
        dept = data.get("department")
        if dept and office and dept.office != office:
            self.add_error("department", "Department belongs to a different office.")
        return data

    def _post_clean(self):
        super()._post_clean()
        if self.cleaned_data.get("office") == Office.UK:
            for name in INDIA_ONLY_FIELDS:
                setattr(self.instance, name, Employee._meta.get_field(name).get_default())


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ["name", "office"]


class EmploymentRecordForm(forms.ModelForm):
    class Meta:
        model = EmploymentRecord
        fields = [
            "effective_from", "designation", "department", "weekly_hours", "basic_monthly", "annual_salary_gbp",
            "reason",
        ]
        widgets = {"effective_from": DATE}
        labels = {
            "basic_monthly": SALARY_LABELS[Office.INDIA][0],
            "annual_salary_gbp": SALARY_LABELS[Office.UK][0],
        }
        help_texts = {
            "basic_monthly": SALARY_LABELS[Office.INDIA][1],
            "annual_salary_gbp": SALARY_LABELS[Office.UK][1],
        }


class LeaveRequestForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ["start_date", "end_date", "days", "reason"]
        widgets = {"start_date": DATE, "end_date": DATE}


class AdminLeaveForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ["employee", "start_date", "end_date", "days", "reason"]
        widgets = {"start_date": DATE, "end_date": DATE}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = Employee.objects.filter(office=Office.INDIA)


class AdvanceForm(forms.ModelForm):
    class Meta:
        model = Advance
        fields = ["date_given", "amount", "monthly_recovery", "note"]
        widgets = {"date_given": DATE}


class PayslipInputsForm(forms.ModelForm):
    class Meta:
        model = Payslip
        fields = [
            "paid_leave_applied", "days_not_employed", "bonus", "incentives", "overtime",
            "other_additions", "other_deductions", "other_deductions_note", "advance_recovery", "admin_note",
        ]
        labels = {
            "days_not_employed": "Days not employed (joined/left mid-period)",
            "other_deductions_note": "What the other deduction is for",
            "admin_note": "Internal note (not shown to employee)",
        }


class NewRunForm(forms.Form):
    MONTHS = [(i, name) for i, name in enumerate(
        ["January", "February", "March", "April", "May", "June", "July", "August",
         "September", "October", "November", "December"], start=1)]
    month = forms.TypedChoiceField(choices=MONTHS, coerce=int, label="Period ending 25th of")
    year = forms.IntegerField(min_value=2020, max_value=2100)


class CreateLoginForm(forms.Form):
    username = forms.CharField(max_length=150)
    temporary_password = forms.CharField(widget=forms.PasswordInput, help_text="They must change it on first login.")

    def clean_username(self):
        username = self.cleaned_data["username"]
        if get_user_model().objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is taken.")
        return username

    def clean_temporary_password(self):
        password = self.cleaned_data["temporary_password"]
        password_validation.validate_password(password)
        return password


class ResetPasswordForm(forms.Form):
    temporary_password = forms.CharField(widget=forms.PasswordInput)

    def clean_temporary_password(self):
        password = self.cleaned_data["temporary_password"]
        password_validation.validate_password(password)
        return password
