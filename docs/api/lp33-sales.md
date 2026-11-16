# LP-33 sales reporting

GET `/api/shop/sales?from=2026-11-01&to=2026-11-16&branch=7&page=1`
and GET `/api/shop/sales/export` require an operator access JWT. Ownership
comes from Marketplace's service-authenticated operator lookup, including
removed branches. Missing shops/foreign branches return 404; lookup faults
return 503. Invalid dates, reversed ranges and invalid pages return 400.

Dates are inclusive Asia/Beirut calendar dates of `pick_up_by`, not creation
or payment dates. Missing dates default to today. Page size is 50. Summary
totals cover every matching order, independently of pagination. Search and
status tabs do not affect the Today's Orders cards; branch selection does.
The LP-42 badge remains an independent, all-branch active Reserved count.

Only Reserved, Paid, Cancelled and Expired orders appear. A Reserved order
with deadline <= report time is reported as Expired without changing the DB
or invoking stock operations. Paid means current COLLECTED, even before stock
consumption; undo removes it from collected cash and saved items. Expected
cash counts only active Reserved orders. Items saved is the sum of Paid line
quantities (one LastBag is one item), not weight, servings or distinct lines.
All financial amounts are Python Decimal sums of saved quantity * unit_price,
formatted as strings with exactly two decimals in USD. No live offer prices
or payment events are summed. Orders with multiple lines count once.

JSON contains currency, timezone, date_basis, filters, branches, summary,
pagination and orders. Summary fields: orders, paid_orders, reserved_orders,
cancelled_orders, expired_orders, cash_collected, cash_expected, items_saved.
Rows contain reservation_ref, branch_id, branch_name, status, status_label,
pick_up_by, collected_at, quantity, order_total, cash_collected, cash_expected,
items_saved. Export contains all filtered rows and ignores pagination; text
cells are protected against spreadsheet formulas. Neither output includes
customer contact data or credentials. Responses must not be cached.

Example empty response (money is always a string):

```json
{
  "currency": "USD",
  "timezone": "Asia/Beirut",
  "date_basis": "pick_up_by",
  "filters": {"from": "2026-11-16", "to": "2026-11-16", "branch": null},
  "branches": [{"id": 7, "name": "Hamra"}],
  "summary": {
    "orders": 0, "paid_orders": 0, "reserved_orders": 0,
    "cancelled_orders": 0, "expired_orders": 0,
    "cash_collected": "0.00", "cash_expected": "0.00", "items_saved": 0
  },
  "pagination": {"page": 1, "page_size": 50, "total": 0},
  "orders": []
}
```

`GET /api/shop/reservations?include_summary=1` adds the same `summary`
for today's Beirut date and the existing `branch_id` filter. The default
LP-42 response and badge contract are unchanged. Read-only sales endpoints
do not require a CSRF token: they require Bearer JWTs and reject POST.
Session-authenticated Web payment mutations retain Django CSRF protection.

LP-77 limitation: development seed orders have no dedicated demo flag and
may use real shops/branches. They are included like other order records even
though they never held or consumed stock. These reports represent recorded
orders, not a verified cash ledger. Use clean non-demo data for acceptance
or accounting. No data is removed or rewritten by reporting.
