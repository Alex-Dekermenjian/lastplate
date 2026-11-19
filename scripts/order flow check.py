"""LP-85: the order flow, checked through the public link.

Run it on the laptop that runs the Docker Compose stack, while the Cloudflare
quick tunnel is open (docs/demo-tunnel.md). Every page is opened through the
link, like a phone would:

  1. The link shows the site but none of the services' private pages.
  2. A fictional restaurant signs up, the Admin approves it in /console and it
     publishes an offer whose pickup is open now.
  3. A fictional customer reserves it, sees the pickup code and My orders.
  4. The restaurant sees the order, looks up the code and marks it Paid.
  5. A second order is left uncollected: it expires and its food goes back on sale.

Only the setup steps use this laptop directly: create_admin, and reading the
new shop and offer ids from Marketplace. For step 5 the order's pick-up-by time
is moved into the past and the timer job is run once, so the check doesn't
wait for the pick-up hold. Everything it adds is labelled FLOWCHECK and
fictional; nothing is deleted, and its offer is marked sold out at the end so
it leaves the public pages. Prints no passwords or tokens.

    .\\.venv\\Scripts\\python.exe "scripts\\order flow check.py" --url https://<words>.trycloudflare.com
    .\\.venv\\Scripts\\python.exe "scripts\\order flow check.py"     (http://127.0.0.1:8000)
"""

import argparse
import html
import json
import os
import re
import secrets
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from datetime import datetime, timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
ACCOUNTS, MARKET = "http://127.0.0.1:8001", "http://127.0.0.1:8002"
LEBANON = ZoneInfo("Asia/Beirut")
RUN = time.strftime("%H%M%S")
PASSWORD = secrets.token_urlsafe(18) + "-Aa1"
ADMIN_EMAIL = f"flowcheck-admin-{RUN}@example.test"
CODE = re.compile(r'aria-label="Pickup code ([A-Z0-9]{6})"')
failures = []


def check(ok, label, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail != "" and not ok else ""))
    if not ok:
        failures.append(label)
    return ok


def section(title):
    print(f"\n== {title}")


def compose(*args, env_names=(), env=None):
    command = ["docker", "compose", "--env-file", str(ROOT / "compose.env"), "exec", "-T"]
    for name in env_names:
        command += ["-e", name]
    return subprocess.run(command + list(args), cwd=ROOT, capture_output=True, text=True,
                          env={**os.environ, "MSYS_NO_PATHCONV": "1", **(env or {})})


class Browser:
    """One cookie jar, as one phone would have."""

    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def request(self, method, path, body=None, headers=None):
        # Cloudflare refuses Python's default User-Agent, so look like a browser.
        headers = {"User-Agent": "Mozilla/5.0 (LastPlate order flow check)", **(headers or {})}
        request = urllib.request.Request(self.base + path, data=body, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=60) as response:
                return response.status, response.geturl(), html.unescape(response.read().decode(errors="replace"))
        except urllib.error.HTTPError as error:
            return error.code, self.base + path, html.unescape(error.read().decode(errors="replace"))

    def get(self, path):
        return self.request("GET", path)

    def csrf(self, path):
        match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', self.get(path)[2])
        return match.group(1) if match else ""

    def form_headers(self, form_path, content_type):
        # Over https Django's CSRF check needs the Referer a browser sends.
        return {"Content-Type": content_type, "Origin": self.base, "Referer": self.base + form_path}

    def post(self, path, data, form_path=None):
        fields = {"csrfmiddlewaretoken": self.csrf(form_path or path), **data}
        return self.request("POST", path, urllib.parse.urlencode(fields).encode(),
                            self.form_headers(form_path or path, "application/x-www-form-urlencoded"))

    def post_photo(self, path, data, filename, content):
        boundary = uuid.uuid4().hex
        body = b""
        for name, value in {"csrfmiddlewaretoken": self.csrf(path), **data}.items():
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="{filename}"\r\n'
                 "Content-Type: image/png\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
        return self.request("POST", path, body, self.form_headers(path, f"multipart/form-data; boundary={boundary}"))


