"""LP-57 integration check against the running Docker Compose stack.

Creates a fictional Admin with the create_admin command, signs up two fictional
restaurants, then approves one and rejects the other from /console, checking
that customers and restaurants are kept out of /console and the Admin API.

    .\\.venv\\Scripts\\python.exe scripts\\console_flow_check.py
"""

import html
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB, ACCOUNTS, MARKET = "http://127.0.0.1:8000", "http://127.0.0.1:8001", "http://127.0.0.1:8002"
RUN = time.strftime("%H%M%S")
PASSWORD = secrets.token_urlsafe(18) + "-Aa1"
ADMIN_EMAIL = f"flowcheck-admin-{RUN}@example.test"
failures = []


def check(ok, label, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail != "" and not ok else ""))
    if not ok:
        failures.append(label)


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

    def post(self, url, data):
        match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', self.get(url)[2])
        fields = {"csrfmiddlewaretoken": match.group(1) if match else "", **data}
        return self.request("POST", url, urllib.parse.urlencode(fields).encode(),
                            {"Content-Type": "application/x-www-form-urlencoded"})


def api(method, url, payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, body, headers, method=method), timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as error:
        return error.code, {}


def create_admin():
    env = {**os.environ, "LASTPLATE_ADMIN_PASSWORD": PASSWORD, "MSYS_NO_PATHCONV": "1"}
    args = ["docker", "compose", "--env-file", str(ROOT / "compose.env"), "exec", "-T",
            "-e", "LASTPLATE_ADMIN_PASSWORD", "accounts", "python", "manage.py", "create_admin",
            "--email", ADMIN_EMAIL, "--name", f"Flowcheck Admin {RUN} (fictional)"]
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, env=env)
    check(result.returncode == 0, "create_admin makes an Admin account", result.stderr.strip())


def new_restaurant(label):
    browser = Browser()
    email = f"flowcheck-{label}-{RUN}@example.test"
    shop = f"FLOWCHECK {label} {RUN} (fictional)"
    browser.post(f"{WEB}/signup/", {"account_type": "restaurant", "display_name": f"Flowcheck {label}",
                                    "email": email, "password": PASSWORD})
    browser.post(f"{WEB}/workspace/finish-setup/", {"shop_name": shop})
    browser.post(f"{WEB}/workspace/profile/", {"name": shop, "address": "12 Sample Street, Beirut (fictional)",
                                               "area": "Hamra", "public_contact": "+961 1 234 567"})
    token = api("POST", f"{ACCOUNTS}/api/auth/token/", {"email": email, "password": PASSWORD})[1]["access"]
    return browser, token, api("GET", f"{MARKET}/api/shops/me/", token=token)[1]


def run():
    create_admin()
    admin = Browser()
    status, url, page = admin.post(f"{WEB}/login/", {"email": ADMIN_EMAIL, "password": PASSWORD})
    check(url.rstrip("/").endswith("/console"), "Admin lands on /console after login", url)
    check("Pending restaurants" in page and "Live offers" in page, "Console home shows the counts")

    owner, owner_token, approve_me = new_restaurant("approve")
    _, _, reject_me = new_restaurant("reject")

    _, _, pending = admin.get(f"{WEB}/console/restaurants/?tab=pending")
    check(approve_me["name"] in pending and reject_me["name"] in pending, "Both restaurants are on the Pending tab")

    page_url = f"{WEB}/console/restaurants/{approve_me['id']}/"
    _, _, page = admin.post(page_url, {"decision": "APPROVED"})
    check("Confirm the applicant" in page, "Approving without the tick is refused")
    _, _, page = admin.post(page_url, {"decision": "APPROVED", "identity_confirmed": "on"})
    check("is approved" in page and "Approved" in page, "Admin approves the restaurant in /console")
    check("Approved" in owner.get(f"{WEB}/workspace/")[2], "The owner's workspace shows Approved")

    page_url = f"{WEB}/console/restaurants/{reject_me['id']}/"
    _, _, page = admin.post(page_url, {"decision": "REJECTED", "reason": ""})
    check("Give a reason" in page, "Rejecting without a reason is refused")
    _, _, page = admin.post(page_url, {"decision": "REJECTED", "reason": "Flowcheck: fictional applicant."})
    check("was rejected" in page, "Admin rejects the other restaurant with a reason")

    _, url, _ = owner.get(f"{WEB}/console/")
    check(not url.rstrip("/").endswith("/console"), "A restaurant is sent away from /console", url)
    status, _ = api("GET", f"{MARKET}/api/admin/shops", token=owner_token)
    check(status == 403, "Marketplace refuses the Admin API to a restaurant token", status)
    status, _ = api("POST", f"{MARKET}/api/admin/shops/{reject_me['id']}/review",
                    {"decision": "APPROVED", "identity_confirmed": True}, token=owner_token)
    check(status == 403, "A forged review request from a restaurant is refused", status)


run()
print(f"\n{len(failures)} check(s) failed." if failures else "\nAll console checks passed.")
sys.exit(1 if failures else 0)
