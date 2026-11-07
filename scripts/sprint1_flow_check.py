"""Integration check: the Sprint 1 flow against the running Docker Compose stack.

Separate from the unit tests. It adds clearly labelled fictional shops and offers
to the development data and never deletes anything. Its temporary admin account
is deactivated at the end (an account that reviewed a shop cannot be deleted).
Prints no passwords or tokens. Uses only the Python standard library.

Usage (from the repository root, with the stack running):
    python scripts/sprint1_flow_check.py
"""

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
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB, ACCOUNTS, MARKET = "http://127.0.0.1:8000", "http://127.0.0.1:8001", "http://127.0.0.1:8002"
RUN = time.strftime("%H%M%S")
OWNER_PASSWORD = secrets.token_urlsafe(18) + "-Aa1"
ADMIN_PASSWORD = secrets.token_urlsafe(24)
REVIEWER = f"flowcheck-reviewer-{RUN}"
# Lebanon is UTC+2 or UTC+3; two days ahead keeps the window in the future in both.
DAY = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")
START, END = f"{DAY}T16:00", f"{DAY}T18:00"
failures = []


def check(ok, label, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail != "" and not ok else ""))
    if not ok:
        failures.append(label)


def section(title):
    print(f"\n== {title}")


def manage(code, password=False):
    args = ["docker", "compose", "--env-file", str(ROOT / "compose.env"), "exec", "-T"]
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    if password:
        args += ["-e", "FLOW_CHECK_PASSWORD"]
        env["FLOW_CHECK_PASSWORD"] = ADMIN_PASSWORD
    args += ["marketplace", "python", "manage.py", "shell", "-v", "0", "-c", code]
    subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True, env=env)


