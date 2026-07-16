import os
import sys
from pathlib import Path

# Füge das backend-Verzeichnis zum PYTHONPATH hinzu
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from database import SessionLocal
from services.auth_service import AuthService
from services.dis_client import DisClient

def create_dev_user():
    print("Erstelle dev Admin/Owner Benutzer...")

    if not DisClient.health_check():
        print("FEHLER: DIS Sidecar ist nicht erreichbar. Bitte starte zuerst die dev Umgebung (z.B. start-dev.bat).")
        sys.exit(1)

    db = SessionLocal()
    try:
        if AuthService.is_owner_exists(db):
            print("Es existiert bereits ein Owner-Benutzer in der Datenbank.")
            owner = db.query(AuthService.create_owner.__annotations__['return']).filter_by(is_owner=True).first()
            if owner:
                print(f"Bestehender Owner: Username={owner.username}, Email={owner.email_plain}")
            return

        username = "admin"
        email = "admin@localhost.de"
        password = "admin"

        print(f"Erstelle Owner: Username={username}, Password={password}")
        owner = AuthService.create_owner(db, username, email, password)
        # Stelle sicher, dass E-Mail verifiziert ist
        owner.email_verified = True
        db.commit()

        print("Owner-Benutzer erfolgreich erstellt!")
        print("Login-Daten:")
        print(f"  Username: {username}")
        print(f"  Passwort: {password}")

    except Exception as e:
        print(f"FEHLER beim Erstellen des Users: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    create_dev_user()
