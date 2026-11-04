# LastPlate microservices plan

Status: draft for team review. Prepared 3 November 2026 from `main` (0c38a50)
and `origin/feature/lp10-lp11` (fce4006).

Implemented on branch `architecture/microservices-foundation` (step 1 of §9):
the four runnable projects, their separate databases, health endpoints,
Docker Compose, CI and server-side web sessions. Everything else here is
planned. Setup and the implemented/planned split: [local-setup.md](local-setup.md).

Revised the same day: web token storage (§4), internal endpoint
authentication (§4), further reservation recovery cases (§6), the listing
branch (§8) and LP-4's scope (§9).

## 1. Why and what changes

The course requires independently running domain services that communicate
through APIs. Separate apps inside one Django project do not qualify.

The repository is currently a monolith:

- One Django project (`config/`) with `accounts`, `marketplace` and
  `reservations` in one `INSTALLED_APPS` (`config/settings.py`).
- One PostgreSQL database (`DATABASES["default"]`).
- One URLconf that only routes `admin/` (`config/urls.py`).
- `README.md` and `docs/database-design.md` §1 and §10 explicitly assume one
  shared database and shared transactions.

Implemented today: the custom email-based `User` model with roles, its
migration, nine tests and admin login. Marketplace and reservations are empty
scaffolds on `main`. No API library is installed.

## 2. Target structure

One GitHub repository. Each service is a separate Django + Django REST
Framework project with its own `manage.py`, settings, requirements, migrations,
tests, `.env.example`, database and port.

```
lastplate/
  services/accounts/       :8001   DB lastplate_accounts
  services/marketplace/    :8002   DB lastplate_marketplace
  services/reservations/   :8003   DB lastplate_reservations
  web/                     :8000   no domain database
  tests/integration/       cross-service scenarios
  docs/api/                one contract file per service
```

- Services run as separate processes and are deployed separately.
- A service only reads and writes its own database. It holds no credentials for
  another service's database. Locally, one PostgreSQL server may host the three
  databases, each with its own database user.
- Cross-service references are plain IDs (`PositiveBigIntegerField`), never
  `ForeignKey`s.
- `web/` is a thin server-rendered frontend. It renders templates, keeps the
  user's tokens in its server-side session and calls the service APIs. It
  contains no business rules and no domain tables. Its own small database
  (`lastplate_web`) stores only Django session rows.
- Services call each other synchronously over HTTP with timeouts. There is no
  message broker.

## 3. Responsibilities and data ownership

| Service | Owns | Refers to by ID |
| --- | --- | --- |
| Accounts | User, password hash, role, login, token issuing | none |
| Marketplace | Shop (profile, approval), Listing, StockAdjustment, Allocation | `operator_user_id`, `reservation_ref` |
| Reservations | Reservation, agreed-term snapshot, ReservationEvent | `customer_user_id`, `listing_id`, `shop_id` |

Changes from `docs/database-design.md`:

- **Shop moves from Accounts to Marketplace.** Approval and publication are
  marketplace rules. `Shop.operator` becomes `operator_user_id` (unique).
- **Marketplace gains Allocation**, its record of stock committed to each
  reservation. Marketplace is the only place that decides stock.
- Free stock is calculated inside Marketplace:
  `free = total_quantity - sum(quantity of allocations in HELD or CONSUMED)`.
  This preserves the README rule: free = allocated − Confirmed − Collected.

## 4. Authentication

- Accounts issues JWTs with `djangorestframework-simplejwt`. **Implemented
  (LP-7, Accounts part):**
  - `POST /api/auth/register/` (shop operators only);
  - `POST /api/auth/token/`;
  - `POST /api/auth/token/refresh/`;
  - `POST /api/auth/logout/` (blacklists the refresh token);
  - `GET /api/users/me/`.

  Customer registration is a separate task.
- Registration sets `role = SHOP_OPERATOR` on the server. Any supplied `role`,
  `is_staff`, `is_superuser`, `is_active`, `approval_status` or other field is
  rejected with 400.
