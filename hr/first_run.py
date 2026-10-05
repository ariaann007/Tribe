"""First-run setup: create the first admin from the browser when no users exist.

The page needs a code derived from SECRET_KEY, which only appears in the
private build log, so a stranger who finds the site first can't claim it.
"""

import hashlib
import hmac

from django.conf import settings
from django.contrib.auth import get_user_model


def setup_code():
    return hmac.new(settings.SECRET_KEY.encode(), b"first-run-setup", hashlib.sha256).hexdigest()[:24]


def setup_needed():
    return not get_user_model().objects.exists()


def code_is_valid(code):
    return bool(code) and hmac.compare_digest(str(code), setup_code())
