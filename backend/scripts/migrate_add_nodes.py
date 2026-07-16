import os
import sys
import secrets
from pathlib import Path
from sqlalchemy import text

# Füge das backend-Verzeichnis zum PYTHONPATH hinzu, damit Module gefunden werden
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from database import engine, SessionLocal, Base
from models import Node
from services.dis_client import DisClient

def run_migration():
    print("Starte Phase-0 Migration: Nodes...")

    # Stelle sicher, dass die Tabellen existieren
    Base.metadata.create_all(bind=engine)

    # Prüfe ob DIS erreichbar ist, da wir verschlüsseln müssen
    if not DisClient.health_check():
        print("FEHLER: DIS Sidecar ist nicht erreichbar. Bitte starte den Sidecar.")
        sys.exit(1)

    db = SessionLocal()
    try:
        # Füge node_id Spalte zu servers Tabelle hinzu, falls sie nicht existiert
        from sqlalchemy import inspect
        inspector = inspect(engine)
        if 'servers' in inspector.get_table_names():
            cols = [c['name'] for c in inspector.get_columns('servers')]
            if 'node_id' not in cols:
                print("Füge node_id Spalte zu servers Tabelle hinzu...")
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE servers ADD COLUMN node_id INTEGER REFERENCES nodes(id)"))

        # Prüfen, ob der Default-Node bereits existiert
        default_node = db.query(Node).filter(Node.is_local == True).first()

        if not default_node:
            print("Erstelle Default-Node (localhost)...")
            raw_token = secrets.token_urlsafe(32)
            encrypted_token = DisClient.encrypt(raw_token, aad="msm:node:auth_token")

            default_node = Node(
                name="Local",
                host="http://127.0.0.1:9000",
                auth_token_enc=encrypted_token,
                is_local=True,
                status="unknown",
            )
            db.add(default_node)
            db.commit()
            db.refresh(default_node)
            print(f"Default-Node erstellt mit ID: {default_node.id}")
            print(f"Generierter Agent-Token: {raw_token}")
            print("BEWAHREN SIE DIESEN TOKEN AUF! Er wird für die Installation des lokalen Agenten in Phase 1 benötigt.")
        else:
            print(f"Default-Node existiert bereits (ID: {default_node.id})")

        # Zuweisen aller bestehenden Server zum Default-Node
        print("Weise bestehenden Servern den Default-Node zu...")
        result = db.execute(
            text("UPDATE servers SET node_id = :nid WHERE node_id IS NULL"),
            {"nid": default_node.id}
        )
        db.commit()

        if result.rowcount > 0:
            print(f"{result.rowcount} Server erfolgreich migriert!")
        else:
            print("Keine Server mussten migriert werden.")

        print("Phase-0 Migration erfolgreich abgeschlossen.")

    except Exception as e:
        print(f"FEHLER während der Migration: {e}")
        db.rollback()
        sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    run_migration()