- Access token claims: `user_id`, `role`, `is_staff`, `display_name`.
  Access tokens last 15 minutes, refresh tokens 1 day.
- **Signing: RS256 (implemented).** Only Accounts holds the private key and
  signs tokens. Marketplace and Reservations will receive the public key only,
  so they can verify tokens but never create them.
  - Keys are PEM files created by `scripts/init_dev_env.py` in the git-ignored
    `.keys/` folder.
  - Docker Compose mounts them read-only. They are never in Git or an image.
  - Paths are configurable with `JWT_PRIVATE_KEY_FILE` / `JWT_PUBLIC_KEY_FILE`.
- Marketplace and Reservations verify tokens offline with the public key. They
  do not call Accounts per request.
- **Marketplace (implemented, LP-7):** `marketplace/authentication.py` uses
  PyJWT directly.
  - It accepts RS256 only, requires `exp`, `token_type == "access"` and a
    numeric `user_id`.
  - Shop operators have no accounts in Marketplace. Its Django auth tables
    (added by LP-9) hold only local team admins, so token callers never map to
    a local user.
  - Compose mounts only `jwt-public.pem` into Marketplace.
  - Endpoints: `POST /api/shops/` and `GET /api/shops/me/`, shop operators
    only. The owner comes from the token and the status is always PENDING.
    Creation is idempotent: the unique `operator_user_id` makes a retried or
    concurrent request return the existing shop unchanged (200) instead of
    creating or overwriting one (201 on first creation).
- **Throttling (implemented):** standard DRF throttles using the default
  in-process cache.
  - Registration: 10/hour per client.
  - Sign-in: 30/minute per client and 5/minute per email.
  - Once the web frontend calls Accounts for browsers, "client" is the frontend,
    so the per-email limit is what protects individual accounts. Trusting
    `X-Forwarded-For` is safe only if Accounts is not directly reachable.
  - Limits are per process. Several workers or instances each keep their own
    counters.
- **Registration recovery:** after an ambiguous failure (timeout, lost
  response), the web frontend signs in with the same email and password before
  continuing. "Email already exists" alone never proves ownership. Only a
  successful sign-in does.
- **Sign-out limitation:** blacklisting the refresh token stops new access
  tokens, but an access token already issued stays valid until it expires (at
  most 15 minutes). A test documents this. The web frontend must delete its
  server-side copy on sign-out. Instant revocation would need a denylist
  checked on every request, which is deliberately not built.
- Every service still checks role and ownership itself. Example: Marketplace
  checks that the listing's shop has `operator_user_id == token.user_id`.
- **Shop approval (implemented, LP-9)** happens in Marketplace's own Django
  admin, by a local team-admin account with the `marketplace.review_shop`
  permission. There is no approval API, and the token's `is_staff` claim grants
  nothing here. Rules: `docs/database-design.md`.

### Token storage in the web frontend (corrected)

The earlier draft stored tokens in signed-cookie sessions. That was wrong:
signed cookies are tamper-evident but not encrypted, so the browser would hold
readable access and refresh tokens.