def local_api(method, url, payload=None, token=None):
    """Setup only: the services' own ports on this laptop, never the link."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, body, headers, method=method), timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as error:
        return error.code, {}


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixels = b"".join(b"\0" + b"\x30\x90\x50" * 32 for _ in range(32))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


def lebanon(moment):
    return moment.astimezone(LEBANON).strftime("%Y-%m-%dT%H:%M")


def pickup_code(page):
    match = CODE.search(page)
    return match.group(1) if match else ""


def status_badge(page):
    match = re.search(r'class="(?:order-page__status|status-badge)[^"]*">(?:<span[^>]*>)?([^<]+)<', page)
    return match.group(1).strip() if match else ""


def left(page):
    match = re.search(r"(\d+) left", page)
    return int(match.group(1)) if match else None


def reserve(customer, listing_id):
    page = customer.get(f"/offers/{listing_id}/")[2]
    match = re.search(r'name="request_key" value="([^"]+)"', page)
    if not match:
        return None, page
    status, url, page = customer.post(f"/offers/{listing_id}/reserve/",
                                      {"request_key": match.group(1), "quantity": "1"}, f"/offers/{listing_id}/")
    ref = re.search(r"/orders/([0-9a-f-]{36})/", url)
    return (ref.group(1) if ref else None), page


def public_link_basics(base):
    section("The public link")
    visitor = Browser(base)
    status, _, page = visitor.get("/")
    check(status == 200 and "LastPlate" in page, "home page opens through the link", status)
    check(visitor.get("/offers/")[0] == 200, "offers page opens through the link")
    # Only the web service is shared. These belong to the other services and must not answer here.
    for path in ("/admin/", "/admin/login/", "/internal/orders/", "/api/listings", "/api/auth/token/",
                 "/api/orders/", "/api/admin/shops"):
        status = visitor.get(path)[0]
        check(status == 404, f"{path} is not reachable through the link", status)
    page = visitor.get("/offers/")[2]
    check(not re.search(r"127\.0\.0\.1:800[1-3]|localhost:800[1-3]|marketplace:8000|reservations:8000", page),
          "pages link no private service address")


def restaurant_with_offer(base, admin):
    section("Restaurant and offer (through the link)")
    owner = Browser(base)
    email = f"flowcheck-orders-{RUN}@example.test"
    shop_name = f"FLOWCHECK Order shop {RUN} (fictional)"
    owner.post("/signup/", {"account_type": "restaurant", "display_name": "Flowcheck owner (fictional)",
                            "email": email, "password": PASSWORD})
    owner.post("/workspace/apply/1/", {"name": shop_name, "category": "Bakery", "public_contact": "+961 1 234 567"})
    owner.post("/workspace/apply/2/", {"address": "12 Sample Street, Beirut (fictional)", "area": "Hamra"})
    owner.post_photo("/workspace/apply/3/", {"accept_conditions": "on"}, "cover.png", png())
    token = local_api("POST", f"{ACCOUNTS}/api/auth/token/", {"email": email, "password": PASSWORD})[1].get("access")
    shop = local_api("GET", f"{MARKET}/api/shops/me/", token=token)[1]
    if not check(shop.get("approval_status") == "PENDING", "restaurant signed up and sent its request",
                 shop.get("approval_status")):
        return owner, None

    _, _, page = admin.post(f"/console/restaurants/{shop['id']}/", {"decision": "APPROVED", "identity_confirmed": "on"})
    check("is approved" in page, "Admin approves it in /console")

    now = datetime.now(LEBANON)
    title = f"FLOWCHECK Order box {RUN} (fictional)"
    owner.post("/workspace/listings/new/", {
        "title": title, "description": "Fictional order flow check.", "category": "Bakery",
        "original_price": "10.00", "discounted_price": "4.00", "total_quantity": "10",
        # Pickup started half an hour ago, so the food can be collected now.
        "pickup_start": lebanon(now - timedelta(minutes=30)), "pickup_end": lebanon(now + timedelta(hours=3)),
        "ingredients": "Flour (fictional)", "allergens": "Gluten (fictional)",
    })
    mine = local_api("GET", f"{MARKET}/api/listings/mine", token=token)[1]
    listing = next((item for item in mine if item.get("title") == title), None)
    if not check(listing is not None, "restaurant creates the offer"):
        return owner, None
    _, _, page = owner.post(f"/workspace/listings/{listing['id']}/publish/", {}, "/workspace/")
    check("published successfully" in page, "restaurant publishes it")
    page = Browser(base).get(f"/offers/{listing['id']}/")[2]
    check(title in page and left(page) == 10, "offer is public with 10 left", left(page))
    return owner, listing["id"]


def order_and_pay(base, owner, listing_id):
    section("Customer reserves; restaurant marks it Paid")
    customer = Browser(base)
    customer.post("/signup/", {"account_type": "customer", "display_name": "Flowcheck customer (fictional)",
                               "email": f"flowcheck-customer-{RUN}@example.test", "password": PASSWORD})
    ref, page = reserve(customer, listing_id)
    code = pickup_code(page)
    if not check(bool(ref and code), "customer reserves and lands on the order page with a pickup code"):
        return customer
    check(status_badge(page) == "Reserved", "order shows Reserved", status_badge(page))
    check(f"/orders/{ref}/" in customer.get("/orders/")[2], "order is in My orders")
    check(left(Browser(base).get(f"/offers/{listing_id}/")[2]) == 9, "offer now shows 9 left")

    check(code in owner.get("/workspace/orders/")[2], "restaurant sees it in Today's orders")
    page = owner.get("/workspace/orders/pickup/")[2]
    branch = re.search(r'<option value="(\d+)"', page)
    branch = branch.group(1) if branch else ""
    _, _, page = owner.post("/workspace/orders/pickup/", {"branch_id": branch, "pickup_code": code})
    version = re.search(r'name="version" value="(\d+)"', page)
    if not check(bool(version) and "Paid" in page, "restaurant finds the order by its code"):
        return customer
    _, _, page = owner.post(f"/workspace/orders/{ref}/paid/",
                            {"branch_id": branch, "version": version.group(1), "cash_received": "yes"},
                            "/workspace/orders/pickup/")
    check(status_badge(page) == "Paid", "restaurant marks it Paid", status_badge(page))
    page = customer.get(f"/orders/{ref}/")[2]
    check(status_badge(page) == "Paid", "customer's order page shows Paid", status_badge(page))
    return customer


def expiry(base, customer, listing_id):
    section("An uncollected order expires")
    ref, page = reserve(customer, listing_id)
    if not check(bool(ref and pickup_code(page)), "customer reserves a second order"):
        return
    check(left(Browser(base).get(f"/offers/{listing_id}/")[2]) == 8, "offer shows 8 left")
    result = compose("reservations", "python", "manage.py", "shell", "-v", "0", "-c", f"""
