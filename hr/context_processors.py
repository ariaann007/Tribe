from .permissions import employee_of, is_hr_admin, is_team_lead


def roles(request):
    user = request.user
    return {
        "nav_is_admin": is_hr_admin(user),
        "nav_is_team_lead": is_team_lead(user),
        "nav_employee": employee_of(user),
    }