- Sessions use Django's database backend (`SESSION_ENGINE =
  "django.contrib.sessions.backends.db"`) in the frontend's own `lastplate_web`
  database. **Implemented.**
- The browser receives only a random session ID in an HttpOnly, SameSite=Lax
  cookie (Secure when `DEBUG` is off). Tokens never reach browser JavaScript
  or cookies. **Implemented and tested** (`web/frontend/tests.py`).
- `web/frontend/tokens.py` stores tokens on sign-in, rotates the session ID
  (prevents session fixation), and on sign-out deletes the server-side
  session row. **Implemented**; sign-in itself is LP-7.
- Planned with LP-7: refresh the access token server-side when it expires,
  and blacklist the refresh token at Accounts on sign-out.

### Internal endpoint authentication (corrected)

The earlier draft said customers "can never reach internal endpoints" because
the frontend lacks a service key. That overstated it: internal endpoints are
served on the same public host and port, so they are protected only by
authentication, not by the network. Planned rules:

- **Caller identity, not just a shared secret.** Each request sends
  `X-Service-Name` (e.g. `reservations`) and `X-Service-Key`. The callee keeps
  one key per allowed caller, generated randomly (at least 32 bytes) and stored
  only in environment variables. A key is never shared between caller/callee
  pairs.
- **Per-endpoint allowlist.** Example: only `reservations` may call
  `/internal/allocations/...`. A valid key for another caller is refused with 403.
- **Constant-time comparison** (`hmac.compare_digest`). Keys are never logged
  or returned in errors.
- **Separate credential types.** Internal endpoints ignore user JWTs, and
  public endpoints ignore service keys. When a call acts for a user, the user ID
  travels in the request body and the callee still checks it against its own
  data.
- **Rotation.** The callee accepts a current and a previous key for each caller
  during a changeover.
- **Transport.** HTTPS between deployed services. If the host offers private
  networking, internal routes are also kept off the public network. This is
  defence in depth and still unverified for Render (LP-15).
- A missing, unknown or wrong key returns 401/403. Reservations must treat that
  as a configuration fault (§6), never as a stock rejection.

Not implemented yet: the first internal endpoint arrives with Sprint 2
allocations. Health endpoints are public and expose no data except, when
`HEALTH_EXPOSE_DETAILS` is on (development and CI only), the connected
database name.

## 5. Minimum API contracts

All bodies are JSON. Errors use `{"code": "...", "detail": "..."}`.
Full request/response examples go in `docs/api/<service>.md` before coding.

### Accounts (Sprint 1)

| Method and path | Caller | Purpose |
| --- | --- | --- |
| `POST /api/auth/register` | web | Create a CUSTOMER or SHOP_OPERATOR; never staff |
| `POST /api/auth/token` | web | Email + password → access and refresh tokens |
| `POST /api/auth/token/refresh` | web | New access token |
| `GET /api/users/me` | web | Current user's profile |

### Marketplace (Sprint 1, except allocations)

| Method and path | Caller | Purpose |
| --- | --- | --- |
| `GET /api/listings` | anyone | Published, reservable listings with `free_quantity` |
| `GET /api/listings/{id}` | anyone | Offer details and `terms_revision` |
| `POST /api/shops`, `GET/PATCH /api/shops/me` | operator | Create (Pending) and edit own shop |
| `POST /api/listings`, `PATCH /api/listings/{id}` | operator | Draft and edit own listings |
| `POST /api/listings/{id}/publish` | operator | Publish; requires Approved, complete shop |
| `POST /api/listings/{id}/stock-adjustments` | operator | Change allocated stock with a reason |
| `GET /api/admin/shops?status=PENDING` | staff | Review queue |
| `POST /api/admin/shops/{id}/approve`, `/reject` | staff | Decision; reject needs a reason |
| `GET /internal/shops/by-operator/{user_id}` | Reservations | Which shop an operator runs |
| `POST /internal/allocations` | Reservations | Allocate stock (Sprint 2, §6) |
| `GET /internal/allocations/{ref}` | Reservations | Read allocation result |
| `POST /internal/allocations/{ref}/release` | Reservations | Customer cancellation or expiry |
| `POST /internal/allocations/{ref}/consume` | Reservations | Collection |
| `POST /internal/allocations/{ref}/shop-cancel` | Reservations | Shop cancels unavailable food |

### Reservations (Sprint 2)

| Method and path | Caller | Purpose |
| --- | --- | --- |
| `POST /api/reservations` | customer | `{listing_id, quantity, request_key, expected_terms_revision}` |
| `GET /api/reservations`, `GET /api/reservations/{ref}` | customer | Own reservations and status |
| `POST /api/reservations/{ref}/cancel` | customer | Before pickup start |
| `GET /api/shop/reservations` | operator | Reservations for own shop |
| `POST /api/shop/reservations/{ref}/collect` | operator | During pickup window |
| `POST /api/shop/reservations/{ref}/cancel` | operator | Unavailable food, with reason |

## 6. Reservation processing and recovery

### Principles

1. **Reservations is the authority on reservation state. Marketplace is the
   authority on stock.** Marketplace never releases an allocation on its own
   initiative.
2. **Every cross-service call is idempotent**, keyed by `reservation_ref` (a
   UUID generated by Reservations and stored before any call).
3. **A timeout is not a failure.** It means the outcome is unknown. Units are
   never released because a confirmation was not observed.
4. **State changes are compare-and-set** on a locked row
   (`select_for_update`), so only one competing transition can win.

### States

Reservations stores two fields. Only `status` is shown to users.

| `status` (user-visible) | Meaning |
| --- | --- |
| PROCESSING | Request accepted; stock outcome not yet known |
| CONFIRMED | Units are held for the customer |
| NOT_RESERVED | Definitively rejected; no units were held |
| CANCELLED | Cancelled by customer or shop (`cancelled_by` records who) |
| COLLECTED | Picked up |
| EXPIRED | Not collected before pickup end |

| `sync_state` (internal only) | Meaning |
| --- | --- |
| ALLOCATE_PENDING | Allocation call not yet answered |
| RELEASE_PENDING | Release not yet acknowledged by Marketplace |
| CONSUME_PENDING | Consume not yet acknowledged |
| SHOP_CANCEL_PENDING | Shop-cancel not yet acknowledged |
| SYNCED | Marketplace agrees with `status` |
| NEEDS_ATTENTION | Retries exhausted or an impossible answer; staff review |

Marketplace `Allocation.status`: HELD, CONSUMED, RELEASED, SHOP_CANCELLED,
REJECTED. HELD and CONSUMED count against free stock. Allocations move only
from HELD; terminal states never change.

### Creating a reservation

1. Reservations looks up `(customer_user_id, request_key)`. A matching retry
   returns the existing reservation. The same key with different details
   returns 409.
2. It commits a new row: `status=PROCESSING`, `sync_state=ALLOCATE_PENDING`,
   new `reservation_ref`. The row exists before Marketplace is contacted.
3. Outside that transaction it calls `POST /internal/allocations` with
   `{reservation_ref, listing_id, quantity, expected_terms_revision}` and a
   short timeout.
4. Marketplace, in one transaction:
   - locks the listing row (`select_for_update`);
   - if an allocation with this `reservation_ref` exists, returns it unchanged;
   - otherwise checks published status, `now < pickup_end`, terms revision and
     `free >= quantity`;
   - stores either a HELD allocation with the agreed-term snapshot, or a
     REJECTED allocation with a reason code. Both are stored so that retries
     always receive the same answer.
5. Reservations applies the answer:

| Answer | Result |
| --- | --- |
| 201/200 HELD | Store the snapshot; CONFIRMED, SYNCED |
| 409 with a stored REJECTED allocation for this ref | NOT_RESERVED, SYNCED; reason shown to customer |
| Timeout, connection error, 5xx | Stay PROCESSING / ALLOCATE_PENDING |
| Any other answer (400, 401, 403, 404, malformed body) | Stay PROCESSING; NEEDS_ATTENTION (configuration or contract fault) |

Only a 409 whose body is the stored REJECTED allocation for this
`reservation_ref` is definitive. Every other non-success answer means the
outcome is unknown.

`reservation_ref` is unique in Marketplace, and concurrent requests for the same
listing are serialised by the listing lock. That prevents both double
allocation and overselling.

### Resolving an unknown outcome

- The frontend shows "Processing" and polls `GET /api/reservations/{ref}`.
- A reconciler (management command run every minute) resends the identical
  allocate request for rows still in ALLOCATE_PENDING, with backoff.
  - If the first request was committed, Marketplace returns that result.
  - If it never arrived, the request is evaluated now and the result is stored.
  - Either way, the outcome is decided once and comes from Marketplace.
- After pickup end, Marketplace rejects a never-received request, so every
  PROCESSING reservation eventually resolves.
- If retries are exhausted, the row becomes NEEDS_ATTENTION and stays
  PROCESSING. Held units stay unavailable until staff resolve it. Nothing is
  released automatically.

### Cancellation and its retries

- Customer cancellation (strictly before pickup start) or the expiry job locks
  the reservation, checks it is CONFIRMED or PROCESSING, and sets `CANCELLED` or
  `EXPIRED` with `sync_state=RELEASE_PENDING` in one transaction. It records a
  ReservationEvent.
- It then calls `POST /internal/allocations/{ref}/release` with `listing_id`.
  The reconciler repeats it until it is acknowledged.
- Marketplace release, under the listing lock:
  - HELD → RELEASED, which frees the units once;
  - already RELEASED → returns success with no further effect;
  - no allocation yet → stores a RELEASED tombstone with quantity 0;
  - CONSUMED or SHOP_CANCELLED → 409, and Reservations marks NEEDS_ATTENTION.
- **Cancellation versus a delayed allocate.** If the original allocate request
  arrives after the tombstone, it finds the existing RELEASED record and returns
  it. It cannot allocate units for a cancelled reservation.
- **Confirmation versus cancellation or cleanup.** Both transitions run on the
  same locked Reservations row and check the current `status`. If cancellation
  wins, a late HELD answer is not applied: it only applies while the row is
  still PROCESSING. The release that follows frees those units. If
  confirmation wins, cancellation proceeds normally from CONFIRMED. No cleanup
  path acts on "confirmation not seen". The reconciler only completes a
  transition that Reservations has already committed.

### Collection and shop cancellation

- Collection (`pickup_start <= now < pickup_end`): set COLLECTED and
  CONSUME_PENDING, then call consume. HELD → CONSUMED.
  - **Consumed stock stays unavailable.** CONSUMED still counts in the free
    stock formula, and release is refused on CONSUMED.
  - Until the consume call succeeds, the allocation is still HELD, which also
    counts, so stock is never deducted twice or returned.
- Shop cancellation (before pickup end): set CANCELLED (by shop) and
  SHOP_CANCEL_PENDING, then call shop-cancel. In one transaction Marketplace:
  - sets HELD → SHOP_CANCELLED;
  - lowers `total_quantity` by the same amount;
  - writes one StockAdjustment (kind SHOP_CANCELLATION) that is unique per
    `reservation_ref`.

  Retries do not repeat the adjustment. Free stock is unchanged, matching
  `docs/database-design.md` §9 (T=8, R=0, free=8).

### Further recovery cases (added)

These cases were unresolved in the first draft.

1. **Late HELD answer after pickup end.** If allocation resolves as HELD
   after `pickup_end`, the reservation goes PROCESSING → EXPIRED, not CONFIRMED,
   and the release follows. The customer never sees a confirmation they could
   not use.
2. **A 404 from `GET /internal/allocations/{ref}` is not a rejection.** The
   original request may still be in flight and commit later. The reconciler
   therefore resends the idempotent POST and never decides from a GET.
3. **A crash between saving PROCESSING and sending the request.** The row is
   already ALLOCATE_PENDING, so the reconciler sends the first request. No
   special case is needed.
4. **The customer retries while PROCESSING.** The same `request_key` returns
   the existing PROCESSING reservation (HTTP 202). It never creates a second
   allocation.
5. **Expiry and shop actions skip PROCESSING rows.** The expiry job handles
   only CONFIRMED rows; case 1 covers late resolution. Shops can collect or
   cancel only CONFIRMED reservations.
6. **Consume and release retries arrive in either order.** Reservations
   allows only one terminal transition per reservation. Marketplace moves only
   from HELD, so a stray release after CONSUMED returns 409 and is flagged
   NEEDS_ATTENTION. Units are never returned.
7. **Release must match the allocation.** Release, consume and shop-cancel
   carry `listing_id`. Marketplace refuses (409) a `reservation_ref` that
   belongs to another listing.
8. **Marketplace finds a HELD allocation that Reservations does not know.**
   This is only possible after data loss, for example a restored backup.
   Marketplace keeps the units held and lists them in a staff report. Releasing
   one needs an explicit staff action that records an actor and a reason. There
   is no automatic expiry of unconfirmed holds.
9. **Resolving NEEDS_ATTENTION.** Staff see the Reservations row next to the
   Marketplace allocation record. They may only apply the outcome Marketplace
   has recorded, or, once Marketplace is reachable again, re-run the reconciler.
   A reservation is never marked CONFIRMED without a HELD allocation, or
   NOT_RESERVED without a stored REJECTED one.
10. **Listing withdrawal with active allocations** (Sprint 3). Marketplace
    cannot release allocations itself. Withdrawal asks Reservations to
    shop-cancel each CONFIRMED reservation and stays blocked while any
    reservation for the listing is PROCESSING.

## 7. Testing

- **Service tests** (each service's `manage.py test`, own test database):
  models, permissions, validation and API views. Calls to other services are
  mocked at the HTTP client.
- **Contract tests:** the JSON examples in `docs/api/` are checked by the
  provider's tests (real responses match) and by the consumer's tests (its
  mocks use the same examples).
- **Integration tests** (`tests/integration/`): start all three services
  against fresh test databases and run scenarios over HTTP:
  - register, approve a shop, publish, browse (Sprint 1);
  - concurrent reservations for the last units: exactly one succeeds;
  - an allocate response lost after commit (test-only fault setting), then
    reconciler retry, ends CONFIRMED with one allocation;
  - cancellation before a delayed allocate arrives: no units held;
  - cancel, collect and shop-cancel retries leave stock correct.
- **Implemented now:** health endpoint tests in every service, server-side
  token storage tests in `web/`, and `scripts/smoke_check.py`. The smoke
  check starts each service alone and then together, checks that each is
  connected to its own database, and checks that every database role is
  refused by the other databases.
- **CI (implemented):** `.github/workflows/django.yml` runs one job per
  service against that service's own PostgreSQL, then the Compose smoke check.
  The cross-service feature scenarios above join the smoke job as the
  features are built. Path filters are not added yet, so every job runs on
  every pull request.

## 8. Preserving existing work

| Existing work | What happened on this branch |
| --- | --- |
| Local `main` was 2 commits behind `origin/main` | Fast-forwarded before branching |
| Monolith on `main` (last commit `efc5f0e`) | Unchanged on `main`; the team may tag it `pre-microservices` |
| `accounts/models.py`, `migrations/0001_initial.py`, `tests.py` (9 tests) | Moved with `git mv` into `services/accounts/accounts/`; contents unchanged; all 9 pass |
| `config/`, `manage.py`, `requirements.txt`, `.env.example` | Moved with `git mv` into `services/accounts/` and adapted; copied as the basis for the other services |
| `marketplace/`, `reservations/` scaffolds | Moved with `git mv` into their services |
| `static/`, `templates/` | Moved into `web/` |
| Root `.env`, the old local `lastplate` database | Not read, changed or deleted; no longer used |
| `origin/feature/lp10-lp11` | Untouched; adapt after this foundation merges (below) |

### Adapting the listing branch (for its author, after the foundation merges)

Commit `fce4006` adds `marketplace/forms.py` and `marketplace/validators.py`
and changes `marketplace/models.py`. `Listing.shop` points at
`"accounts.Shop"`, which exists on no branch, and there is no migration. Steps:

1. Update and rebase, keeping a backup branch:
   ```powershell
   git fetch origin
   git switch feature/lp10-lp11
   git branch backup/lp10-lp11-before-rebase
   git rebase origin/main
   ```
2. Git applies the `models.py` change to
   `services/marketplace/marketplace/models.py` (rename detection). New files
   may stay at the old `marketplace/` path or be reported as a directory-rename
   conflict. In either case, move them and continue:
   ```powershell
   git mv marketplace/forms.py marketplace/validators.py services/marketplace/marketplace/
   git rebase --continue
   ```
   If the rebase becomes confusing, `git rebase --abort` restores the branch.
   As an alternative, start a new branch from `origin/main` and run
   `git cherry-pick fce4006`, resolving the same paths.
3. Remove the cross-service reference. A `Shop` owned by Marketplace comes
   from LP-8. Until then, either base the branch on LP-8's branch and use
   `ForeignKey("marketplace.Shop", ...)`, or temporarily store
   `shop_id = models.PositiveBigIntegerField()` and switch when LP-8 merges.
   Never reference `accounts.*` models from Marketplace.
4. From `services\marketplace`, create and check the migration:
   ```powershell
   ..\..\.venv\Scripts\python.exe manage.py makemigrations marketplace
   ..\..\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
   ..\..\.venv\Scripts\python.exe manage.py test --noinput
   ```
5. The form and validators need no auth or admin apps; Marketplace does not
   install them. Add tests for the validators in the same service.

## 9. Implementation order

Dependencies are noted in brackets.

**Before coding:** team agreement on §10. Contracts go in `docs/api/` first.

1. **Foundation** — **implemented on `architecture/microservices-foundation`,
   awaiting review.** Four runnable projects, separate databases and roles,
   health endpoints, Compose, CI with smoke check, server-side web sessions,
   updated docs. No features.
2. **Accounts auth, LP-7** [1]: register, token, refresh, `/me`, JWT claims and
   tests; web sign-in and sign-out pages using `web/frontend/tokens.py`.
   Others can start with hand-signed test tokens using the agreed claims.
3. **Marketplace shops, LP-8 and LP-9** [1, token format from 2]: Shop model,
   own-profile API and staff approve/reject, with their web pages.
4. **Marketplace listings, LP-10 and LP-11** [3]: adapt the listing branch (§8),
   then add draft, publish and photo/ingredient fields, with their web pages.
5. **Stock adjustments, LP-12** [4]: StockAdjustment and free-stock
   calculation (Allocation table empty for now).
6. **Public browse API and pages, LP-13** [4]
7. **Permissions and validation, LP-14, and integration, LP-15** [all]:
   Sprint 1 integration scenarios and deployment.

**LP-4 (corrected).** LP-4 is "Design the Sprint 1 screens and navigation in
Figma". It produces designs, not code, and can run in parallel from now. Each
feature card implements its own web pages from those designs (steps 2–6). The
earlier draft wrongly listed LP-4 as a web implementation step.

Sprint 2 adds Allocation endpoints and internal service authentication in
Marketplace, then the Reservations service, then the reconciler and recovery
integration tests. Sprint 3 adds cancellation and expiry flows on the same
endpoints.

## 10. Assumptions requiring team agreement

1. The professor accepts three domain services plus a thin web frontend, and
   accepts separate databases hosted on one PostgreSQL server.
2. **Timing.** Sprint 1 ends 5 November. The team decides whether the Sprint 1
   demo runs on the current monolith or waits for the skeleton. Restructuring
   two days before the review is a schedule risk.
3. Shop moves from Accounts to Marketplace (changes LP-5's design).
4. New user-visible statuses PROCESSING and NOT_RESERVED, added to the README's
   Confirmed/Collected/Cancelled/Expired. This relates to the open LP-3
   question on Confirmed versus Pending.
5. A customer may cancel while PROCESSING.
6. Token lifetimes (15 min access, 1 day refresh). Role changes take effect at
   the next token refresh.
7. Reconciler interval (1 minute) and the retry limit before NEEDS_ATTENTION.
8. Expired reservations release units even though the offer is closed; this has
   no customer-visible effect.
9. Hosting: `docs/hosting-plan.md` assumed one Render web service and one
   database. Four web services, a scheduled reconciler and four databases
   (three domain databases plus the web session database) need a new quota and
   cost check before LP-15.
10. Ports (8000–8003, PostgreSQL 55432 in Compose), service key handling and
    the error format listed above.
11. The web frontend's session database (`lastplate_web`) instead of a cache
    such as Redis, chosen so that no extra service is needed.
12. Development roles have CREATEDB so that Django can create test databases;
    deployed roles will not.
