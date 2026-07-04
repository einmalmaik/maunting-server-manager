import base64
import hashlib
import sys
import pytest
from cryptography.fernet import Fernet

from config import settings
from database import SessionLocal
from models import User, OAuthProvider, PostgresDatabase, PanelSetting, Server
from scripts.migrate_to_dis import migrate, _old_fernet, _is_fernet
from services.dis_client import DisClient, DisDecryptionError


def test_old_fernet_reconstruction():
    """Testet, ob die Fernet-Rekonstruktion mit Base64 Urlsafe-Encoding korrekt arbeitet."""
    # settings.secret_key ist in conftest.py auf "test-secret-key-32-chars-long!!!" gesetzt.
    fernet = _old_fernet()
    plaintext = "my-secret-data"
    
    # Manuelle korrekte Generierung des Fernet-Schlüssels
    digest = hashlib.sha256(settings.secret_key.encode()).digest()
    key = base64.urlsafe_b64encode(digest)
    f_ref = Fernet(key)
    
    token = f_ref.encrypt(plaintext.encode())
    assert fernet.decrypt(token).decode() == plaintext


def test_is_fernet_check():
    """Testet die Erkennung von Fernet-Tokens am gAAAAA-Präfix."""
    fernet = _old_fernet()
    token = fernet.encrypt(b"test").decode()
    assert _is_fernet(token) is True
    assert _is_fernet("not-a-fernet-token") is False
    assert _is_fernet("") is False


def test_migrate_all_secrets(monkeypatch):
    """Testet die Migration aller Krypto-Werte von Fernet zu DIS."""
    db = SessionLocal()
    try:
        # 1. Fernet-Schlüssel vorbereiten
        fernet = _old_fernet()
        
        # 2. Legacy-Daten in DB schreiben
        # Server anlegen für FK-Beziehung von PostgresDatabase
        server = Server(
            name="test-server",
            game_type="dayz",
            install_dir="/opt/msm/servers/test",
        )
        db.add(server)
        db.flush()
        
        # a) User 2FA Secret (Fernet) und email_plain (Plaintext)
        user = User(
            username="testuser",
            password_hash="some-hash",
            two_factor_secret_encrypted=fernet.encrypt(b"secrettotp").decode(),
            email_plain="user@example.com",
            email_encrypted=None,
        )
        db.add(user)
        db.flush()  # We need user.id for AAD
        
        # b) OAuth provider (Fernet)
        provider = OAuthProvider(
            slug="github",
            name="GitHub",
            preset="github",
            client_id="gh-client-id",
            client_secret_encrypted=fernet.encrypt(b"github-client-secret").decode(),
        )
        db.add(provider)
        
        # c) PostgresDatabase owner password (Fernet)
        pg_db = PostgresDatabase(
            server_id=server.id,
            name="mydb",
            owner_role="myowner",
            owner_password_encrypted=fernet.encrypt(b"postgrespassword").decode(),
        )
        db.add(pg_db)
        
        # d) PanelSettings: Steam password, Postgres admin password (Fernet), GitHub Token (Plaintext)
        setting_steam = PanelSetting(
            key="steam_account_password_enc",
            value=fernet.encrypt(b"steampass").decode(),
        )
        setting_pg_admin = PanelSetting(
            key="managed_postgres.admin_password_encrypted",
            value=fernet.encrypt(b"pgadminpass").decode(),
        )
        setting_github = PanelSetting(
            key="github_clone_token",
            value="github_pat_12345",
        )
        db.add_all([setting_steam, setting_pg_admin, setting_github])
        db.commit()

        # 3. Migration ausführen
        migrate()

        # 4. Verifikation nach Migration (frische Session laden)
        db.close()
        db = SessionLocal()

        # a) User check
        user_migrated = db.query(User).filter_by(username="testuser").one()
        # 2FA Secret must be DIS-encrypted
        assert user_migrated.two_factor_secret_encrypted.startswith("test-enc-v1:")
        # Verify it has correct AAD (msm:user:{user.id}:2fa)
        plaintext_2fa = DisClient.decrypt(
            user_migrated.two_factor_secret_encrypted,
            aad=f"msm:user:{user_migrated.id}:2fa"
        )
        assert plaintext_2fa == "secrettotp"
        
        # E-Mail must be DIS-encrypted and email_plain must contain the hash
        assert user_migrated.email_encrypted.startswith("test-enc-v1:")
        assert user_migrated.email_hash == User._email_hash("user@example.com")
        assert user_migrated.email_plain == user_migrated.email_hash
        assert user_migrated.email == "user@example.com"

        # b) OAuth check
        provider_migrated = db.query(OAuthProvider).filter_by(name="GitHub").one()
        assert provider_migrated.client_secret_encrypted.startswith("test-enc-v1:")
        plaintext_oauth = DisClient.decrypt(
            provider_migrated.client_secret_encrypted,
            aad="msm:oauth:secret"
        )
        assert plaintext_oauth == "github-client-secret"

        # c) Postgres Database check
        pgdb_migrated = db.query(PostgresDatabase).filter_by(name="mydb").one()
        assert pgdb_migrated.owner_password_encrypted.startswith("test-enc-v1:")
        plaintext_pg = DisClient.decrypt(
            pgdb_migrated.owner_password_encrypted,
            aad="msm:pg:db:owner"
        )
        assert plaintext_pg == "postgrespassword"

        # d) PanelSettings check
        steam_mig = db.query(PanelSetting).filter_by(key="steam_account_password_enc").one()
        assert steam_mig.value.startswith("test-enc-v1:")
        assert DisClient.decrypt(steam_mig.value, aad="msm:steam:password") == "steampass"

        pgadmin_mig = db.query(PanelSetting).filter_by(key="managed_postgres.admin_password_encrypted").one()
        assert pgadmin_mig.value.startswith("test-enc-v1:")
        assert DisClient.decrypt(pgadmin_mig.value, aad="msm:pg:admin") == "pgadminpass"

        # Old plain-text github token is cleared
        github_plain = db.query(PanelSetting).filter_by(key="github_clone_token").one()
        assert github_plain.value == ""
        # New encrypted token setting is created
        github_enc = db.query(PanelSetting).filter_by(key="github_clone_token_enc").one()
        assert github_enc.value.startswith("test-enc-v1:")
        assert DisClient.decrypt(github_enc.value, aad="msm:github:token") == "github_pat_12345"

    finally:
        db.close()


