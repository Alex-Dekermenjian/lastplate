"""Start the Docker Compose stack and prove each service runs independently.

Checks, in order:
1. Each service starts on its own (with only PostgreSQL), reports live and
   ready, and is connected to its own database.
2. All services run together; each uses a different database, and the web
   frontend reaches the three services over their health APIs.
3. Each service's database role can connect to its own database and is
   refused by every other service's database.
4. Marketplace's non-root user can write uploaded photos to their persistent
   volume, and nowhere else in the application folder.

The stack is left running. Stop it with
    docker compose --env-file compose.env down
which keeps the database volume. Uses only the Python standard library.

Usage (from the repository root, after scripts/init_dev_env.py):
    python scripts/smoke_check.py
"""

import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / "compose.env"
PORTS = {"accounts": 8001, "marketplace": 8002, "reservations": 8003, "web": 8000}
DOMAIN_SERVICES = ("accounts", "marketplace", "reservations")

failures = []


def compose(*args, check=True, capture=False, env=None):
    command = ["docker", "compose", "--env-file", str(ENV_FILE), *args]
    return subprocess.run(
        command, cwd=ROOT, check=check, capture_output=capture, text=True, env=env
    )


def expect(condition, message):
    print(("PASS " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


def get_json(url, attempts=20):
    for attempt in range(attempts):
        try:
            with urlopen(url, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)
        except (URLError, ConnectionError, TimeoutError):
            time.sleep(1)
    return None, {}


def check_service(name):
    base = f"http://127.0.0.1:{PORTS[name]}"
    status, body = get_json(f"{base}/health/live/")
    expect(status == 200 and body.get("service") == name, f"{name}: live")
    status, body = get_json(f"{base}/health/ready/")
    expect(
        status == 200 and body.get("status") == "ready",
        f"{name}: ready ({body.get('checks')})",
    )
    expect(
        body.get("database") == f"lastplate_{name}",
        f"{name}: connected to lastplate_{name} (got {body.get('database')})",
    )
    return body.get("database")


def running_services():
    result = compose("ps", "--status", "running", "--services", capture=True)
    return set(result.stdout.split())


def read_passwords():
    values = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def can_connect(role, password, database):
    result = compose(
        "exec", "-T", "-e", f"PGPASSWORD={password}", "postgres",
        "psql", "-h", "127.0.0.1", "-U", role, "-d", database,
        "-tAc", "SELECT current_user",
        check=False, capture=True,
    )
    return result.returncode == 0


def main():
    if not ENV_FILE.exists():
        sys.exit("compose.env is missing. Run scripts/init_dev_env.py first.")

    apps = [*DOMAIN_SERVICES, "web"]
    print("== Building images and starting PostgreSQL")
    compose("build")
    compose("stop", *apps)
    compose("up", "-d", "--wait", "postgres")

    print("== Each service started alone")
    for name in apps:
        compose("up", "-d", "--wait", name)
        others = running_services() & (set(apps) - {name})
        expect(not others, f"{name}: started without other services (running: {others or 'none'})")
        check_service(name)
        compose("stop", name)

    print("== All services together")
    compose("up", "-d", "--wait")
    databases = [check_service(name) for name in apps]
    expect(len(set(databases)) == len(apps), f"four distinct databases: {databases}")
    with urlopen("http://127.0.0.1:8000/", timeout=10) as response:
        page = response.read().decode()
    expect(page.count("<td>ready</td>") == 3, "web: status page reports 3 services ready")

    print("== Database isolation")
    passwords = read_passwords()
    for owner in apps:
        role = f"lastplate_{owner}"
        password = passwords[f"{owner.upper()}_DB_PASSWORD"]
        for target in apps:
            allowed = can_connect(role, password, f"lastplate_{target}")
            if owner == target:
                expect(allowed, f"{role} can connect to its own database")
            else:
                expect(not allowed, f"{role} is refused by lastplate_{target}")

    print("== Photo storage")
    probe = (
        "import os, pathlib\n"
        "media = pathlib.Path('/app/media')\n"
        "print('volume' if os.stat(media).st_dev != os.stat('/app').st_dev else 'not-a-volume')\n"
        "print('root' if os.getuid() == 0 else 'non-root')\n"
        "test = media / '.smoke-check'\n"
        "test.write_text('x'); test.unlink(); print('writable')\n"
        "try:\n"
        "    pathlib.Path('/app/.smoke-check').write_text('x'); print('app-writable')\n"
        "except PermissionError:\n"
        "    print('app-protected')\n"
    )
    result = compose("exec", "-T", "marketplace", "python", "-c", probe, capture=True, check=False)
    seen = set(result.stdout.split())
    expect("volume" in seen, "marketplace: /app/media is a mounted volume")
    expect("non-root" in seen, "marketplace: runs as a non-root user")
    expect("writable" in seen, "marketplace: runtime user can write photos")
    expect("app-protected" in seen, "marketplace: rest of /app is read-only to that user")

    if failures:
        print(f"\n{len(failures)} check(s) failed.")
        compose("ps")
        sys.exit(1)
    print("\nAll smoke checks passed. The stack is still running.")


if __name__ == "__main__":
    main()
