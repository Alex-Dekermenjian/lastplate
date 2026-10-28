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

Shop features, public pages, automated checks and deployment
remain unfinished. This server is for local development.