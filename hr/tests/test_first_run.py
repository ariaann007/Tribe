from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from hr.first_run import setup_code

PASSWORD = "a-long-test-password-123"


class FirstRunSetup(TestCase):
    def post(self, code):
        return self.client.post(reverse("first_run_setup"), {
            "code": code, "username": "ann", "password1": PASSWORD, "password2": PASSWORD,
        })

    def test_wrong_or_missing_code_is_404(self):
        self.assertEqual(self.client.get(reverse("first_run_setup")).status_code, 404)
        self.assertEqual(self.post("nope").status_code, 404)
        self.assertFalse(get_user_model().objects.exists())

    def test_correct_code_creates_logged_in_superuser(self):
        page = self.client.get(reverse("first_run_setup") + f"?code={setup_code()}")
        self.assertContains(page, "Create your admin account")
        resp = self.post(setup_code())
        self.assertRedirects(resp, reverse("dashboard"))
        user = get_user_model().objects.get()
        self.assertTrue(user.is_superuser)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def test_closed_once_any_user_exists(self):
        get_user_model().objects.create_user("someone", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("first_run_setup") + f"?code={setup_code()}").status_code, 404)
        self.assertEqual(self.post(setup_code()).status_code, 404)
        self.assertEqual(get_user_model().objects.count(), 1)
