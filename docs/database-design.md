# LastPlate database design

Status: proposed design for review.
Jira task: LP-5.

> **Architecture update (3 November 2026).** The course requires independently
> running services, so the single shared database below is being replaced by one
> database per service. Shop moves to Marketplace, and Marketplace gains an
> Allocation record. Cross-service links become plain IDs, and cross-record stock
> rules run inside Marketplace's own transactions. See
> [microservices-plan.md](microservices-plan.md). Field-level rules in this
> document still apply unless the plan says otherwise.

## 1. Overall structure

Use one PostgreSQL database shared by the whole website.

Django apps:
- accounts: users and shop profiles.
- marketplace: food listings and stock adjustments.
- reservations: reservations and their history.

These are six main project models. Django also creates supporting tables for authentication, permissions, sessions and administration.

Sprint 1 implements users, shops, listings and stock adjustments.
Sprint 2 implements reservations and reservation events.
Sprint 3 completes cancellation flows.

## 2. Main relationships

```mermaid
erDiagram
    USER ||--o| SHOP : operates
    SHOP ||--o{ LISTING : publishes
    USER ||--o{ RESERVATION : makes
    LISTING ||--o{ RESERVATION : receives
    LISTING ||--o{ STOCK_ADJUSTMENT : records
    RESERVATION ||--o{ RESERVATION_EVENT : records
```

Each shop has one operator account in this version.
A customer's account does not have a shop profile.
A reservation belongs to one customer and one listing.
Its shop is found through that listing.

## 3. User

Use a custom Django user model configured before the first migration.

| Field | Purpose |
| --- | --- |
| id | Unique account identifier |
| display_name | Name shown to the user |
| email | Login address; unique ignoring letter case |
| password | Password hash managed by Django |
| role | CUSTOMER or SHOP_OPERATOR |
| is_active | Whether the account can authenticate |
| is_staff / is_superuser | Django administration permissions |
| date_joined / last_login | Framework account timestamps |

Public registration cannot grant administrator permissions.
Administrator is a supporting account role, not a separate persona model.
Passwords are never stored as plain text.

## 4. Shop

| Field | Purpose |
| --- | --- |
| id | Unique shop identifier |
| operator | Unique link to its shop-operator User |
| name | Bakery/shop name |
| address | Shop address |
| area | Area used for browsing/filtering |
| public_contact | Contact method intended for customers |
| approval_status | PENDING, APPROVED or REJECTED |
| rejection_reason | Required when rejected |
| reviewed_by / reviewed_at | Administrator and decision time |
| created_at / updated_at | Record timestamps |

New shops start Pending.
Only an administrator can approve or reject.
Publication requires Approved status and a complete profile.

Approval requires complete details and manual confirmation that the applicant represents the shop. Missing confirmation leaves the application Pending. Confirmed false, duplicate or unrelated applications can be rejected with a reason.

Use labelled fictional shops for demonstrations.

### Shop profile rules (implemented in LP-8)

The operator edits only their own shop through `PATCH /api/shops/me/`. Marketplace finds the shop from the operator ID in the verified access token; no shop ID is ever accepted.

| Field | Rule |
|---|---|
| name | 2–150 characters |
| address (shown as "Pickup address") | 5–255 characters |
| area | 2–100 characters, free text (no agreed list of areas yet) |
| public_contact (shown as "Shop phone number") | A phone number customers can call (see below) |

**Public contact decision:** a phone number. LP-3 research says customers use a phone and pay in cash at pickup.
- **Accepted:** digits, spaces, `-`, `.`, `/`, brackets and one optional leading `+`, totalling **7–15 digits** (15 is the E.164 maximum).
- **Rejected as unusable:** too few digits (for example `666` or `12345`), too many, a single repeated digit (`0000000`), letters, email addresses, a `+` anywhere but the start.
- **Why not an email address:** so the operator's sign-in email can never become public by mistake. The sign-in email is never shown on shop pages or used as a default.
- **Storage:** the number is stored as entered, trimmed.

**Other rules:**
- A field that is sent may not be blank, so completed details cannot be erased by accident.
- `approval_status`, `rejection_reason`, ownership, IDs and timestamps cannot be submitted (400).
- An edit saves only the changed columns and never alters the approval decision. An Approved shop keeps its approval after an edit. The admin list shows `updated_at` beside `reviewed_at`, so a reviewer can see later edits and reject if needed. Automatic re-review is not built; that is a team decision.
- `profile_complete` is true when name, address, area and public contact are all set. It is the "complete profile" condition for publishing.

### Shop review rules (implemented in LP-9)

Shops are reviewed in Marketplace's own Django admin at `/admin/` (port 8002). The Accounts admin cannot do this because it has no access to the Marketplace database.

