# Local development setup — Windows

Install Git, Python 3.14 and PostgreSQL 17.
During PostgreSQL installation, remember your own postgres administrator password.

Clone the shared repository, then open its folder in VS Code:

```powershell
git clone https://github.com/Alex-Dekermenjian/lastplate.git
cd lastplate
code .
```

Run the remaining PowerShell commands from the lastplate folder.
If you already cloned the repository, use that existing folder.

## 1. Install Python dependencies

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. Create your local database

Open PostgreSQL's terminal:

```powershell
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -h 127.0.0.1 -p 5432 -U postgres -d postgres
```

Enter your postgres administrator password.

Inside psql, create the development user:

```sql
CREATE ROLE lastplate_user WITH LOGIN CREATEDB;
```

Set its password when prompted:

```text
\password lastplate_user
```

Then create the database:

```sql
CREATE DATABASE lastplate OWNER lastplate_user ENCODING 'UTF8' TEMPLATE template0;
```

Exit psql:

```text
\q
```

Each developer uses their own local database and passwords.
CREATEDB allows Django to create separate test databases during development.

## 3. Configure private settings

```powershell
Copy-Item .env.example .env
```

In .env, replace the DB_PASSWORD placeholder with the password
you chose for lastplate_user. Keep the surrounding quotes.

For an existing .env created before the explicit debug setting was added,
enable local development mode once:

```powershell
.\.venv\Scripts\python.exe -c "from dotenv import set_key; set_key('.env', 'DJANGO_DEBUG', 'True')"
```

New .env files copied from .env.example already contain this setting.

Generate a private Django secret key:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; from dotenv import set_key; set_key('.env', 'DJANGO_SECRET_KEY', secrets.token_urlsafe(50))"
```

Never commit .env or share its contents.
The repository shares code and migrations, not local accounts or data.

## 4. Create tables and your administrator account

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py check
```

Use your own administrator email, display name and password.

## 5. Start the application

```powershell
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:18080 --noreload
```

Leave the terminal running.
Open http://127.0.0.1:18080/admin/ and sign in.

With --noreload, restart the server after changing Python files.
If port 18080 is unavailable, choose another available localhost port.

## Current checkpoint

Django, PostgreSQL, the custom email-based user model,
initial migrations and administrator login are working.

Shop features, public pages and deployment remain unfinished.
Automated foundation tests and a PostgreSQL CI workflow are now included.
This server is for local development.

## 6. Run automated checks

With PostgreSQL running and your .env configured:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
.\.venv\Scripts\python.exe manage.py test --noinput --verbosity 2
```

Django creates a separate test database. These tests do not use your
local administrator account or erase your normal development data.

The foundation suite checks password hashing, email normalization,
database-enforced case-insensitive uniqueness, administrator flags,
and admin login/logout/access restrictions. It does not yet test the
unimplemented shop, listing or reservation features.

## Pull request checks and review

GitHub Actions runs the Django checks against PostgreSQL 17 and Python
3.14 on pull requests to main and pushes to main. Its database and
credentials are disposable CI-only fixtures, not shared development
or production accounts.

Before merging:
1. Confirm the latest commit's **Django / PostgreSQL 17** check is green.
2. Review the diff and relevant Jira completion checks. Prefer a teammate review;
   for the initial setup, a documented AI-assisted review is also accepted and
   must be labelled as such. Do not claim that AI review is a teammate's approval.
3. Resolve review feedback and check that no private configuration is included.
4. Attach actual validation evidence to the Jira card.
5. Merge the reviewed change. Mark the Jira card Done only when all its
   acceptance criteria are complete.

This is the documented manual review procedure. Automatic branch-protection
enforcement has not been verified: the GitHub integration cannot read
the branch-protection settings. A green check covers the implemented
tests; it is not a guarantee that every application feature is correct.


## Asset and storage configuration

Shared HTML templates belong in templates/; shared CSS/JS/images in static/.
App-specific template/static directories also work. collectstatic builds
staticfiles/, which is generated and not committed. Local user uploads belong in
media/ and are not committed. Development upload serving requires DJANGO_DEBUG=True.

The selected hosting and persistent database/photo-storage architecture, with
implementation and persistence checks for LP-11/LP-15, is recorded in
[hosting-plan.md](hosting-plan.md). Local setup completion does not mean deployment.
