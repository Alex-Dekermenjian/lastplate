# Local development setup — Windows

LastPlate runs as separate services. Each has its own Django project,
dependencies, settings, port and PostgreSQL database. The architecture and its
open decisions are in [microservices-plan.md](microservices-plan.md).

## What is implemented

| Part | Implemented now | Planned, not built |
| --- | --- | --- |
| Accounts (`services/accounts`, port 8001) | Email-based user model with roles, its migration, Django admin login, health endpoints; LP-7 APIs: shop-operator registration, RS256 token sign-in, refresh, logout (refresh-token blacklist), `/api/users/me/`, throttling; 9 original tests + health and auth API tests | Web sign-in/registration pages and the Pending shop (rest of LP-7) |
| Marketplace (`services/marketplace`, port 8002) | Own database, health endpoints; LP-7: verifies Accounts tokens with the public key only, minimal `Shop` (one per operator, Pending), `POST /api/shops/` (idempotent), `GET /api/shops/me/`; LP-8: shop profile (pickup address, area, public contact) via `PATCH /api/shops/me/`; LP-9: team-admin shop review in Django admin (`/admin/`) | Listings, stock (LP-10 to LP-13) |
| Reservations (`services/reservations`, port 8003) | Runnable empty service, own database, health endpoints | All reservation logic (Sprint 2) |
| Web frontend (`web`, port 8000) | Server-side sessions; home page; LP-56: one login page (`/login/`) and sign-up for customers and restaurants (`/signup/`), customer account page (`/account/`), POST sign-out, workspace with shop status (`/workspace/`), LP-37: three-step restaurant request (`/workspace/apply/`) and its status page (`/workspace/request/`); LP-8: shop profile page (`/workspace/profile/`); LP-10 and LP-11: create, edit and publish offers with photos; LP-12: stock adjustments; LP-13: public offers (`/offers/`) | Customer accounts, reservations (Sprint 2) |
| Docker Compose | PostgreSQL with one database and login role per service; all four apps | Production deployment (LP-15) |
| CI | Checks and tests per service; Compose smoke check | Cross-service feature integration tests |

Shops can register, get approved, publish offers and manage stock, and visitors can
browse them. Reservations, collection and customer accounts are not built yet.

Each service exposes:
- `GET /health/live/` — the process is serving requests.
- `GET /health/ready/` — the service reaches its own database and every
  migration is applied. It returns HTTP 503 otherwise.

## 1. Install the tools

Install Git, Python 3.14 and Docker Desktop. Start Docker Desktop and wait until
it reports that the engine is running. A separately installed PostgreSQL is
optional (see section 8).

Run every command below in PowerShell from the repository folder.

```powershell
git clone https://github.com/Alex-Dekermenjian/lastplate.git
cd lastplate
```

If you already have a clone, update it instead: `git pull`.

## 2. Install Python dependencies

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The root `requirements.txt` installs every service's pinned requirements into
one local environment. Each service's Docker image installs only its own
`requirements.txt`. If you already have a `.venv`, run only the second command.

## 3. Create your private settings

```powershell
.\.venv\Scripts\python.exe scripts\init_dev_env.py
```

This creates random passwords and secret keys in:
- `compose.env` — read by Docker Compose;
- `.env` in each service folder — used when a service runs outside Docker;
- `.keys/jwt-private.pem` and `.keys/jwt-public.pem` — the RS256 key pair
  Accounts uses to sign sign-in tokens. Docker Compose mounts them read-only;
  they are never copied into an image.

It never overwrites an existing file and never prints a secret. All these files
are ignored by Git. Never commit or share them, especially the private key.

**Marketplace crashes with `relation "marketplace_listing" does not exist`?** Your
database is older than the LP-10 merge. Update to the latest code and start the stack
as usual; `migrate` repairs it and keeps your data. See
[migration-integration-notes.md](migration-integration-notes.md).

**Already set up before LP-7?** Run the same command once more (step 2 first, to
install the new packages). It keeps your existing settings and only adds the
missing key pair. Then rebuild Accounts:
`docker compose --env-file compose.env up -d --build accounts`. Accounts will
not start without its keys.

## 4. Run everything with Docker Compose

```powershell
docker compose --env-file compose.env up -d --build --wait
docker compose --env-file compose.env ps
```

