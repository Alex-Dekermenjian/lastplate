# LP-42 contracts and assumptions

Marketplace `GET /internal/shops/by-operator/{user_id}` accepts only the
Reservations service name/key (not a user JWT). Returns
`{id, branches: [{id, name}]}`; 404 if no shop. Includes removed branches so
existing orders remain accessible. No cross-service database reads.

Reservations `GET /api/shop/reservations` requires a SHOP_OPERATOR access JWT.
Ownership is resolved using its verified user_id, never browser shop IDs.
Optional `branch_id` must belong to that shop (otherwise 404); `q` searches
pickup code or saved customer first name, case insensitively.
Returns `{orders: [...], branches: [...], count: N}`. Orders use the existing
order response plus customer_first_name. Sorted by pick_up_by then id.
Today means pick_up_by within the Asia/Beirut calendar day. Only CONFIRMED
orders are listed, including overdue ones awaiting expiry processing.
Count means today's active (deadline still in the future) CONFIRMED orders,
independent of branch/search. `count_only=1` omits order serialization.
Missing shop returns 404; unusable Marketplace responses return 503, never
an unscoped query. Responses must not be cached.

Team review: the badge is an active Reserved count, not an unread counter.
Customer first name is the first whitespace-delimited word of the verified
customer JWT display_name, saved only when creating an order. Missing names
and old orders display "Customer". No profile lookup or historical backfill.
The additive migration must be deployed before using the new API. Checkout
retry, stock allocation and status transitions are unchanged. LP-24 excluded.
