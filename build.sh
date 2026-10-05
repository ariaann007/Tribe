#!/usr/bin/env bash
# Render build step.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input
python manage.py create_initial_admin
