"""Create private local settings for the Docker Compose development stack.

Writes compose.env (used by Docker Compose) and, unless --compose-only is
given, a .env file in each service folder for running that service outside
Docker against the Compose PostgreSQL. Existing files are never overwritten;
missing service files reuse the passwords already in compose.env. Secrets are
generated randomly and never printed.

Usage (PowerShell, from the repository root):
    .\\.venv\\Scripts\\python.exe scripts\\init_dev_env.py
"""

import argparse
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_ENV = ROOT / "compose.env"
SERVICES = {
    "accounts": ROOT / "services" / "accounts",
    "marketplace": ROOT / "services" / "marketplace",
    "reservations": ROOT / "services" / "reservations",
    "web": ROOT / "web",
}
POSTGRES_HOST_PORT = "55432"


def read_env(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    return values


def write_new(path, lines):
    # "x" mode refuses to replace a file that already exists.
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"created {path.relative_to(ROOT)}")


def compose_values():
    if COMPOSE_ENV.exists():
        print("kept existing compose.env")
        return read_env(COMPOSE_ENV)

    values = {
        "POSTGRES_ADMIN_PASSWORD": secrets.token_urlsafe(24),
        "POSTGRES_HOST_PORT": POSTGRES_HOST_PORT,
    }
    for name in SERVICES:
        values[f"{name.upper()}_DB_PASSWORD"] = secrets.token_urlsafe(24)
        values[f"{name.upper()}_SECRET_KEY"] = secrets.token_urlsafe(50)
    write_new(
        COMPOSE_ENV,
        ["# Private local Docker Compose settings. Never commit this file."]
        + [f"{key}={value}" for key, value in values.items()],
    )
    return values


def write_service_env(name, folder, values):
    path = folder / ".env"
    if path.exists():
        print(f"kept existing {path.relative_to(ROOT)}")
        return

    lines = [
        f"# Private local settings for {name}. Never commit this file.",
        f"DB_NAME=lastplate_{name}",
        f"DB_USER=lastplate_{name}",
        f"DB_PASSWORD='{values[f'{name.upper()}_DB_PASSWORD']}'",
        "DB_HOST=127.0.0.1",
        f"DB_PORT={values.get('POSTGRES_HOST_PORT', POSTGRES_HOST_PORT)}",
        f"DJANGO_SECRET_KEY={values[f'{name.upper()}_SECRET_KEY']}",
        "DJANGO_DEBUG=True",
        "DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost",
        "HEALTH_EXPOSE_DETAILS=True",
    ]
    if name == "web":
        lines += [
            "ACCOUNTS_SERVICE_URL=http://127.0.0.1:8001",
            "MARKETPLACE_SERVICE_URL=http://127.0.0.1:8002",
            "RESERVATIONS_SERVICE_URL=http://127.0.0.1:8003",
        ]
    write_new(path, lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--compose-only", action="store_true", help="only create compose.env"
    )
    args = parser.parse_args()

    values = compose_values()
    if not args.compose_only:
        for name, folder in SERVICES.items():
            write_service_env(name, folder, values)


if __name__ == "__main__":
    main()
