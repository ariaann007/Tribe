# Denizns Tribe: internal HR system

Phase 1 of the Denizns internal HR system. It covers UK and India staff. India staff get full HR and payroll. UK staff get records, an annual salary in GBP with dated history, and a cost view; UK payroll stays with the accountant.

| Who | What they can do |
|---|---|
| **Admin** | Everything: staff records, salary history, payroll, approving and publishing payslips, the cost dashboard, the audit log |
| **Team lead** | Approve leave for their own team and see the team list. **No salary figures.** |
| **Employee** | See their own details, leave balance and published payslips, and request leave. Nothing about anyone else. |

Phase 2 (not built yet): performance tracking.

## Running it on this computer

Open a terminal in this folder.

```bash
.venv/Scripts/python manage.py runserver
```

Then go to http://127.0.0.1:8000.

The database currently holds **fictional demo data**. The demo logins are `admin`, `teamlead` and `staff`, and their passwords are in `hr/management/commands/seed_demo.py`. Each one has to set a new password on first login.

To start again with fresh demo data, delete `db.sqlite3`, then run:

```bash
.venv/Scripts/python manage.py migrate
```

```bash
.venv/Scripts/python manage.py seed_demo
```

To run the tests:

```bash
.venv/Scripts/python manage.py test hr
```

## Setting up with real staff

1. Delete `db.sqlite3` and run `migrate`. Don't run `seed_demo`.
2. Create your own admin login with `.venv/Scripts/python manage.py createsuperuser`.
3. Log in and go to **Staff → Departments**. Add each department for both offices.
4. Add each employee under **Staff → Add employee**. For India staff, set:
   - their Basic salary
   - whether they're enrolled in PF and ESIC
   - UAN, PAN and ESIC insurance number
   - their **paid leave balance at go-live**
5. Set each India employee's **team lead**, and give the team lead the *Team lead* role.
6. On each employee's page, use **Create login** to set a temporary password, and give it to them privately. They must change it the first time they log in.

## Running payroll each month

1. **Payroll → Start a new run**, then pick the month the period ends in. For example, *September* means 26 Aug – 25 Sep.
2. The system creates a draft payslip for every India employee, using approved leave and current salary.
3. For each person, open the payslip and confirm:
   - how many leave days are paid (it suggests as many as their balance allows)
   - bonus, incentives, overtime and other additions
   - advance recovery (suggested automatically) and other deductions
4. Check the calculation, then **Approve**.
5. On the run page, **Publish approved**. Only then can employees see their payslips. Published payslips are locked.
6. If a published payslip is wrong, use **Issue correction**. This creates version 2, and the original stays on record.

If leave or salary changes after a payslip is approved, publishing is blocked for that person until you unapprove it and review it.

## Payroll rules (in `hr/payroll.py`)

These were agreed in discussion and checked against the August 2026 salary sheet and the September 2026 payslip.

| Rule | Value |
|---|---|
| Pay period | 26th to 25th |
| Salary | Basic only |
| Working days | 23 every month |
| Paid leave | 1.5 days per month. Unused leave carries over with no cap. |
| Unpaid leave (LOP) | Basic ÷ 23 × (leave taken − paid leave applied) |
| PF | 12% of Basic after unpaid leave, wage capped at ₹15,000 (max ₹1,800). Employer pays the same. Only for staff marked *enrolled*. |
| ESIC | 0.75% employee and 3.25% employer, on Basic after unpaid leave. Applies to enrolled staff whose Basic was ₹21,000 or less at the start of the ESIC period (Apr–Sep, Oct–Mar). **Rounded up to the next rupee.** To keep paise, set `ESIC_ROUND_UP = False`. |
| Bonus, incentives, overtime | Paid in full and not included in the PF/ESIC wage (as in the existing sheet) |
| Professional Tax, TDS | Not deducted. The dashboard warns when someone's annual salary nears ₹12 lakh. |
| Mid-period joiners and leavers | "Days not employed" is pre-filled and charged like unpaid days |

### Assumptions to confirm with your accountant

- **ESIC during probation:** the system warns about anyone earning ₹21,000 or less who isn't enrolled. The law normally requires ESIC from day one.
- **Professional Tax in Kerala:** the Calicut corporation normally levies it. Not built in yet.
- **Overtime and incentives in the ESIC wage:** ESIC rules usually count them. The system follows the old sheet and leaves them out.
- **Leave accrual for new joiners:** they get the full 1.5 days in their first month.

## Data protection

- Passwords are hashed. Sessions end after 8 hours or when the browser closes.
- Each person sees only their own data. Team leads see no pay figures.
- Only the last 4 digits of Aadhaar are stored.
- UK salaries (annual, GBP) are visible to admins only. Team leads and employees never see them.
- Every change to staff records, payroll and leave is written to the audit log.

## Going live on Render

The repo includes a Render Blueprint (`render.yaml`). It creates:

| Resource | Details | Cost |
|---|---|---|
| `denizns-tribe` web service | Free plan, Frankfurt region | Free |
| `tribe-db` PostgreSQL 16 | Free plan, Frankfurt region | Free |

Frankfurt is in the EU, which the UK treats as adequate for personal data.

**Free plan limits. Read these before adding real staff:**

- **The free database is deleted 30 days after it's created**, together with all its data, unless you upgrade it. Render emails you before this happens. Upgrade before then: in Render, open **tribe-db → Settings**, choose Basic 256 MB (about $6/month), and the data is kept.
- **The free database has no backups.**
- **The free web service sleeps after 15 minutes without visitors.** The next visit takes about a minute to load.

Use the free plan to try the system out. Upgrade the database before you run real payroll on it.

### First deployment

1. In Render, choose **New → Blueprint**, connect GitHub, and pick the **Tribe** repo.
2. Render asks for two values. Enter them yourself:
   - `DJANGO_ADMIN_USERNAME`: your login name
   - `DJANGO_ADMIN_PASSWORD`: a temporary password you'll change on first login
3. Click **Apply**. The first build takes a few minutes.
4. Open the `https://denizns-tribe.onrender.com` link and log in. You'll be asked to set a new password.
5. In Render, go to **denizns-tribe → Environment** and **delete `DJANGO_ADMIN_PASSWORD`**. It's only used once.
6. Follow "Setting up with real staff" above, starting from step 3.

Every push to `main` on GitHub deploys automatically.

### Still to do

- **Data crossing borders:** India staff data is stored in the EU, not India. Under the DPDP Act and UK GDPR, tell staff where their data is held. If anyone in India will handle UK staff data, put an IDTA in place.
- **Custom domain (optional):** for example `tribe.denizns.co.uk`. Add it in Render under **Settings → Custom Domains**, then set `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` to match.

## Project layout

```
config/           Django settings and URLs
hr/payroll.py     Payroll rules (pure functions, fully tested)
hr/services.py    Payroll runs, leave balances, warnings, dashboard figures
hr/models.py      Data model: employees, dated salary/role history, leave, advances, payslips, audit log
hr/views.py       Screens
hr/templates/     Pages, including the payslip layout
hr/tests/         Tests (payroll maths, permissions, full monthly flow)
```
