"""LP-15: Start the stack and load the fictional Sprint 1 demonstration data.

1. Rebuilds and starts every service with Docker Compose.
2. Creates or resets the demo shop-operator accounts in Accounts.
3. Creates or resets the demo shops, offers and team-admin in Marketplace.
4. Prints the sign-in details for the presenter.

Safe to repeat: run it again shortly before the demonstration so every pickup
time is in the future. It touches only the demo accounts and demo shops.

The password is never stored in the repository. Pass your own with
--password, or a random one is generated and printed.

Usage (from the repository root, after scripts/init_dev_env.py):
    python scripts/seed_demo.py
    python scripts/seed_demo.py --password "Choose-a-demo-password-1"
"""

import argparse
import json
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / "compose.env"


def compose(*args, capture=False):
    command = ["docker", "compose", "--env-file", str(ENV_FILE), *args]
    result = subprocess.run(command, cwd=ROOT, capture_output=capture, text=True)
    if result.returncode != 0:
        if capture:
            sys.stderr.write(result.stdout + result.stderr)
        sys.exit(f"Command failed: docker compose {' '.join(args[:4])} ...")
    return result.stdout


def manage(service, *args, capture=False):
    return compose("exec", "-T", service, "python", "manage.py", *args, capture=capture)


def main():
    sys.stdout.reconfigure(line_buffering=True)  # keep our lines in order with Docker's
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--password", help="Password for every demo account (default: random).")
    parser.add_argument("--no-build", action="store_true", help="Skip rebuilding and restarting the services.")
    options = parser.parse_args()
    password = options.password or f"Demo-{secrets.token_urlsafe(9)}"

    if not ENV_FILE.exists():
        sys.exit("compose.env is missing. Run scripts/init_dev_env.py first.")

    if not options.no_build:
        print("Starting the services (this can take a minute)...")
        compose("up", "-d", "--build", "--wait")

    print("Creating the demo shop-operator accounts...")
    output = manage("accounts", "seed_demo", "--password", password, capture=True)
    operators = json.loads(output.strip().splitlines()[-1])

    print("Creating the demo shops and offers...")
    manage("marketplace", "seed_demo", "--operators", json.dumps(operators), "--password", password)

    print(
        f"""
Demo data is ready. Everything below is fictional.

  START HERE: demo guide       http://127.0.0.1:8000/demo/

  Customer view (no sign-in)   http://127.0.0.1:8000/offers/
  Shop sign-in                 http://127.0.0.1:8000/sign-in/
  Team-admin shop review       http://127.0.0.1:8002/admin/marketplace/shop/

  Customer        customer@demo.lastplate.test  Maya
  Approved shop   cedar@demo.lastplate.test     Cedar Crumb Bakery
  Approved shop   sunrise@demo.lastplate.test   Sunrise Patisserie
  Pending shop    newshop@demo.lastplate.test   Zaatar Corner
  Team admin      demo-admin                    Marketplace admin

  Password for all of them: {password}

Walkthrough: docs/sprint-1-demo.md
"""
    )


if __name__ == "__main__":
    main()