def multipart(fields, filename, content_type, content):
    boundary = uuid.uuid4().hex
    body = b""
    for name, value in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    body += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode() + content + b"\r\n" + f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def valid_png():
    width = height = 32
    pixels = b"".join(b"\0" + b"\xc8\x50\x20" * width for _ in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


class Browser:
    """One cookie jar, as one browser would have."""

    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def request(self, method, url, body=None, headers=None):
        request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        try:
            with self.opener.open(request, timeout=30) as response:
                return response.status, response.geturl(), html.unescape(response.read().decode(errors="replace"))
        except urllib.error.HTTPError as error:
            return error.code, url, html.unescape(error.read().decode(errors="replace"))

    def get(self, url):
        return self.request("GET", url)

    def csrf(self, url):
        match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', self.get(url)[2])
        return match.group(1) if match else ""

    def post(self, url, data, form_url=None):
        fields = {"csrfmiddlewaretoken": self.csrf(form_url or url), **data}
        return self.request("POST", url, urllib.parse.urlencode(fields).encode(),
                            {"Content-Type": "application/x-www-form-urlencoded"})

    def post_photo(self, url, data, filename, content_type, content):
        body, header = multipart({"csrfmiddlewaretoken": self.csrf(url), **data}, filename, content_type, content)
        return self.request("POST", url, body, {"Content-Type": header})


def api(method, url, payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status, text = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, text = error.code, error.read()
    try:
        return status, json.loads(text)
    except ValueError:
        return status, {}


def new_operator(browser, label):
    email = f"flowcheck-{label}-{RUN}@example.test"
    shop = f"FLOWCHECK {label} shop {RUN} (fictional)"
    browser.post(f"{WEB}/signup/", {"account_type": "restaurant", "display_name": f"Flowcheck {label} (fictional)",
                                    "email": email, "password": OWNER_PASSWORD})
    # LP-37: the three-step request to join LastPlate.
    browser.post(f"{WEB}/workspace/apply/1/", {"name": shop, "category": "Bakery", "public_contact": "+961 1 234 567"})
    browser.post(f"{WEB}/workspace/apply/2/", {"address": "12 Sample Street, Beirut (fictional)", "area": "Hamra"})
    browser.post_photo(f"{WEB}/workspace/apply/3/", {"accept_conditions": "on"}, "cover.png", "image/png", valid_png())
    _, tokens = api("POST", f"{ACCOUNTS}/api/auth/token/", {"email": email, "password": OWNER_PASSWORD})
    token = tokens["access"]
    return token, api("GET", f"{MARKET}/api/shops/me/", token=token)[1]


def offer(title, **changes):
    return {"title": title, "description": "Fictional check offer.", "category": "Bakery", "original_price": "10.00",
            "discounted_price": "4.00", "total_quantity": "10", "pickup_address": "12 Sample Street, Beirut (fictional)",
            "pickup_start": START, "pickup_end": END, "ingredients": "Flour (fictional)", "allergens": "Gluten (fictional)", **changes}


def mine(token):
    return api("GET", f"{MARKET}/api/listings/mine", token=token)[1]


def own(token, title):
    return next(item for item in mine(token) if item["title"] == title)


def decide(admin, shop_id, **data):
    return admin.post(f"{MARKET}/admin/marketplace/shop/{shop_id}/change/", {"_save": "Save", **data})


def is_public(listing_id):
    return api("GET", f"{MARKET}/api/listings/{listing_id}")[0] == 200


def run():
    workspace = f"{WEB}/workspace/"
    new_offer = f"{WEB}/workspace/listings/new/"

    section("Sign-in and Pending shop")
    owner = Browser()
    token, shop = new_operator(owner, "owner")
    check(shop["approval_status"] == "PENDING" and shop["profile_complete"], "new shop is Pending with a complete profile", shop)
    check(shop["application_sent"] and shop["category"] == "Bakery" and shop["photo_display"], "request sent with category and photo", shop)
    check("Request sent" in owner.get(workspace)[2], "Pending owner sees the request status")
    check("Request sent" in owner.get(new_offer)[2], "Pending owner cannot open offer pages")
    title = f"FLOWCHECK Pastry box {RUN} (fictional)"
    check(api("POST", f"{MARKET}/api/listings", {"title": title}, token)[0] == 201, "API: Pending shop can save a draft")
    listing = own(token, title)
    api("POST", f"{MARKET}/api/listings/{listing['id']}/publish", {}, token)
    check(own(token, title)["status"] == "DRAFT", "Pending shop cannot publish")
    check(api("PATCH", f"{MARKET}/api/shops/me/", {"approval_status": "APPROVED"}, token)[0] == 400, "owner cannot approve itself")

    section("Admin approval")
    admin = Browser()
    page = admin.post(f"{MARKET}/admin/login/", {"username": REVIEWER, "password": ADMIN_PASSWORD, "next": "/admin/"})
    check(page[1].endswith("/admin/"), "team admin signs in", page[1])
    check("was changed successfully" in decide(admin, shop["id"], approval_status="APPROVED", rejection_reason="", identity_confirmed="on")[2], "shop approved")
    check("Approved" in owner.get(workspace)[2], "workspace shows Approved")

    section("Photos")
    photo_title = f"FLOWCHECK Photo offer {RUN} (fictional)"
    page = owner.post_photo(new_offer, offer(photo_title), "check.png", "image/png", valid_png())[2]
    check("photo were saved" in page, "valid PNG uploads through the web form")
    with_photo = own(token, photo_title)
    path = with_photo.get("photo_display") or ""
    check(path.startswith("/media/"), "photo stored", path)
    status, _, _ = owner.get(MARKET + path)
    check(status == 200, "uploaded photo can be downloaded by a browser", status)
    page = owner.post_photo(f"{WEB}/workspace/listings/{with_photo['id']}/edit/", offer(photo_title), "fake.png", "image/png", b"not an image")[2]
    check("not a valid image" in page and own(token, photo_title)["photo_display"] == path, "fake image refused; photo kept")
    other = Browser()
    other_token, other_shop = new_operator(other, "other")
    body, header = multipart({}, "a.png", "image/png", valid_png())
    request = urllib.request.Request(f"{MARKET}/api/listings/{with_photo['id']}/photo", data=body, method="POST",
                                     headers={"Content-Type": header, "Authorization": f"Bearer {other_token}"})
    try:
        status = urllib.request.urlopen(request, timeout=30).status
    except urllib.error.HTTPError as error:
        status = error.code
    check(status in (403, 404), "another operator cannot change the photo", status)

    section("Publishing and public pages")
    page = owner.post(f"{WEB}/workspace/listings/{with_photo['id']}/publish/", {}, form_url=workspace)[2]
    check("published successfully" in page, "valid offer published")
    lid = with_photo["id"]
    page = Browser().get(f"{WEB}/offers/{lid}/")[2]
    for want in (photo_title, "$4.00", "$10.00", "10 left", "16:00", "18:00", "12 Sample Street", "Gluten (fictional)", "Hamra", "+961 1 234 567"):
        check(want in page, f"public details show {want}")
    check(f"{MARKET}{path}" in page, "public page links the photo at a browser-reachable address")
    check("marketplace:8000" not in page, "no Docker-internal hostname on the page")
    check(photo_title in Browser().get(f"{WEB}/offers/")[2], "offer on the public list")
    check(not is_public(listing["id"]), "drafts stay private")

    section("Stock")
    stock_url = f"{WEB}/workspace/listings/{lid}/stock/"

    def quantity():
        return own(token, photo_title)["total_quantity"]

    page = owner.post(stock_url, {"quantity_change": "5", "reason": "Fictional extra batch"}, form_url=workspace)[2]
    check("Added 5 units" in page and quantity() == 15, "add stock with feedback")
    page = owner.post(stock_url, {"quantity_change": "-3", "reason": "Fictional damage"}, form_url=workspace)[2]
    check("Removed 3 units" in page and quantity() == 12, "remove stock with feedback")
    for label, change, reason in (("zero", "0", "x"), ("text", "abc", "x"), ("decimal", "1.5", "x"), ("no reason", "2", " "),
                                  ("below zero", "-999", "x"), ("huge", "99999999999999", "x")):
        status, _, _ = owner.post(stock_url, {"quantity_change": change, "reason": reason}, form_url=workspace)
        check(status == 200 and quantity() == 12, f"stock refused: {label}", status)

    section("Other operators")
    decide(admin, other_shop["id"], approval_status="APPROVED", rejection_reason="", identity_confirmed="on")
    check(other.get(f"{WEB}/workspace/listings/{listing['id']}/edit/")[0] == 404, "cannot open another shop's draft")
    other.post(stock_url, {"quantity_change": "5", "reason": "Fictional attempt"}, form_url=workspace)
    other.post(f"{WEB}/workspace/listings/{listing['id']}/publish/", {}, form_url=workspace)
    check(quantity() == 12 and own(token, title)["status"] == "DRAFT", "another shop's stock and publish attempts change nothing")
    check(api("PATCH", f"{MARKET}/api/listings/{listing['id']}", {"title": "x"}, other_token)[0] == 404, "API: cannot edit another shop's draft")
    check(api("POST", f"{MARKET}/api/listings/{lid}/stock-adjustments", {"quantity_change": 1, "reason": "x"}, other_token)[0] == 403, "API: cannot adjust another shop's stock")
    check(api("POST", f"{MARKET}/api/listings", {"title": "x"})[0] == 401, "API: no token is refused")
    check("/login" in Browser().get(new_offer)[1], "anonymous visitor is sent to login")

    section("Offers that must leave public view")
    owner.post(stock_url, {"quantity_change": "-12", "reason": "Fictional sell-out"}, form_url=workspace)
    check(not is_public(lid), "sold-out offer is hidden")
    owner.post(stock_url, {"quantity_change": "12", "reason": "Fictional restock"}, form_url=workspace)
    check(is_public(lid), "restocked offer returns")

    expired_title = f"FLOWCHECK Expired offer {RUN} (fictional)"
    owner.post(new_offer, offer(expired_title))
    expired = own(token, expired_title)
    owner.post(f"{WEB}/workspace/listings/{expired['id']}/publish/", {}, form_url=workspace)
    check(is_public(expired["id"]), "offer is public before its pickup ends")
    manage(f"""
from datetime import timedelta
from django.utils import timezone
from marketplace.models import Listing
now = timezone.now()
Listing.objects.filter(pk={expired['id']}, title__startswith='FLOWCHECK').update(pickup_start=now - timedelta(hours=3), pickup_end=now - timedelta(hours=1))
""")
    check(not is_public(expired["id"]), "offer past its pickup end is hidden")

    other_title = f"FLOWCHECK Other shop offer {RUN} (fictional)"
    other.post(new_offer, offer(other_title))
    other_listing = own(other_token, other_title)
    other.post(f"{WEB}/workspace/listings/{other_listing['id']}/publish/", {}, form_url=workspace)
    check(is_public(other_listing["id"]), "second shop's offer is public while approved")
    reason = "Fictional reason: this is a check."
    decide(admin, other_shop["id"], approval_status="REJECTED", rejection_reason=reason)
    page = other.get(workspace)[2]
    check("Not approved" in page and reason in page, "rejected shop sees its status and the reason")
    check(not is_public(other_listing["id"]), "rejected shop's offer is hidden")
    check(is_public(lid), "other shops' offers stay public")


manage(f"""
import os
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
user = get_user_model().objects.create_user('{REVIEWER}', password=os.environ['FLOW_CHECK_PASSWORD'], is_staff=True)
user.user_permissions.add(Permission.objects.get(codename='review_shop'))
""", password=True)
try:
    run()
finally:
    manage(f"""
from django.contrib.auth import get_user_model
for user in get_user_model().objects.filter(username='{REVIEWER}'):
    user.is_active = False
    user.set_unusable_password()
    user.save()
""")

print(f"\n{len(failures)} check(s) failed." if failures else "\nAll integration checks passed.")
sys.exit(1 if failures else 0)
