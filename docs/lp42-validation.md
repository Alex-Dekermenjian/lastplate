# LP-42 implementation and validation

Implemented on LP-42-todays-orders. No commits, pushes, branch changes, or
development database migrations. See [API assumptions](api/lp42-orders.md).

## Changed files

Marketplace (`services/marketplace/marketplace/`):
- `internal_urls.py`, `internal_views.py`: service-authenticated operator lookup.
- `test_lp42_lookup.py`: lookup, isolation, caller authentication, key rotation.

Reservations (`services/reservations/reservations/`):
- `authentication.py`: operator-only permission.
- `marketplace_client.py`: internal ownership lookup using existing service credentials.
- `models.py`, `migrations/0004_order_customer_first_name.py`: optional saved first name.
- `shop_views.py`, `urls.py`: today's scoped, sorted, searchable orders and unfiltered count.
- `views.py`: extract first name from verified customer JWT at checkout.
- `checkout.py`: pass optional name only through new-order creation.
- `orders.py`: save name with the initial order; retries do not overwrite it.
- `test_lp42_orders.py`: permissions, shop/branch isolation, deadlines, date boundaries,
  search, counts, API failure, first-name trust and retry preservation.

Web:
- `web/config/urls.py`: orders page and session-authenticated count route.
- `web/frontend/services.py`: Reservations API client.
- `web/frontend/shop_order_views.py`: thin page and count proxy.
- `web/frontend/templates/frontend/today_orders.html`: owner order list and filters.
- `web/templates/base.html`: shared owner navigation and badge.
- `web/static/css/lastplate.css`: order list/filter/badge presentation.
- `web/static/js/orders-badge.js`: immediate and 20-second session-based polling.
- `web/frontend/test_lp42_orders.py`: rendering, permissions, refresh, escaping and failures.
- `web/frontend/tests_js/orders-badge.test.cjs`: executable polling tests.

Documentation: this file and `docs/api/lp42-orders.md`.

## Actual local results (13 November 2026, after review fixes)

PostgreSQL is accessible at 127.0.0.1:55464. DB_PORT was overridden only in
individual test processes. No environment or shared configuration files changed.
Django separate test databases were preserved using --keepdb.

- Reservations targeted LP-42: **12 passed, 0 failed**.
- Web targeted LP-42: **8 passed, 0 failed**.
- Reservations complete PostgreSQL suite: **123 passed, 0 failed**.
- Web complete PostgreSQL suite: **421 passed, 0 failed**.
- Marketplace targeted PostgreSQL: **2 passed**; complete suite **433 passed**
  during implementation (unchanged since that run).
- Executable JavaScript polling checks: **passed**. Verifies immediate fetch,
  20-second interval, changing/zero counts, service failure/recovery, login
  redirects, and no polling when the owner badge is absent.
- Django checks: no issues in the rerun suites.
- Model/migration checks previously reported no missing migrations in all four projects.
- `git diff --check`: passed.

The earlier PostgreSQL connectivity failures and SQLite-only supplemental
validation are superseded by the PostgreSQL results above for Reservations/Web.

From each service directory, use the workspace virtualenv Python with
`manage.py test --keepdb --noinput`, after setting process-only DB_PORT=55464.
Targeted labels: `reservations.test_lp42_orders`, `frontend.test_lp42_orders`.
JavaScript: `node web/frontend/tests_js/orders-badge.test.cjs`.

## Review findings

Fixed malformed upstream payloads causing owner-page errors: invalid rows,
branches, counts and item data now show a 503 retry state. Added regression tests.
Added an interrupted-checkout name snapshot test: PROCESSING retries preserve
names, produce one order, and allocate stock once. Existing LP-19 tests pass,
including duplicate requests, concurrency, allocation failures and recovery.
No stock/allocation rules, request fingerprints, advisory locks, unique
constraints, order events, or state transitions were changed by LP-42.

Reviewed all tracked changes and untracked candidate files. No environment
files, JWT keys or known local credentials are included; sensitive paths remain
Git-ignored. Customer name is derived from verified JWT claims and HTML-escaped.
Operator identity drives the service-authenticated shop lookup, followed by both
shop and branch query constraints. Customers/admins are excluded. All owner pages
inherit the shared badge. LP-24 actions remain absent.

## Remaining deployment/team follow-up

The new Reservations migration is authored but not applied to the development database.
Deploy it before the new checkout/API code. Review the documented definition of today,
active badge count, and first-word name snapshots (old orders display "Customer").
Historical branch_id=0 orders from before Marketplace branches existed are excluded
by strict branch ownership filtering; migrating those to a real owned branch would
need a separate, explicit data decision. Removed real branches are retained in lookup
results so their existing orders remain visible.