**Who can review:** a local Marketplace team-admin account that is staff and has the permission **Can approve or reject shops** (`marketplace.review_shop`), or a superuser. Stock `change_shop` permission alone is not enough. Shop operators live in Accounts, have no account in this admin, and cannot sign in to it. `view_shop` gives read-only access.

| Decision | Requires | Effect |
|---|---|---|
| Approve | Complete profile (`profile_complete`), and the reviewer ticks "I confirmed the applicant represents this shop" | Status Approved; any earlier reason cleared |
| Reject | A reason, which the owner sees | Status Rejected; reason stored |
| Back to Pending | Not allowed once reviewed | Approve or reject instead |

- Each decision records `reviewed_by` and `reviewed_at`. Django's admin also keeps a change history.
- A reviewer account cannot be deleted while a shop refers to it (deactivate it instead). Its identity is not exposed to shop owners.
- Only the decision is editable in the admin. Shop details, ownership and the review record are read-only; shops cannot be added or deleted there.
- The operator API rejects `approval_status`, `rejection_reason`, `reviewed_by` and `reviewed_at` with 400.
- The identity confirmation is a required step at approval and is not stored separately; `reviewed_by` and `reviewed_at` record who approved and when.

### LP-10 integration checks (offers are not on main yet)

**The approval check for publishing:**

```python
with transaction.atomic():  # select_for_update() raises an error outside a transaction
    shop = Shop.objects.select_for_update().get(operator_user_id=request.user.id)
    can_publish = shop.approval_status == Shop.ApprovalStatus.APPROVED and shop.profile_complete
    # Publish here, inside the same transaction, so a rejection cannot slip in between.
```

Evaluate it on the server at the moment of publishing, from the current `Shop` row. Do not cache it in a session or token, because a team admin can reject a shop at any time. `feature/lp10-lp11` currently compares `shop.operator_id`; on `main` the field is `operator_user_id`.

**Open decision for LP-10:** what happens to offers already published when an Approved shop is later rejected. LP-9 does not affect them because no offers exist yet.

When listings are merged, verify:
1. Publishing is refused unless the check above passes. Pending, Rejected and incomplete shops can only save drafts.
2. A listing belongs to the operator's own shop, found from the token, never a submitted `shop_id`.
3. A new listing's pickup address starts as a copy of the shop's `address`, which the operator confirms.
4. Later shop profile edits do not change existing listings' pickup addresses.
5. Later shop profile edits do not change reservations' agreed terms (Sprint 2).
6. Public offer pages show the shop's name, area and phone number, never the operator's sign-in email.
7. Area filtering uses the shop's `area`.

## 5. Listing

| Field | Purpose |
| --- | --- |
| id | Unique offer identifier |
| shop | Link to the owning Shop |
| title / description | Food name and contents |
| category | Food category |
| original_price / discounted_price | Decimal USD unit prices |
| total_quantity | Current stock allocated to the offer |
| pickup_address | Address copied from the shop and confirmed |
| pickup_start / pickup_end | Timezone-aware pickup window |
| photo | Optional image storage path |
| ingredients / allergens | Optional shop-supplied information |
| status | DRAFT, PUBLISHED or WITHDRAWN |
| terms_revision | Changes when agreed offer terms change |
| first_reserved_at | Permanent marker locking offer terms |
| created_at / updated_at | Record timestamps |

Publication rules:
- Required food and pickup details must be complete.
- Prices must be positive; discounted price must be lower.
- Publication quantity must be a positive whole number.
- Pickup start must be before pickup end.
- Pickup end must be in the future.
- Photos must be valid JPEG/PNG files, at most 5 MB.
- Missing ingredient/allergen information displays Unknown.

Available quantity is calculated; it is not an independent counter.
Sold-out and pickup-ended conditions are calculated, not additional stored statuses.

After the first reservation, lock contents, prices and pickup terms permanently. Stock adjustments and withdrawal follow their own rules. Stock-only changes do not change terms_revision.

## 6. StockAdjustment

| Field | Purpose |
| --- | --- |
| id | Unique adjustment identifier |
| listing | Affected Listing |
| quantity_change | Positive addition or negative removal |
| reason | Required explanation |
| kind | MANUAL or SHOP_CANCELLATION |
| reservation | Optional link for a shop cancellation |
| actor | User making the adjustment |
| created_at | Time of the change |

Record changes to allocated total stock and save them with the actual stock update in one transaction.

Reservations and collection do not change total_quantity: their effects come from reservation status.

A shop cancellation of unavailable food changes total_quantity and records one linked adjustment. Repeating it must not create another adjustment.

## 7. Reservation

