from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from .models import UserSettings


class PasswordChangeView(auth_views.PasswordChangeView):
    success_url = reverse_lazy("password_change_done")

    def form_valid(self, form):
        response = super().form_valid(form)
        UserSettings.objects.update_or_create(user=self.request.user, defaults={"must_change_password": False})
        return response


urlpatterns = [
    path("change/", PasswordChangeView.as_view(), name="password_change"),
    path("change/done/", auth_views.PasswordChangeDoneView.as_view(), name="password_change_done"),
]
