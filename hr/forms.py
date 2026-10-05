from django import forms
from django.contrib.auth import get_user_model, password_validation

from .models import Advance, Department, Employee, EmploymentRecord, LeaveRequest, Office, Payslip

DATE = forms.DateInput(attrs={"type": "date"})


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


class NewEmployeeForm(EmployeeForm):
    """Employee plus their first employment record."""

    designation = forms.CharField(max_length=100)
    department = forms.ModelChoiceField(Department.objects.all(), required=False)
    weekly_hours = forms.DecimalField(max_digits=4, decimal_places=1, required=False)
    basic_monthly = forms.DecimalField(
        label="Monthly Basic (INR)", max_digits=12, decimal_places=2, required=False,
        help_text="India staff only. Leave blank for UK staff.",
    )
    annual_salary_gbp = forms.DecimalField(
        label="Annual salary (GBP)", max_digits=12, decimal_places=2, required=False,
        help_text="UK staff only. Leave blank for India staff.",
    )

    def clean(self):
        data = super().clean()
        office = data.get("office")
        if office == Office.INDIA and data.get("basic_monthly") is None:
            self.add_error("basic_monthly", "Basic salary is required for India staff.")
        if office == Office.UK and data.get("basic_monthly") is not None:
            self.add_error("basic_monthly", "UK staff have an annual GBP salary instead.")
        if office == Office.INDIA and data.get("annual_salary_gbp") is not None:
            self.add_error("annual_salary_gbp", "India staff are paid a monthly INR Basic instead.")
        dept = data.get("department")
        if dept and office and dept.office != office:
            self.add_error("department", "Department belongs to a different office.")
        return data


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
