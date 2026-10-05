"""Create the first admin login from environment variables, once.

Used on Render, where there is no interactive shell on the starter plan.
Set DJANGO_ADMIN_USERNAME and DJANGO_ADMIN_PASSWORD in the Render
dashboard. The command does nothing if any user already exists, and the
admin must change the password on first login.
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from hr.models import UserSettings


class Command(BaseCommand):
    help = "Create the first superuser from DJANGO_ADMIN_USERNAME / DJANGO_ADMIN_PASSWORD."

    def handle(self, *args, **options):
        User = get_user_model()
        if User.objects.exists():
            self.stdout.write("Users already exist; skipping initial admin.")
            return
        username = os.environ.get("DJANGO_ADMIN_USERNAME")
        password = os.environ.get("DJANGO_ADMIN_PASSWORD")
        if not (username and password):
            from hr.first_run import setup_code

            host = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or os.environ.get("RENDER_EXTERNAL_HOSTNAME")
            link = f"https://{host}/setup/?code={setup_code()}" if host else f"/setup/?code={setup_code()}"
            self.stdout.write(self.style.WARNING(
                "No users yet. Create the first admin account by opening this one-time link:\n"
                f"  FIRST-RUN SETUP: {link}"
            ))
            return
        user = User.objects.create_superuser(username=username, email="", password=password)
        UserSettings.objects.create(user=user, must_change_password=True)
        self.stdout.write(self.style.SUCCESS(f"Created initial admin '{username}'. Remove DJANGO_ADMIN_PASSWORD from Render now."))
