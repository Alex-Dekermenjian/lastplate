"""Create private local settings for the Docker Compose development stack.

Writes compose.env (used by Docker Compose), the Accounts RS256 token key
pair in .keys/, and, unless --compose-only is given, a .env file in each
service folder for running that service outside Docker against the Compose
PostgreSQL. --keys-only creates just the key pair. Existing files are never
overwritten; missing service files reuse the passwords already in compose.env.
Secrets are generated randomly and never printed.

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
KEY_DIR = ROOT / ".keys"
PRIVATE_KEY = KEY_DIR / "jwt-private.pem"
PUBLIC_KEY = KEY_DIR / "jwt-public.pem"
# LP-28: Reservations' key for Marketplace's /internal/ endpoints.
SERVICE_KEYS = [KEY_DIR / "reservations-to-marketplace.key"]


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


def ensure_service_keys():
    """LP-28: one random key per calling service pair, created once and
    never replaced. Running this script again adds a key that is missing."""
    for path in SERVICE_KEYS:
        if path.exists():
            print(f"kept existing {path.relative_to(ROOT)}")
            continue
        KEY_DIR.mkdir(exist_ok=True)
        write_new(path, [secrets.token_urlsafe(48)])


def ensure_jwt_keys():
    """Create the Accounts RS256 key pair once; never replace an existing key."""
    if PRIVATE_KEY.exists() and PUBLIC_KEY.exists():
        print("kept existing .keys/jwt-private.pem and .keys/jwt-public.pem")
        return
    if PRIVATE_KEY.exists() or PUBLIC_KEY.exists():
        raise SystemExit(
            "Only one of .keys/jwt-private.pem and .keys/jwt-public.pem exists. "
            "Restore the missing file or remove both to generate a new pair."
        )

    # Imported here so the rest of the script needs only the standard library.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    KEY_DIR.mkdir(exist_ok=True)
    write_new(
        PRIVATE_KEY,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode().splitlines(),
    )
    write_new(
        PUBLIC_KEY,
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode().splitlines(),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--compose-only", action="store_true", help="only create compose.env and the token keys"
    )
    group.add_argument("--keys-only", action="store_true", help="only create the token keys")
    args = parser.parse_args()

    ensure_jwt_keys()
    ensure_service_keys()
    if args.keys_only:
        return
    values = compose_values()
    if not args.compose_only:
        for name, folder in SERVICES.items():
            write_service_env(name, folder, values)


if __name__ == "__main__":
    main()
