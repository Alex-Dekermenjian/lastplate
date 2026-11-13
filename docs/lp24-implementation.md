# LP-24 implementation

Implementation branch: LP-24-pickup-payment. No commits, pushes, merges, development
database migrations, database resets, or secret/configuration changes.

## Behavior and team contract

See [collection API and LP-25 contract](api/lp24-collection.md). Owner workflow:
Today's Orders → Enter pickup code → choose owned branch → inspect summary →
confirm cash received → Paid. Paid orders have a separate tab and a guarded Undo.
Customer My Orders already supports COLLECTED/Paid and reflects these transitions.
Optional customer phone is entered in the cart, saved once at checkout, and
returned/displayed only while Reserved. Old orders have no phone.

The LP-25 worker is intentionally not implemented. It must call the durable
`claim_consumption` helper outside an enclosing transaction before stock HTTP,
wait at least ten minutes, consume the existing UUID5 allocation for each line,
and retain the irreversible claim across partial failures and retries. Undo and
claim use the same row lock. HELD stock remains unavailable during the window.

Assumptions for team agreement:
- Collection requires the pickup window to have opened and now < pick_up_by.
- Undo is allowed strictly before collected_at + 10 minutes, even if pick_up_by
  has since passed; it never extends that deadline. LP-25 expires overdue Reserved
  orders later. Exact ten-minute boundary belongs to the worker.
- Phone is optional, unverified checkout contact data; no Accounts change.
- Paid tab uses the same Beirut pickup-deadline date as LP-42; badge counts only
  active Reserved orders. Legacy branch_id=0 orders remain excluded as in LP-42.
- Apply the additive migration before the new code. Its stronger uniqueness
  constraint also covers historical unclaimed COLLECTED/CONSUME_PENDING rows;
  any historical duplicate codes must be reviewed before deployment.

## Files changed

Reservations (`services/reservations/reservations/`):
- `collection.py`: locked, versioned Paid/Undo and durable LP-25 consumption claim.
- `collection_views.py`, `urls.py`: operator-owned lookup/collect/undo APIs.
- `models.py`, `migrations/0005_remove_order_order_unique_open_pickup_code_per_branch_and_more.py`:
  optional phone, payment version, claim timestamp, extended code uniqueness.
- `orders.py`: protect Paid codes until claimed; explicit guarded undo with history.
- `checkout.py`, `views.py`: optional phone snapshot; status-dependent phone responses.
- `shop_views.py`: separate Paid listing, preserving default Reserved and badge behavior.
- `test_lp24_collection.py`: API, snapshot, privacy, idempotency, deadlines, database
  uniqueness, worker timing and PostgreSQL concurrency coverage.

Web:
- `web/frontend/pickup_views.py`, `web/config/urls.py`: owner pickup and payment routes.
- `web/frontend/services.py`: API clients; optional phone checkout argument.
- `web/frontend/cart.py`, `web/frontend/templates/frontend/cart.html`: optional contact input.
- `web/frontend/shop_order_views.py`, `web/frontend/order_views.py`: payment metadata,
  Paid tab and conditional phone display data.
- `web/frontend/templates/frontend/pickup.html`: styled lookup, cash confirmation,
  Paid/Undo and same-version retry after ambiguous failures.
- `web/frontend/templates/frontend/today_orders.html`: Paid tab, entry link and Undo.
- `web/frontend/test_lp24_pickup.py`: UI, permissions, CSRF, privacy, errors and phone forwarding.

Documentation: `docs/microservices-plan.md`, `docs/api/lp24-collection.md`, this file.
Marketplace, Accounts, global CSS, JWT/session storage, allocation rules and stock
mutation endpoints are unchanged. Shared checkout changes add only the optional
phone snapshot; request fingerprints, locks and stock allocation paths remain intact.

## Validation

PostgreSQL at 127.0.0.1:55464, DB_PORT overridden only for test processes.
Django separate test databases preserved with --keepdb:
- Targeted Reservations LP-24 + LP-42: 31 passed, 0 failed (19 LP-24, 12 LP-42).
- Targeted Web LP-24 + LP-42: 23 passed, 0 failed (15 LP-24, 8 LP-42).
- Full Reservations suite after final review: 142 passed, 0 failed.
- Full Web suite after final review: 436 passed, 0 failed.
- LP-42 JavaScript badge polling checks: passed.
- `makemigrations --check --dry-run`: no model changes missing migrations.
- `git diff --check`: passed.
- Candidate-file credential scan: no local credentials, environment files or keys.

Tests include no stock HTTP in Paid/Undo, concurrent duplicate payment, claim/undo
at the ten-minute boundary, stale requests after undo/recollection, cross-shop/branch
denials, actual database uniqueness, expired-code deadlines, and Paid/phone rendering.

## Final review

Read-only checks of the running development stack found a Pending shop and
Reservations with MARKETPLACE_SERVICE_KEY absent. The latter prevents the
internal ownership lookup and explains the original orders-service failure;
approval itself does not produce that failure. All four services' readiness
endpoints were healthy: readiness checks do not validate inter-service keys.
No secret was printed or changed. Configure the existing Reservations caller
credential through the normal secret setup before local end-to-end verification.

Fixed during review:
- Orders/pickup entry now reuse workspace application/request-status navigation
  for unapproved operators rather than showing an unexplained orders error.
- Malformed pickup branch/order/payment responses now show a 503 retry state.
  Ambiguous payment results retain the original reference/version/cash confirmation.
- Generic state-transition timestamps are captured after the row lock is acquired,
  so a lock wait cannot preserve a stale undo-window check.
- Missing service-key diagnostics are logged without values or order data.

Files changed during review: `web/frontend/shop_order_views.py`,
`web/frontend/pickup_views.py`, `web/frontend/test_lp24_pickup.py`,
`web/frontend/test_lp42_orders.py`,
`services/reservations/reservations/marketplace_client.py`,
`services/reservations/reservations/orders.py`,
`services/reservations/reservations/test_lp24_collection.py`,
`docs/api/lp24-collection.md`, and this file.

Migration 0005 was already applied to the development Reservations database at
review time; all three LP-24 columns exist and a read-only preflight found zero
historical code groups conflicting with the strengthened uniqueness constraint.
The review ran no development migration and changed no development records.
Other deployments should perform the same duplicate-code preflight; no automatic
deletion or code rewriting is included. LP-25 worker integration remains pending
and must follow the documented durable-claim/per-line retry contract.

No further code blocker was found. Ready for a GitHub PR with the missing runtime
credential and LP-25 dependency called out; local end-to-end payment verification
remains blocked on that configuration. No PR, commit, push, or merge was created.
