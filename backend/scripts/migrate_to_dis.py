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

import base64
import hashlib
import os
import sys

# Ensure backend root is in sys.path so config, database, etc. can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cryptography.fernet import Fernet

from config import settings
from database import SessionLocal, engine
from models import User, OAuthProvider, PostgresDatabase, PanelSetting
from services.dis_client import DisClient


def _old_fernet() -> Fernet:
    """Rekonstruiert den alten Fernet-Key aus settings.secret_key."""
    digest = hashlib.sha256(settings.secret_key.encode()).digest()
    key = base64.urlsafe_b64encode(digest)
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
        # 1. Users: 2FA secrets & Reset token invalidation
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
            if user.password_reset_token and len(user.password_reset_token) < 64:
                user.password_reset_token = None
                migrated += 1

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

        # SMTP password migration (plain -> DIS encrypted)
        smtp_row = db.query(PanelSetting).filter_by(key="smtp_password").first()
        if smtp_row and smtp_row.value and not smtp_row.value.startswith("test-enc-v1:"):
            enc = DisClient.encrypt(smtp_row.value.strip(), aad="msm:settings:smtp_password")
            db.add(PanelSetting(key="smtp_password_encrypted", value=enc))
            smtp_row.value = ""
            migrated += 1

        # Resend API key migration (plain -> DIS encrypted)
        resend_row = db.query(PanelSetting).filter_by(key="resend_api_key").first()
        if resend_row and resend_row.value and not resend_row.value.startswith("test-enc-v1:"):
            enc = DisClient.encrypt(resend_row.value.strip(), aad="msm:settings:resend_api_key")
            db.add(PanelSetting(key="resend_api_key_encrypted", value=enc))
            resend_row.value = ""
            migrated += 1

        # 6. Users: E-Mail-Verschluesselung (plain -> DIS encrypted + hash)
        for user in db.query(User).filter(User.email_encrypted.is_(None)).all():
            if user.email_plain:
                user.email = user.email_plain  # Setter verschluesselt + hasht
                migrated += 1

        # 7. OAuthUserLink subject hashing and profile details encryption
        # Idempotent + kollisionsresistent: wenn zwei Links dieselbe
        # (provider_id, subject)-Identitaet auf verschiedene User mappen,
        # gewinnt der bereits migrierte Link; das stale Duplikat wird entfernt.
        from models.oauth_user_link import OAuthUserLink

        existing_hashes = {
            (l.provider_id, l.subject)
            for l in db.query(OAuthUserLink).all()
            if l.subject and len(l.subject) == 64
        }
        new_hashes: set[tuple[int, str]] = set()

        for link in db.query(OAuthUserLink).all():
            if link.subject and len(link.subject) < 64:
                new_subject = OAuthUserLink._hash_subject(link.subject)
                key = (link.provider_id, new_subject)
                if key in existing_hashes or key in new_hashes:
                    print(
                        f"[DIS Migration] WARN: Duplicate OAuth-Link entfernt "
                        f"(id={link.id}, provider_id={link.provider_id}, "
                        f"user_id={link.user_id}) — Identitaet bereits "
                        f"mit anderem User verlinkt."
                    )
                    db.delete(link)
                    skipped += 1
                    continue
                link.subject = new_subject
                new_hashes.add(key)
                migrated += 1
            if link.email_at_link_plain and not link.email_at_link_encrypted:
                link.email_at_link = link.email_at_link_plain
                migrated += 1
            if link.username_at_link_plain and not link.username_at_link_encrypted:
                link.username_at_link = link.username_at_link_plain
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
    from database import Base
    Base.metadata.create_all(bind=engine)

    # Check sidecar is running
    if not DisClient.health_check():
        print("[DIS Migration] ERROR: DIS Sidecar not reachable. Start it first.", file=sys.stderr)
        sys.exit(1)

    migrate()