def test_migrate_idempotency():
    """Testet, ob die Migration idempotent ist (zweiter Durchlauf ändert nichts)."""
    db = SessionLocal()
    try:
        fernet = _old_fernet()
        user = User(
            username="idempotent-user",
            password_hash="some-hash",
            two_factor_secret_encrypted=fernet.encrypt(b"secret-totp").decode(),
            email_plain="idem@example.com",
        )
        db.add(user)
        db.commit()

        # Erstes Mal migrieren
        migrate()

        db.close()
        db = SessionLocal()
        user_mig = db.query(User).filter_by(username="idempotent-user").one()
        first_enc_2fa = user_mig.two_factor_secret_encrypted
        first_enc_email = user_mig.email_encrypted
        
        # Zweites Mal migrieren
        migrate()

        db.close()
        db = SessionLocal()
        user_mig_2 = db.query(User).filter_by(username="idempotent-user").one()
        
        # Werte dürfen sich nicht verändert haben (kein Doppel-Encrypt o.Ä.)
        assert user_mig_2.two_factor_secret_encrypted == first_enc_2fa
        assert user_mig_2.email_encrypted == first_enc_email
        assert user_mig_2.email == "idem@example.com"
        
    finally:
        db.close()


def test_migrate_error_rolls_back_transaction(monkeypatch):
    """Testet, ob bei Fehlern (z. B. fehlerhaftes Fernet-Token) ein Rollback durchgeführt wird."""
    db = SessionLocal()
    try:
        # Mock sys.exit, um ein Beenden des Testprozesses zu verhindern
        exit_called = False
        def mock_exit(code):
            nonlocal exit_called
            exit_called = True
            raise RuntimeError("sys.exit was called")
        monkeypatch.setattr(sys, "exit", mock_exit)

        # 1. Valide Daten einfügen
        fernet = _old_fernet()
        valid_user = User(
            username="valid-user",
            password_hash="hash",
            two_factor_secret_encrypted=fernet.encrypt(b"valid").decode(),
        )
        db.add(valid_user)
        
        # 2. Defekte Fernet-Daten einfügen (löst InvalidToken aus)
        corrupt_user = User(
            username="corrupt-user",
            password_hash="hash",
            two_factor_secret_encrypted="gAAAAA-invalid-fernet-token-data-!!!",
        )
        db.add(corrupt_user)
        db.commit()

        # 3. Migration starten -> Muss fehlschlagen
        with pytest.raises(RuntimeError, match="sys.exit was called"):
            migrate()

        assert exit_called is True

        # 4. Verifizieren, dass Transaktion zurückgerollt wurde (kein Wert wurde in DB geändert)
        db.close()
        db = SessionLocal()
        
        u_valid = db.query(User).filter_by(username="valid-user").one()
        # Darf nicht verschlüsselt sein, da rollback() stattfand
        assert _is_fernet(u_valid.two_factor_secret_encrypted) is True
        assert not u_valid.two_factor_secret_encrypted.startswith("test-enc-v1:")

    finally:
        db.close()


def test_user_email_property_pre_migration():
    """Testet, dass vor der Migration (email_plain gesetzt, email_encrypted None) die E-Mail im Klartext zurückgegeben wird."""
    user = User(
        username="pre-user",
        email_plain="pre@example.com",
        email_encrypted=None,
    )
    assert user.email == "pre@example.com"


def test_user_email_property_post_migration():
    """Testet, dass nach der Migration die E-Mail korrekt entschlüsselt wird."""
    user = User(username="post-user")
    # Setzen über den Setter (verschlüsselt und hasht automatisch)
    user.email = "post@example.com"
    
    assert user.email_encrypted.startswith("test-enc-v1:")
    assert user.email_plain == User._email_hash("post@example.com")
    assert user.email == "post@example.com"


def test_user_email_property_decrypt_failure_raises_error():
    """Testet, dass bei einem Fehler beim Entschlüsseln (z.B. AAD-Mismatch) der Fehler eskaliert und kein Fallback auf den Hash erfolgt."""
    user = User(username="corrupt-email-user")
    # Direktes Setzen mit ungültigem/anderem AAD-Wert in der Verschlüsselung
    user.email_encrypted = DisClient.encrypt("corrupt@example.com", aad="wrong:aad:context")
    user.email_plain = User._email_hash("corrupt@example.com")
    user.email_hash = user.email_plain
    
    # Der Getter muss fehlschlagen und darf NICHT den Hash aus email_plain zurückgeben
    with pytest.raises(DisDecryptionError):
        _ = user.email


def test_user_email_property_inconsistent_state_raises_error():
    """Testet, dass wenn email_encrypted None ist, aber email_plain bereits ein Hashwert ist, ein Fehler geworfen wird."""
    user = User(
        username="inconsistent-user",
        email_plain=User._email_hash("test@example.com"),
        email_encrypted=None,
    )
    
    with pytest.raises(DisDecryptionError, match="Inconsistent database state"):
        _ = user.email
