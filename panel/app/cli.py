from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import select

from .auth import hash_password
from .database import SessionLocal
from .models import User


def create_admin(username: str, password: str, force: bool = False) -> int:
    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.username == username))
        if existing is not None:
            if not force:
                print(f"User already exists: {username}")
                return 1
            existing.password_hash = hash_password(password)
            existing.is_active = True
            db.add(existing)
            db.commit()
            print(f"Updated admin user: {username}")
            return 0

        user = User(username=username, password_hash=hash_password(password), is_active=True)
        db.add(user)
        db.commit()
        print(f"Created admin user: {username}")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Conan Exiles Enhanced Server Panel utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_admin_parser = subparsers.add_parser("create-admin", help="Create or update the initial admin account")
    create_admin_parser.add_argument("--username", required=True)
    create_admin_parser.add_argument(
        "--password",
        required=False,
        help="Admin password. Omit to be prompted interactively (safer — avoids shell history exposure).",
    )
    create_admin_parser.add_argument("--force", action="store_true")

    args = parser.parse_args()

    if args.command == "create-admin":
        username = args.username.strip()
        if not username:
            print("Error: username must not be empty.", file=sys.stderr)
            return 1
        # Password acquisition precedence: CLI arg → env var → interactive prompt.
        # Interactive prompt is safest: it never appears in process listings or shell history.
        password: str = args.password or os.environ.get("CONAN_PANEL_ADMIN_PASSWORD", "")
        if not password:
            try:
                password = getpass.getpass("Admin password: ")
            except EOFError:
                print("\nError: password input cancelled.", file=sys.stderr)
                return 1
        if not password:
            print("Error: password must not be empty.", file=sys.stderr)
            return 1
        return create_admin(username, password, args.force)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