| Field | Purpose |
| --- | --- |
| id | Internal identifier |
| reference | Unique pickup reference |
| customer | Link to the customer User |
| listing | Link to the Listing |
| quantity | Positive whole-number quantity |
| agreed_title | Saved food title |
| agreed_unit_price / agreed_total | Saved decimal USD prices |
| agreed_address | Saved pickup address |
| agreed_pickup_start / agreed_pickup_end | Saved pickup window |
| agreed_terms_revision | Accepted listing revision |
| request_key / request_details | Safe retry key and submitted details |
| status | CONFIRMED, COLLECTED, CANCELLED or EXPIRED |
| created_at | Reservation time |
| collected_at / collected_by | Collection time and operator |

The server calculates the total. The customer cannot submit an authoritative price.

Enforce a unique customer/request_key pair in the database.
Matching retries return the existing reservation, including its current final status. Reusing the key with different details is rejected.

Agreed values are snapshots. Later shop/profile changes do not rewrite them.

## 8. ReservationEvent

| Field | Purpose |
| --- | --- |
| id | Unique event identifier |
| reservation | Affected Reservation |
| from_status / to_status | Transition; creation has no previous status |
| actor | User responsible; empty for a system action |
| reason | Required for shop cancellation |
| created_at | Event timestamp |

Create an event with reservation creation and each successful status change.
Save events in the same transaction as the change.
Repeated actions must not create duplicate transition events.

## 9. Stock calculations

T = total_quantity.
R = quantity in Confirmed reservations.
C = quantity in Collected reservations.

Free stock = T - R - C.
Always keep T >= R + C.

Public reservable stock is zero unless the listing is Published, has free units and has not reached pickup end.

| Action | T | R | C | Free |
| --- | ---: | ---: | ---: | ---: |
| Publish 10 units | 10 | 0 | 0 | 10 |
| Reserve 2 | 10 | 2 | 0 | 8 |
| Collect those 2 | 10 | 0 | 2 | 8 |

Separate cancellation examples starting from T=10, R=2, C=0:
- Customer cancels: T=10, R=0, free=10 if the offer remains open.
- Shop cancels unavailable units: T=8, R=0, free=8.

Collection never deducts stock twice.

### Public marketplace rules (implemented in LP-13)

`GET /api/listings` and `GET /api/listings/{id}` need no sign-in and ignore any token sent. An offer is shown only when all of these hold:

- the listing is PUBLISHED (drafts and withdrawn offers are hidden);
- its shop is APPROVED (a shop rejected later hides its offers straight away);
- `pickup_end` is in the future (hidden from the exact end time);
- `stock.free_quantity()` is above 0. It is not recalculated elsewhere, so reserved units will hide an offer once Sprint 2 fills in `committed_quantity()`.

Hidden offers return 404 `{"code": "not_found", "detail": ...}` and are not told apart, so a visitor cannot learn whether a hidden offer is a draft, from a rejected shop, or sold out.

Responses include prices, `free_quantity`, the pickup address and window, the photo path, ingredients/allergens (`Unknown` when missing) and the shop's `name`, `area` and `public_contact`. The detail also includes `terms_revision`. They never include `operator_user_id`, the operator's email, `total_quantity` or the listing status. Shops are loaded in one query per request (two queries in total, however many offers).

The web pages at `/offers/` and `/offers/{id}/` show times in Asia/Beirut and show a friendly message when Marketplace cannot be reached.

## 10. Transactions, permissions and time

Use database constraints for unique references, case-insensitive email uniqueness, request-key uniqueness and valid local field values.

Cross-record stock rules require shared transaction logic. Lock the listing consistently for reservations, stock changes and final transitions; recheck stock, revision, status and server time after locking.

Every protected action checks role and ownership on the server.
A reference alone does not grant access to private reservation details.

- Customer cancellation: strictly before pickup start.
- Collection: pickup start <= now < pickup end.
- Shop cancellation: before pickup end.
- New reservations: before pickup end while the offer is eligible.
- Expiry: remaining Confirmed reservations become Expired within one minute after pickup end.
- Time checks block late actions even if the expiry job is delayed.

Store timezone-aware timestamps and display Asia/Beirut times.
Final states cannot be overwritten by competing actions.

Withdrawal closes availability and resolves active reservations atomically. Preserve collected records and history. Until this flow is implemented in Sprint 3, deny withdrawal with active reservations.

## 11. Retention and implementation checks

Protect referenced shops, listings and users from hard deletion that would erase history. Withdraw offers instead of deleting their reservation records.

Before completing LP-5, review:
- Relationship and field definitions against the specification.
- User-model choice before initial migrations.
- Stock and cancellation examples.
- Saved reservation terms and safe retries.
- Ownership and permanent first-reservation locking.

Implementation tests and migrations are completed in their respective coding tasks. This design document does not claim those tests have passed.

## References

- Django custom-user guidance:
  https://docs.djangoproject.com/en/5.2/topics/auth/customizing/
- Django transaction row-locking guidance:
  https://docs.djangoproject.com/en/6.0/ref/models/querysets/#select-for-update
