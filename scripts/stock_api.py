"""Try Marketplace's internal stock API (LP-20) by hand, as Reservations would.

Development only. Reads the Reservations key from compose.env and sends it in
the X-Service-Key header; the key itself is never printed. Uses only the
Python standard library. Run from the repository root with the stack running.

    python scripts/stock_api.py offer 12              how many customers see
    python scripts/stock_api.py reserve 12 --quantity 1
    python scripts/stock_api.py release <ref> --listing 12
    python scripts/stock_api.py consume <ref> --listing 12      (mark sold)
    python scripts/stock_api.py shop-cancel <ref> --listing 12 --actor 5 --reason "Ran out"
    python scripts/stock_api.py show <ref>
    python scripts/stock_api.py reserve 12 --no-key      a call without the key is refused
    python scripts/stock_api.py reserve 12 --wrong-key   so is a wrong key
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE = "http://127.0.0.1:8002"
KEY_NAME = "RESERVATIONS_TO_MARKETPLACE_KEY"


def service_key():
    path = ROOT / "compose.env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{KEY_NAME}="):
                value = line.split("=", 1)[1].strip().strip("'\"")
                if value:
                    return value
    sys.exit(f"{KEY_NAME} is missing from compose.env. Run scripts/init_dev_env.py first.")


def call(method, path, body=None, key_mode="right"):
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key_mode != "none":
        headers["X-Service-Name"] = "reservations"
        headers["X-Service-Key"] = service_key() if key_mode == "right" else "wrong-" + "x" * 40
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(MARKETPLACE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")
    except urllib.error.URLError as error:
        sys.exit(f"Cannot reach Marketplace at {MARKETPLACE} ({error.reason}). Is the stack running?")


def public_offer(listing_id):
    status, body = call("GET", f"/api/listings/{listing_id}", key_mode="none")
    return body if status == 200 else None


def say_customers_see(listing_id):
    offer = public_offer(listing_id)
    if offer is None:
        print(f"Customers see: offer {listing_id} is not listed (ended, sold out or not published).")
    else:
        print(f"Customers see: {offer['free_quantity']} left of \"{offer['title']}\".")


def show(status, body):
    print(f"HTTP {status}")
    if isinstance(body, dict):
        allocation = body.get("allocation", body)
        if "code" in body:
            print(f"  code:   {body['code']} - {body.get('detail', '')}")
        if "status" in allocation:
            print(f"  ref:    {allocation['reservation_ref']}")
            print(f"  status: {allocation['status']}  quantity: {allocation['quantity']}"
                  + (f"  changed: {body['changed']}" if "changed" in body else ""))
            if allocation.get("reject_reason"):
                print(f"  reason: {allocation['reject_reason']}")


def main():
    parser = argparse.ArgumentParser(description="Try the LP-20 internal stock API.")
    sub = parser.add_subparsers(dest="command", required=True)

    offer = sub.add_parser("offer", help="what customers see for an offer")
    offer.add_argument("listing", type=int)

    reserve = sub.add_parser("reserve", help="hold units (prints the new ref)")
    reserve.add_argument("listing", type=int)
    reserve.add_argument("--quantity", type=int, default=1)
    reserve.add_argument("--ref", help="send this ref again to see a repeated call change nothing")
    keys = reserve.add_mutually_exclusive_group()
    keys.add_argument("--no-key", action="store_true")
    keys.add_argument("--wrong-key", action="store_true")

    for name in ("release", "consume", "shop-cancel"):
        action = sub.add_parser(name)
        action.add_argument("ref")
        action.add_argument("--listing", type=int, required=True)
        if name == "shop-cancel":
            action.add_argument("--actor", type=int, required=True, help="the restaurant owner's user ID")
            action.add_argument("--reason", required=True)

    show_parser = sub.add_parser("show", help="the stored answer for a ref")
    show_parser.add_argument("ref")

    args = parser.parse_args()

    if args.command == "offer":
        say_customers_see(args.listing)
        return

    if args.command == "show":
        show(*call("GET", f"/internal/allocations/{args.ref}"))
        return

    if args.command == "reserve":
        offer_data = public_offer(args.listing)
        revision = offer_data["terms_revision"] if offer_data else 1
        key_mode = "none" if args.no_key else "wrong" if args.wrong_key else "right"
        body = {"reservation_ref": args.ref or str(uuid.uuid4()), "listing_id": args.listing,
                "quantity": args.quantity, "expected_terms_revision": revision}
        show(*call("POST", "/internal/allocations", body, key_mode=key_mode))
        say_customers_see(args.listing)
        return

    body = {"listing_id": args.listing}
    if args.command == "shop-cancel":
        body.update(actor_user_id=args.actor, reason=args.reason)
    show(*call("POST", f"/internal/allocations/{args.ref}/{args.command}", body))
    say_customers_see(args.listing)


if __name__ == "__main__":
    main()
