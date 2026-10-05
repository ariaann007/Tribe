from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .models import Role


def employee_of(user):
    return getattr(user, "employee", None) if user.is_authenticated else None


def is_hr_admin(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    employee = employee_of(user)
    return bool(employee and employee.role == Role.ADMIN)


def is_team_lead(user):
    employee = employee_of(user)
    return bool(employee and employee.role == Role.TEAM_LEAD)


def can_decide_leave(user, leave):
    """Admins decide anyone's leave; team leads decide their team's. Nobody decides their own."""
    employee = employee_of(user)
    if employee and leave.employee_id == employee.pk:
        return False
    if is_hr_admin(user):
        return True
    return bool(employee and employee.role == Role.TEAM_LEAD and leave.employee.team_lead_id == employee.pk)


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not is_hr_admin(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


def employee_required(view):
    """Logged-in user linked to an employee record."""
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if employee_of(request.user) is None:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper
