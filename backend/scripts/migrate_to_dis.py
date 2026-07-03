#!/usr/bin/env python3
"""One-time migration: Fernet-encrypted secrets -> DIS (AES-256-GCM).

Lauft einmalig beim Update (von install.sh getriggert), NACHDEM der DIS
Sidecar gestartet wurde und VORDEM das Panel startet.

Liest alle Fernet-verschluesselten Werte (Prefix 'gAAAAA'), entschluesselt
sie mit dem alten Fernet-Key (SHA256(secret_key)) und verschluesselt sie
neu mit DIS ueber den Sidecar (mit jeweiligem AAD-Context).

Passwort-Hashes werden hier NICHT migriert — das passiert lazy beim Login
(siehe AuthService.rehash_password_if_needed).
"""
from __future__ import annotations

import hashlib
import sys

from cryptography.fernet import Fernet

from config import settings
from database import SessionLocal, engine
from models import User, OAuthProvider, PostgresDatabase, PanelSetting
from services.dis_client import DisClient


def _old_fernet() -> Fernet:
    """Rekonstruiert den alten Fernet-Key aus settings.secret_key."""
    key = hashlib.sha256(settings.secret_key.encode()).digest()
    return Fernet(key)


def _is_fernet(value: str) -> bool:
    """Erkennt Fernet-formatierte Werte am Praefix."""
    return bool(value) and value.startswith("gAAAAA")


def migrate() -> None:
    fernet = _old_fernet()
    db = SessionLocal()
    migrated = 0
    skipped = 0

    try:
        # 1. Users: 2FA secrets
        for user in db.query(User).all():
            val = user.two_factor_secret_encrypted
            if val and _is_fernet(val):
                plaintext = fernet.decrypt(val.encode()).decode()
                user.two_factor_secret_encrypted = DisClient.encrypt(
                    plaintext, aad=f"msm:user:{user.id}:2fa"
                )
                migrated += 1
            elif val:
                skipped += 1

        # 2. OAuth providers: client secrets
        for provider in db.query(OAuthProvider).all():
            val = provider.client_secret_encrypted
            if val and _is_fernet(val):
                plaintext = fernet.decrypt(val.encode()).decode()
                provider.client_secret_encrypted = DisClient.encrypt(
                    plaintext, aad="msm:oauth:secret"
                )
                migrated += 1
            elif val:
                skipped += 1

        # 3. Postgres databases: owner passwords
        for pgdb in db.query(PostgresDatabase).all():
            val = pgdb.owner_password_encrypted
            if val and _is_fernet(val):
                plaintext = fernet.decrypt(val.encode()).decode()
                pgdb.owner_password_encrypted = DisClient.encrypt(
                    plaintext, aad="msm:pg:db:owner"
                )
                migrated += 1
            elif val:
                skipped += 1

        # 4. Panel settings: steam password, postgres admin password
        _PANEL_SECRET_KEYS = {
            "steam_account_password_enc": "msm:steam:password",
            "managed_postgres.admin_password_encrypted": "msm:pg:admin",
        }
        for key, aad in _PANEL_SECRET_KEYS.items():
            row = db.query(PanelSetting).filter_by(key=key).first()
            if row and _is_fernet(row.value):
                plaintext = fernet.decrypt(row.value.encode()).decode()
                row.value = DisClient.encrypt(plaintext, aad=aad)
                migrated += 1
            elif row and row.value:
                skipped += 1

        # 5. Panel settings: GitHub token (plain-text -> DIS encrypted)
        gh_row = db.query(PanelSetting).filter_by(key="github_clone_token").first()
        if gh_row and gh_row.value and not _is_fernet(gh_row.value):
            # Legacy plain-text token — encrypt with DIS
            enc = DisClient.encrypt(gh_row.value.strip(), aad="msm:github:token")
            db.add(PanelSetting(key="github_clone_token_enc", value=enc))
            gh_row.value = ""  # Legacy plain-text loeschen
            migrated += 1

        # 6. Users: E-Mail-Verschluesselung (plain -> DIS encrypted + hash)
        for user in db.query(User).filter(User.email_encrypted.is_(None)).all():
            if user.email_plain:
                user.email = user.email_plain  # Setter verschluesselt + hasht
                migrated += 1

        db.commit()
        print(f"[DIS Migration] Done: {migrated} values migrated, {skipped} already DIS-format or empty.")
    except Exception as e:
        db.rollback()
        print(f"[DIS Migration] ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    # Ensure tables exist
    from models import Base
    Base.metadata.create_all(bind=engine)

    # Check sidecar is running
    if not DisClient.health_check():
        print("[DIS Migration] ERROR: DIS Sidecar not reachable. Start it first.", file=sys.stderr)
        sys.exit(1)

    migrate()
