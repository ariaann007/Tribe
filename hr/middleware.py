from django.shortcuts import redirect
from django.urls import reverse

from .models import UserSettings


class ForcePasswordChangeMiddleware:
    """Send users with a temporary password to the change-password page."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated:
            allowed = {reverse("password_change"), reverse("password_change_done"), reverse("logout")}
            if request.path not in allowed and not request.path.startswith("/static/"):
                flags = UserSettings.objects.filter(user=user).first()
                if flags and flags.must_change_password:
                    return redirect("password_change")
        return self.get_response(request)