Always pass `--env-file compose.env`. It supplies the generated settings and
stops Compose from reading the repository's private root `.env`.

On first start PostgreSQL creates four databases (`lastplate_accounts`,
`lastplate_marketplace`, `lastplate_reservations`, `lastplate_web`), each owned
by a login role of the same name. Each role can connect only to its own
database. Each service applies its own migrations when it starts.

Open:
- http://127.0.0.1:8000/ — web status page; all three services should show `ready`
- http://127.0.0.1:8001/health/ready/
- http://127.0.0.1:8002/health/ready/
- http://127.0.0.1:8003/health/ready/
- http://127.0.0.1:8001/admin/ — Accounts admin

Create your own Accounts administrator:

```powershell
docker compose --env-file compose.env exec accounts python manage.py createsuperuser
```

View logs or stop the stack:

```powershell
docker compose --env-file compose.env logs -f accounts
docker compose --env-file compose.env down
```

`down` keeps your data in the `lastplate-dev_pgdata` volume and uploaded photos in
`lastplate-dev_marketplace_media`. **Do not add `-v` / `--volumes`**: it deletes all
four databases and every photo. The init script runs only
when that volume is first created; changing `compose.env` passwords later
does not change existing database roles.

## 5. Run the smoke check

```powershell
.\.venv\Scripts\python.exe scripts\smoke_check.py
```

It builds the images and then checks three things:

- Each service started alone, with only PostgreSQL running, is live, ready and
  connected to its own database.
- With all four running together, they use four distinct databases and the web
  status page sees all three services ready.
- Every database role is refused by the other three databases.

The stack is left running.

## Review a restaurant in /console (LP-57)

The LastPlate administrator reviews restaurants on the website itself, at
http://127.0.0.1:8000/console/. Admin accounts can only be created with a command;
public sign-up never creates one. Create yours once (it asks for the password twice):

```powershell
docker compose --env-file compose.env exec accounts python manage.py create_admin --email admin@example.test --name "LastPlate Admin"
```

1. Log in at http://127.0.0.1:8000/login/ with that account. You land on **/console**,
   which counts pending and approved restaurants and live offers.
2. Open **Restaurants**. The **Pending**, **Approved** and **Rejected** tabs list each
   restaurant; open one to see its full profile.
3. To approve, tick **I confirmed the applicant represents this restaurant** and press
   **Approve restaurant**. The profile must be complete.
4. To reject, write a reason (the owner sees it) and press **Reject restaurant**.

A reviewed restaurant cannot go back to Pending. The console and the Django admin
below use the same rules (`marketplace/shop_review.py`).

## Review a shop in the Django admin (LP-9, developer backup)

Since LP-57 the Django admin is only a developer backup for /console.
Shops are approved or rejected in Marketplace's own Django admin. It has its own
team-admin accounts, separate from Accounts and from shop operators. Create one once:

```powershell
docker compose --env-file compose.env exec marketplace python manage.py createsuperuser
```

To give another team member review rights without full control, sign in at
http://127.0.0.1:8002/admin/, add a user under **Users**, tick **Staff status**, and
add the permission **marketplace | shop | Can approve or reject shops**.

**Pending → Approved:**
1. Sign up at http://127.0.0.1:8000/signup/, choose **I own a restaurant**, then give the
   restaurant's name. The workspace shows **Pending review**.
2. Complete the profile at http://127.0.0.1:8000/workspace/profile/.
3. At http://127.0.0.1:8002/admin/marketplace/shop/, open the shop, choose **Approved**,
   tick **I confirmed the applicant represents this shop**, and save.
4. Refresh the workspace. It shows **Approved**.

**Pending → Rejected:**
1. Register a second shop in a private window (a different browser session).
2. In the admin, open it, choose **Rejected**, write a reason, and save.
3. Refresh that workspace. It shows **Not approved** and your reason.

The admin refuses approval when the profile is incomplete or the confirmation box is
unticked, and refuses rejection without a reason. Rules:
[database-design.md](database-design.md).

## 6. Run one service outside Docker

Useful for debugging. Stop that service's container, keep PostgreSQL in
Docker, and run the service with your virtual environment:

```powershell
docker compose --env-file compose.env up -d --wait postgres
docker compose --env-file compose.env stop accounts
cd services\accounts
..\..\.venv\Scripts\python.exe manage.py migrate
..\..\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8001 --noreload
```

