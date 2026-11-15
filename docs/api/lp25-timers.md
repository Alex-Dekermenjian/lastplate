# LP-25 timer job

`reservations/timers.py` holds the rules; `python manage.py run_timers` runs
them once (add `--every 60` to keep running once a minute). Compose starts it
in the `timers` container. Each step is safe to run twice, from two processes
at once, or late (the next run catches up):

| Step | Which orders | What happens |
| --- | --- | --- |
| Expire | Reserved (CONFIRMED) with `pick_up_by <= now` | EXPIRED by SYSTEM under the row lock, RELEASE_PENDING, then every line's allocation is released; SYNCED when all are acknowledged |
| Consume | Paid (COLLECTED / CONSUME_PENDING) older than the 10-minute undo window | `collection.claim_consumption` first (permanent undo barrier), then consume each line; SYNCED only if the same claim and version still apply |
| Stuck | PROCESSING / ALLOCATE_PENDING older than 2 minutes | NOT_RESERVED (never shown), then every line is released; release leaves a tombstone, so a late allocate can't hold stock |
| Retry | any RELEASE_PENDING | release again until Marketplace confirms |

A 409/404/bad-key answer marks the order NEEDS_ATTENTION instead of retrying
forever. SHOP_CANCEL_PENDING is not retried yet: it needs the operator's user
ID, which LP-79 does not store on the order.

Expiry also runs at the start of every order read (customer orders, order
page, checkout, Today's orders, pickup lookup, Paid/Undo) and, through
`POST /api/timers/expire` (no sign-in, at most once every 10 seconds per
process), whenever the web shows offers. So expiry works even if the host puts
the background job to sleep; the timer is a backup.

Demo: put `PICKUP_HOLD_MINUTES=2` in `compose.env` and recreate the stack.

Public site (LP-85): run `python manage.py run_timers` once a minute (a cron
job), or a worker with `python manage.py run_timers --every 60`, using the
Reservations service's environment.