from datetime import timedelta
from django.utils import timezone
from reservations.models import Order
Order.objects.filter(reservation_ref='{ref}', status='CONFIRMED', shop_name__startswith='FLOWCHECK') \\
    .update(pick_up_by=timezone.now() - timedelta(minutes=1))
""")
    check(result.returncode == 0, "pick-up-by moved into the past (setup)", result.stderr.strip()[-300:])
    result = compose("timers", "python", "manage.py", "run_timers")
    check(result.returncode == 0, "timer job runs", result.stderr.strip()[-300:])
    page = customer.get(f"/orders/{ref}/")[2]
    check(status_badge(page) == "Expired", "customer's order shows Expired", status_badge(page))
    check(left(Browser(base).get(f"/offers/{listing_id}/")[2]) == 9, "the food is back on sale (9 left)")


def run(base):
    public_link_basics(base)

    result = compose("accounts", "python", "manage.py", "create_admin", "--email", ADMIN_EMAIL,
                     "--name", f"Flowcheck Admin {RUN} (fictional)", env_names=["LASTPLATE_ADMIN_PASSWORD"],
                     env={"LASTPLATE_ADMIN_PASSWORD": PASSWORD})
    if not check(result.returncode == 0, "fictional Admin created (setup)", result.stderr.strip()[-300:]):
        return
    admin = Browser(base)
    _, url, _ = admin.post("/login/", {"email": ADMIN_EMAIL, "password": PASSWORD})
    check(url.rstrip("/").endswith("/console"), "Admin logs in through the link and lands on /console", url)

    owner, listing_id = restaurant_with_offer(base, admin)
    if listing_id is None:
        return
    try:
        customer = order_and_pay(base, owner, listing_id)
        expiry(base, customer, listing_id)
    finally:
        # Keep the public offers page clean for the demo.
        owner.post(f"/workspace/listings/{listing_id}/sold-out/", {}, "/workspace/")
        status = Browser(base).get(f"/offers/{listing_id}/")[0]
        check(status == 404, "check's offer is marked sold out and leaves the public pages", status)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8000",
                        help="The public link (https://<words>.trycloudflare.com). Default: this laptop.")
    base = parser.parse_args().url.rstrip("/")
    print(f"Checking the order flow at {base}")
    run(base)
    print(f"\n{len(failures)} check(s) failed." if failures else "\nAll order flow checks passed.")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
