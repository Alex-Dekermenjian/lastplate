# LP-24 collection and LP-25 handoff

Operator JWTs are required. Shop identity comes exclusively from Marketplace's
service-authenticated operator lookup; every branch and order is scoped to it.
`POST /api/shop/reservations/lookup` takes `{branch_id, pickup_code}` and
returns an order summary. Codes are trimmed/uppercased, exactly six characters.
Wrong/foreign-branch codes return 404 "No order with this code here".
Expired codes return 409 with `pick_up_by`; no expiry transition occurs here.

`POST /api/shop/reservations/{ref}/collect` takes
`{branch_id, version, cash_received: true}`. Under an order row lock it checks
ownership, CONFIRMED/SYNCED, pickup start <= now < pick_up_by, then records
COLLECTED, collected_at, CONSUME_PENDING and an event. No consume HTTP call.
`POST /api/shop/reservations/{ref}/undo` takes `{branch_id, version}`.
Undo is allowed only for COLLECTED/CONSUME_PENDING, before collected_at + 10
minutes, and before consumption_started_at is set. It returns CONFIRMED/SYNCED,
clears collected_at, and appends an event. Each mutation increments the version.
Retrying the immediately preceding successful operation is a no-op; stale
requests after another transition return 409. The browser must retain the same
reference/version on ambiguous failure and must not automatically resubmit using
a freshly fetched version. Cash totals always come from saved order lines.

Code lookup is POST to avoid putting pickup codes in query strings. Responses
are never cached. Optional customer_phone is saved at checkout and returned
only while CONFIRMED, never on Paid/Cancelled/Expired orders. Empty legacy
values are supported. Optional phone is contact data, not verified identity.

## Mandatory LP-25 contract

Call `collection.claim_consumption(order_id)` before sending ANY consume call.
It locks the same order row used by Paid/Undo. Only COLLECTED/CONSUME_PENDING
orders with now >= collected_at + 10 minutes are eligible. It durably sets
consumption_started_at before returning. That marker is irreversible even if
HTTP fails or the process crashes; Undo is then forbidden. Existing claims may
be retried. Never hold the transaction open across HTTP. Multiple workers may
retry safely because each Marketplace call is idempotent.

The claim uses a durable atomic transaction: call it outside any enclosing
transaction so the marker is committed before HTTP. Do not bypass this helper.

Consume each line using `checkout.item_ref(order, listing_id)` (UUID5), not
the parent order reference, and send its listing_id. After every line is
acknowledged, lock the order again and mark SYNCED only if the same collection
version and claim still apply. Partial failures retain CONSUME_PENDING and the
claim; retries must finish all lines. Configuration/contract faults require
attention, not release/reallocation. Do not clear the marker on failure.

Expiry operates only on CONFIRMED orders, under the same row lock, and never
releases Paid orders. Undo after pick_up_by restores an overdue Reserved order;
LP-25 then expires it. Undo never extends the pickup deadline. The exact
10-minute boundary belongs to consumption, not Undo. Repeat collection after
an undo starts a fresh window/version.

Pickup codes remain unique across open orders and unclaimed
COLLECTED/CONSUME_PENDING orders. Once claimed they can be reused, because undo
is permanently disabled. Lookup selects Reserved orders; Paid actions always
use immutable reference/version, never the possibly reused code.

Team agreement: this replaces the plan's immediate-consumption and terminal
COLLECTED rules only for the guarded ten-minute undo. Pickup starts are still
enforced, and pick_up_by is the collection cutoff. Owner Paid list is a separate
tab; the LP-42 badge always counts active Reserved orders independently.

The Web orders and pickup-entry screens follow the workspace's existing setup/
approval navigation: missing/incomplete shops go to application, submitted
Pending or Rejected shops go to request status. Approval is not an ownership
credential, and service-key failures are not approval decisions. Reservations
still checks operator identity and branch ownership independently for every API
request. Existing payment receipt actions retain those checks so an interrupted
transaction can be retried without changing its version.