The service reads `services\accounts\.env`, which points to the Compose
PostgreSQL on `127.0.0.1:55432`. Use ports 8002 and 8003 for Marketplace and
Reservations, and from the `web` folder use `..\.venv\Scripts\python.exe` and
port 8000.

## 7. Run automated checks

With the Compose PostgreSQL running:

```powershell
$py = (Resolve-Path .\.venv\Scripts\python.exe).Path
foreach ($dir in "services\accounts", "services\marketplace", "services\reservations", "web") {
    Push-Location $dir
    Write-Host "== $dir"
    & $py manage.py check
    & $py manage.py makemigrations --check --dry-run
    & $py manage.py test --noinput --verbosity 2
    Pop-Location
}
```

Django creates and then deletes a separate `test_` database per service,
using that service's own role. Your development data is not touched. Web unit tests
cannot reach running services, so they give the same result whether or not the stack
is up, and Marketplace tests save uploads to a temporary folder.

### Integration check against the running stack

This is separate from the unit tests. It drives the running site over HTTP, from
registration to public browsing, and adds clearly labelled fictional shops and offers
to your development data:

```powershell
.\.venv\Scripts\python.exe scripts\sprint1_flow_check.py
```

The Accounts suite still contains the nine original tests (password hashing,
email normalisation, case-insensitive uniqueness, administrator flags and
admin login/logout/access restrictions) plus health endpoint tests. The web
suite checks that tokens are stored only server-side.

## 8. Using an installed PostgreSQL instead of Docker (optional)

Create a separate role and database per service. Do not reuse the old
`lastplate` database. In psql as the postgres administrator:

```sql
CREATE ROLE lastplate_accounts WITH LOGIN CREATEDB;
\password lastplate_accounts
CREATE DATABASE lastplate_accounts OWNER lastplate_accounts ENCODING 'UTF8' TEMPLATE template0;
REVOKE ALL ON DATABASE lastplate_accounts FROM PUBLIC;
```

Repeat for `lastplate_marketplace`, `lastplate_reservations` and
`lastplate_web`. In each service's `.env`, set `DB_PORT=5432` and the password
you chose for that role.

## Your previous single-project setup

The monolith's files moved with `git mv`, so their history is preserved
(`git log --follow services/accounts/accounts/models.py`). The last
single-project commit on main is `efc5f0e`; the team may mark it with
`git tag pre-microservices efc5f0e` and `git push origin pre-microservices`.

- The root `.env` and your old `lastplate` database are **no longer used**
  and were not changed or deleted. Keep them until the team agrees they are
  not needed; delete them yourself afterwards.
- Old development data (for example your admin account) is not copied.
  Create a new administrator in the Accounts service (section 4).
- Compose uses port 55432, so an installed PostgreSQL on 5432 keeps running
  unaffected.

## Pull request checks and review

GitHub Actions (`.github/workflows/django.yml`) runs on pull requests to main
and pushes to main:

- **One job per service** against its own disposable PostgreSQL 17: pip check,
  Django checks, the migration check, collectstatic where used, migrate and the
  tests. The Accounts job also runs the nine original tests by name.
- **Docker Compose smoke check**, after the service jobs pass: section 5's
  script.

CI database credentials and keys are disposable CI-only fixtures.

Before merging:
1. Confirm that all service jobs and the smoke check are green.
2. Review the diff and relevant Jira completion checks. Prefer a teammate review;
   an AI-assisted review must be labelled as such and is not a teammate's approval.
3. Resolve review feedback and check that no private configuration is included.
4. Attach actual validation evidence to the Jira card.
5. Merge the reviewed change. Mark the Jira card Done only when all its
   acceptance criteria are complete.

Automatic branch-protection enforcement has not been verified. A green check
covers the implemented tests; it is not a guarantee that every feature is
correct.

## Asset and storage configuration

Web templates belong in `web/templates/` and shared CSS/JS/images in
`web/static/`. Accounts and Marketplace collect only Django admin assets. `collectstatic`
output (`staticfiles/`) and local uploads (`media/`) are ignored by Git. Photo
storage will belong to Marketplace; see [hosting-plan.md](hosting-plan.md),
which still describes the earlier single-service target.
