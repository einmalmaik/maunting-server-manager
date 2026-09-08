from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any
from sqlalchemy import func
from sqlalchemy.orm import Session

from models import (
    User,
    UserAchievement,
    UserActivityTime,
    UserFriend,
    Server,
    Backup,
    AiConversation,
    ServerPermission,
)
from services.sync_event_service import SyncEventService

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


ACHIEVEMENTS_CATALOG: list[dict[str, Any]] = [
    # =========================================================================
    # 1. STARTER & KONTO (1 - 15)
    # =========================================================================
    {
        "id": "starter_first_step",
        "title": "Erster Schritt",
        "description": "Erste erfolgreiche Anmeldung im MSM Control Panel.",
        "category": "starter",
        "points": 10,
        "icon": "award",
    },
    {
        "id": "starter_security_first",
        "title": "Sicherheitsbewusst",
        "description": "Zwei-Faktor-Authentisierung (2FA) oder biometrischen Tresor aktiviert.",
        "category": "starter",
        "points": 20,
        "icon": "shield-check",
    },
    {
        "id": "starter_profile_setup",
        "title": "Digitale Identität",
        "description": "Profilbild angepasst und Benutzerkonto personalisiert.",
        "category": "starter",
        "points": 10,
        "icon": "user",
    },
    {
        "id": "starter_timezone_set",
        "title": "Pünktlich wie die Uhr",
        "description": "Eigene Zeitzone für exakte Server- und Kalenderzeiten festgelegt.",
        "category": "starter",
        "points": 10,
        "icon": "clock",
    },
    {
        "id": "starter_biometrics",
        "title": "Fingerabdruck des Vertrauens",
        "description": "Biometrische Schnellanmeldung in der Desktop-App eingerichtet.",
        "category": "starter",
        "points": 25,
        "icon": "fingerprint",
    },
    {
        "id": "starter_autolock",
        "title": "Wachsamer Wächter",
        "description": "Automatisches Sperren bei Abwesenheit oder Fensterverlust aktiviert.",
        "category": "starter",
        "points": 15,
        "icon": "lock",
    },
    {
        "id": "starter_privacy_pledge",
        "title": "Schutz braucht Vertrauen",
        "description": "Datenschutzerklärung und Betreiber-Impressum im Panel eingesehen.",
        "category": "starter",
        "points": 10,
        "icon": "file-text",
    },
    {
        "id": "starter_dark_mode",
        "title": "Nachteule",
        "description": "Design-DNA und Farbschema an deine Vorlieben angepasst.",
        "category": "starter",
        "points": 10,
        "icon": "moon",
    },
    {
        "id": "starter_hotkeys",
        "title": "Tastatur-Virtuose",
        "description": "Globale Desktop-Hotkeys für Sprache und Schnellzugriff konfiguriert.",
        "category": "starter",
        "points": 15,
        "icon": "keyboard",
    },
    {
        "id": "starter_vault_master",
        "title": "Schlüsseltresor",
        "description": "Ersten sicheren Eintrag im verschlüsselten Vault abgelegt.",
        "category": "starter",
        "points": 20,
        "icon": "key",
    },
    {
        "id": "starter_audio_tuned",
        "title": "Klangmeister",
        "description": "Mikrofon-Eingangspegel und Signalverarbeitung erfolgreich kalibriert.",
        "category": "starter",
        "points": 15,
        "icon": "volume-2",
    },
    {
        "id": "starter_audio_ducking",
        "title": "Sprechfunk-Disziplin",
        "description": "Automatische Lautstärke-Absenkung (Audio Ducking) im Desktop aktiviert.",
        "category": "starter",
        "points": 15,
        "icon": "sliders",
    },
    {
        "id": "starter_wakeword_tuned",
        "title": "Auf ein Wort",
        "description": "Eigenes Aktivierungswort (Wake-Word) für die Sprachsteuerung trainiert.",
        "category": "starter",
        "points": 25,
        "icon": "mic",
    },
    {
        "id": "starter_session_hygiene",
        "title": "Sitzungs-Hygiene",
        "description": "Aktive Sitzungen überprüft und unbenutzte Tokens abgemeldet.",
        "category": "starter",
        "points": 15,
        "icon": "check-circle",
    },
    {
        "id": "starter_onboarding_done",
        "title": "Bereit zum Einsatz",
        "description": "Alle Einstiegsschritte abgeschlossen und das Panel voll eingerichtet.",
        "category": "starter",
        "points": 30,
        "icon": "compass",
    },

    # =========================================================================
    # 2. SERVER-ADMINISTRATION & INFRASTRUKTUR (16 - 35)
    # =========================================================================
    {
        "id": "server_architect",
        "title": "Weltenbauer",
        "description": "Den ersten Spielserver erfolgreich aufgesetzt.",
        "category": "servers",
        "points": 25,
        "icon": "server",
    },
    {
        "id": "server_fleet_admiral",
        "title": "Flottenadmiral",
        "description": "Mindestens 3 Server gleichzeitig verwaltet.",
        "category": "servers",
        "points": 50,
        "icon": "layers",
    },
    {
        "id": "server_power_cycle",
        "title": "Neu gestartet",
        "description": "Einen Server sauber gestoppt und wieder hochgefahren.",
        "category": "servers",
        "points": 15,
        "icon": "refresh-cw",
    },
    {
        "id": "server_port_forwarder",
        "title": "Pfortenöffner",
        "description": "Server-Ports, TCP/UDP-Freigaben und Port-Rollen eingerichtet.",
        "category": "servers",
        "points": 20,
        "icon": "network",
    },
    {
        "id": "terminal_commander",
        "title": "Kommandozentrale",
        "description": "Administrative Befehle oder Server-Aktionen aktiv ausgeführt.",
        "category": "servers",
        "points": 35,
        "icon": "terminal",
    },
    {
        "id": "server_blueprint_deployer",
        "title": "Blaupausen-Ingenieur",
        "description": "Einen Server basierend auf einem Blueprint automatisiert ausgerollt.",
        "category": "servers",
        "points": 30,
        "icon": "cpu",
    },
    {
        "id": "server_custom_blueprint",
        "title": "Rezept-Erfinder",
        "description": "Eine eigene Server-Vorlage (Blueprint) erstellt und gespeichert.",
        "category": "servers",
        "points": 40,
        "icon": "file-code",
    },
    {
        "id": "server_modpack_installer",
        "title": "Mod-Enthusiast",
        "description": "Mods oder ein Modpack über den Katalog installiert.",
        "category": "servers",
        "points": 25,
        "icon": "package",
    },
    {
        "id": "server_file_manager",
        "title": "Datei-Navigator",
        "description": "Serverdateien über den Web-Dateimanager inspiziert und bearbeitet.",
        "category": "servers",
        "points": 20,
        "icon": "folder",
    },
    {
        "id": "server_sftp_connected",
        "title": "Sicherer Datentransfer",
        "description": "SFTP-Zugangsdaten erstellt und verschlüsselt verbunden.",
        "category": "servers",
        "points": 25,
        "icon": "upload-cloud",
    },
    {
        "id": "server_log_analyzer",
        "title": "Logbuch-Detektiv",
        "description": "Live-Serverlogs gefiltert und nach Ausnahmen durchsucht.",
        "category": "servers",
        "points": 20,
        "icon": "file-text",
    },
    {
        "id": "server_resource_watcher",
        "title": "Leistungs-Analyst",
        "description": "CPU-, RAM- und Speicherauslastung im Detail-Dashboard überwacht.",
        "category": "servers",
        "points": 15,
        "icon": "activity",
    },
    {
        "id": "server_node_connector",
        "title": "Node-Commander",
        "description": "Einen externen Daemon-Node erfolgreich im Panel registriert.",
        "category": "servers",
        "points": 50,
        "icon": "hard-drive",
    },
    {
        "id": "server_high_availability",
        "title": "Dauerläufer",
        "description": "Einen Spielserver über 72 Stunden ohne Absturz betrieben.",
        "category": "servers",
        "points": 40,
        "icon": "shield",
    },
    {
        "id": "server_auto_restart",
        "title": "Unbeirrbar",
        "description": "Crash-Detection mit automatischem Neustart für einen Server konfiguriert.",
        "category": "servers",
        "points": 25,
        "icon": "zap",
    },
    {
        "id": "server_multi_env",
        "title": "Umgebungs-Spezialist",
        "description": "Benutzerdefinierte Umgebungsvariablen (ENV) in einen Server eingespeist.",
        "category": "servers",
        "points": 20,
        "icon": "sliders",
    },
    {
        "id": "server_player_moderator",
        "title": "Spieler-Hüter",
        "description": "Whitelist, Ban-Listen oder Ops über die Serververwaltung gepflegt.",
        "category": "servers",
        "points": 20,
        "icon": "users",
    },
    {
        "id": "server_tag_organizer",
        "title": "Struktur-Genie",
        "description": "Server mit Farb-Tags und Kategorien übersichtlich organisiert.",
        "category": "servers",
        "points": 15,
        "icon": "bookmark",
    },
    {
        "id": "server_bulk_operator",
        "title": "Massen-Dirigent",
        "description": "Mehrere Server gleichzeitig über Gruppenaktionen angesteuert.",
        "category": "servers",
        "points": 35,
        "icon": "layers",
    },
    {
        "id": "server_zero_downtime",
        "title": "Unterbrechungsfrei",
        "description": "Wartungsmodus für Wartungsarbeiten ohne Datenverlust aktiviert.",
        "category": "servers",
        "points": 30,
        "icon": "tool",
    },

    # =========================================================================
    # 3. BACKUP, SICHERHEIT & DESASTER-RECOVERY (36 - 50)
    # =========================================================================
    {
        "id": "backup_guardian",
        "title": "Eiserne Reserve",
        "description": "Ein vollständiges Server-Backup erfolgreich erstellt und gesichert.",
        "category": "backup",
        "points": 30,
        "icon": "archive",
    },
    {
        "id": "backup_cron_master",
        "title": "Automatischer Schutz",
        "description": "Einen wiederkehrenden Backup-Zeitplan (Cron) eingerichtet.",
        "category": "backup",
        "points": 30,
        "icon": "calendar",
    },
    {
        "id": "backup_restorer",
        "title": "Zeitreisender",
        "description": "Einen Server erfolgreich aus einem früheren Sicherungspunkt wiederhergestellt.",
        "category": "backup",
        "points": 40,
        "icon": "rotate-ccw",
    },
    {
        "id": "backup_lock_champion",
        "title": "Unlöschbar",
        "description": "Ein kritisches Backup gegen automatisches Löschen gesperrt.",
        "category": "backup",
        "points": 20,
        "icon": "lock",
    },
    {
        "id": "backup_cloud_offloader",
        "title": "Wolken-Depot",
        "description": "Ein Server-Backup in externen S3/Cloud-Speicher übertragen.",
        "category": "backup",
        "points": 45,
        "icon": "cloud",
    },
    {
        "id": "backup_panel_snapshot",
        "title": "Gesamtsicherung",
        "description": "Ein vollständiges Panel-Datenbank-Backup erzeugt.",
        "category": "backup",
        "points": 35,
        "icon": "database",
    },
    {
        "id": "backup_checksum_verifier",
        "title": "Bitgenau geprüft",
        "description": "Integritätsprüfung (SHA-256 Checksumme) eines Backups erfolgreich validiert.",
        "category": "backup",
        "points": 25,
        "icon": "check-circle",
    },
    {
        "id": "backup_retention_cleaner",
        "title": "Saubere Festplatte",
        "description": "Aufbewahrungsrichtlinien konfiguriert und veraltete Stände bereinigt.",
        "category": "backup",
        "points": 20,
        "icon": "trash-2",
    },
    {
        "id": "security_audit_inspector",
        "title": "Audit-Wachhund",
        "description": "Sicherheits- und Audit-Protokolle nach kritischen Ereignissen gefiltert.",
        "category": "security",
        "points": 25,
        "icon": "eye",
    },
    {
        "id": "security_api_key_creator",
        "title": "Maschinen-Schlüssel",
        "description": "Einen Scoped API-Key mit minimalen Rechten für Automation erstellt.",
        "category": "security",
        "points": 25,
        "icon": "key",
    },
    {
        "id": "security_ip_allowlist",
        "title": "Burggraben",
        "description": "IP-Zugriffsbeschränkungen für sensible Endpunkte eingerichtet.",
        "category": "security",
        "points": 30,
        "icon": "shield",
    },
    {
        "id": "security_role_granularity",
        "title": "Präzise Privilegien",
        "description": "Feingranulare Rollenrechte nach dem Least-Privilege-Prinzip vergeben.",
        "category": "security",
        "points": 30,
        "icon": "user-check",
    },
    {
        "id": "security_recovery_test",
        "title": "Notfallprobe",
        "description": "Wiederherstellungs-App im Trockenlauf erfolgreich getestet.",
        "category": "security",
        "points": 40,
        "icon": "life-buoy",
    },
    {
        "id": "security_password_rotation",
        "title": "Schlüsselwechsel",
        "description": "Passwort oder Tresorschlüssel turnusmäßig aktualisiert.",
        "category": "security",
        "points": 20,
        "icon": "refresh-cw",
    },
    {
        "id": "security_fortress",
        "title": "Festung MSM",
        "description": "Alle Sicherheitsmodule (2FA, CSRF, Audit, Auto-Lock) gleichzeitig scharf.",
        "category": "security",
        "points": 60,
        "icon": "shield-check",
    },

    # =========================================================================
    # 4. KI, DIALOGE & AUTONOME WERKZEUGE (51 - 70)
    # =========================================================================
    {
        "id": "ai_first_contact",
        "title": "Erster Kontakt",
        "description": "Erste Unterhaltung mit dem Singra KI-Assistenten geführt.",
        "category": "ai",
        "points": 15,
        "icon": "sparkles",
    },
    {
        "id": "ai_collaborator",
        "title": "Autonomer Partner",
        "description": "Einen KI-Aktionsvorschlag geprüft und freigegeben.",
        "category": "ai",
        "points": 30,
        "icon": "bot",
    },
    {
        "id": "ai_mastermind",
        "title": "Meisterstratege",
        "description": "Intensive KI-Zusammenarbeit mit über 50 Interaktionen.",
        "category": "ai",
        "points": 75,
        "icon": "cpu",
    },
    {
        "id": "ai_voice_dialog",
        "title": "Auf gleicher Wellenlänge",
        "description": "Einen flüssigen Sprachdialog über den Voice-Bridge-Modus geführt.",
        "category": "ai",
        "points": 30,
        "icon": "mic",
    },
    {
        "id": "ai_deep_thinker",
        "title": "Tiefgründig",
        "description": "Eine komplexe Frage mit erweiterter KI-Denkstufe gelöst.",
        "category": "ai",
        "points": 25,
        "icon": "brain",
    },
    {
        "id": "ai_satellite_eye",
        "title": "Blick aus dem Orbit",
        "description": "Satellitenbilder und Geodaten mit dem 3D-Globus der KI analysiert.",
        "category": "ai",
        "points": 35,
        "icon": "globe",
    },
    {
        "id": "ai_memory_keeper",
        "title": "Elefantengedächtnis",
        "description": "Eine wichtige Information dauerhaft im KI-Gedächtnis gespeichert.",
        "category": "ai",
        "points": 20,
        "icon": "bookmark",
    },
    {
        "id": "ai_skill_user",
        "title": "Werkzeugkasten",
        "description": "Ein spezialisiertes Skill-Paket für die KI aktiviert.",
        "category": "ai",
        "points": 25,
        "icon": "tool",
    },
    {
        "id": "ai_server_medic",
        "title": "KI-Doktor",
        "description": "Einen Serverfehler durch die Diagnose des KI-Assistenten behoben.",
        "category": "ai",
        "points": 35,
        "icon": "activity",
    },
    {
        "id": "ai_calendar_assistant",
        "title": "Termin-Dirigent",
        "description": "Einen Kalendereintrag direkt per Sprach- oder Textanweisung erstellt.",
        "category": "ai",
        "points": 20,
        "icon": "calendar",
    },
    {
        "id": "ai_notes_scribe",
        "title": "Protokollant",
        "description": "Eine strukturierte Notiz über die KI verfassen lassen.",
        "category": "ai",
        "points": 20,
        "icon": "file-text",
    },
    {
        "id": "ai_proposal_denied",
        "title": "Kritischer Geist",
        "description": "Einen KI-Aktionsvorschlag nach sorgfältiger Prüfung abgelehnt.",
        "category": "ai",
        "points": 15,
        "icon": "x-circle",
    },
    {
        "id": "ai_file_analyzer",
        "title": "Dokumenten-Forscher",
        "description": "Eine angehängte Konfigurationsdatei von der KI analysieren lassen.",
        "category": "ai",
        "points": 25,
        "icon": "paperclip",
    },
    {
        "id": "ai_sightseeing_tour",
        "title": "Virtuelle Weltreise",
        "description": "Mehrere Sehenswürdigkeiten nacheinander auf dem Globus anfliegen lassen.",
        "category": "ai",
        "points": 30,
        "icon": "map-pin",
    },
    {
        "id": "ai_model_switcher",
        "title": "Modell-Kenner",
        "description": "Zwischen verschiedenen KI-Modellen für die passende Aufgabe gewechselt.",
        "category": "ai",
        "points": 20,
        "icon": "refresh-cw",
    },
    {
        "id": "ai_speech_directness",
        "title": "Auf den Punkt",
        "description": "Lautlose, floskelfreie Werkzeugausführung im Sprachmodus erlebt.",
        "category": "ai",
        "points": 20,
        "icon": "zap",
    },
    {
        "id": "ai_vision_expert",
        "title": "Scharfes Auge",
        "description": "Ein Bild über den Chat hochgeladen und von der KI auswerten lassen.",
        "category": "ai",
        "points": 25,
        "icon": "camera",
    },
    {
        "id": "ai_hundred_prompts",
        "title": "Dialog-Marathon",
        "description": "Über 100 fundierte Dialogrunden mit dem Assistenten absolviert.",
        "category": "ai",
        "points": 60,
        "icon": "message-circle",
    },
    {
        "id": "ai_autonomous_pilot",
        "title": "Autonomer Flug",
        "description": "Mehrere zusammenhängende Aktionen autonom von der KI ausführen lassen.",
        "category": "ai",
        "points": 50,
        "icon": "sparkles",
    },
    {
        "id": "ai_symbiosis",
        "title": "Perfekte Symbiose",
        "description": "Serververwaltung, Monitoring und Notizen vollständig mit KI verzahnt.",
        "category": "ai",
        "points": 80,
        "icon": "cpu",
    },

    # =========================================================================
    # 5. SOCIAL, MESSENGER & E2EE KRYPTOGRAPHIE (71 - 85)
    # =========================================================================
    {
        "id": "social_handshake",
        "title": "Netzwerker",
        "description": "Erste Freundschaftsanfrage im Social Hub bestätigt.",
        "category": "social",
        "points": 20,
        "icon": "user-plus",
    },
    {
        "id": "social_zero_knowledge",
        "title": "Kryptographischer Pakt",
        "description": "Erste Zero-Knowledge E2EE-Nachricht über DIS verschlüsselt gesendet.",
        "category": "social",
        "points": 50,
        "icon": "lock",
    },
    {
        "id": "social_group_founder",
        "title": "Gilden-Gründer",
        "description": "Eine eigene verschlüsselte Chat-Gruppe erstellt.",
        "category": "social",
        "points": 30,
        "icon": "users",
    },
    {
        "id": "social_invite_sharer",
        "title": "Gemeinschaftsbauer",
        "description": "Einen Gruppen-Einladungslink erfolgreich geteilt und neue Mitglieder begrüßt.",
        "category": "social",
        "points": 25,
        "icon": "share-2",
    },
    {
        "id": "social_role_architect",
        "title": "Rangordnung",
        "description": "Eigene Gruppenrollen und Rechte-Vorlagen konfiguriert.",
        "category": "social",
        "points": 30,
        "icon": "shield",
    },
    {
        "id": "social_status_story",
        "title": "Im Rampenlicht",
        "description": "Eine 24-Stunden Status-Story für deine Freunde geteilt.",
        "category": "social",
        "points": 20,
        "icon": "sparkles",
    },
    {
        "id": "social_voice_memo",
        "title": "Stimmabdruck",
        "description": "Eine verschlüsselte Sprachnachricht im Chat aufgenommen und versendet.",
        "category": "social",
        "points": 25,
        "icon": "mic",
    },
    {
        "id": "social_camera_moment",
        "title": "Augenblick festgehalten",
        "description": "Ein Direktfoto per Kamera im Messenger geteilt.",
        "category": "social",
        "points": 20,
        "icon": "camera",
    },
    {
        "id": "social_sticker_fun",
        "title": "Ausdrucksstark",
        "description": "Einen Sticker oder ein Emoji im E2EE-Chat gesendet.",
        "category": "social",
        "points": 15,
        "icon": "smile",
    },
    {
        "id": "social_note_share",
        "title": "Gemeinsames Wissen",
        "description": "Eine Notiz direkt in einer Gruppenunterhaltung geteilt.",
        "category": "social",
        "points": 20,
        "icon": "file-text",
    },
    {
        "id": "social_event_share",
        "title": "Verabredung",
        "description": "Einen Kalendertermin im Chat geteilt und abgestimmt.",
        "category": "social",
        "points": 20,
        "icon": "calendar",
    },
    {
        "id": "social_read_receipts",
        "title": "Gelesen und Verstanden",
        "description": "Lesebestätigungen (blaue Häkchen) in den Privatsphäre-Einstellungen gewählt.",
        "category": "social",
        "points": 15,
        "icon": "check-circle",
    },
    {
        "id": "social_rich_presence",
        "title": "Status-Update",
        "description": "Benutzerdefinierten Statustext oder Aktivitätsanzeige eingestellt.",
        "category": "social",
        "points": 15,
        "icon": "activity",
    },
    {
        "id": "social_public_ambassador",
        "title": "Offenes Buch",
        "description": "Profil auf 'Öffentlich' gestellt, um für alle Server-Admins auffindbar zu sein.",
        "category": "social",
        "points": 25,
        "icon": "globe",
    },
    {
        "id": "social_squad_leader",
        "title": "Squad Leader",
        "description": "Eine Gruppe mit mindestens 5 aktiven Mitgliedern geleitet.",
        "category": "social",
        "points": 45,
        "icon": "crown",
    },

    # =========================================================================
    # 6. TEAMS, ROLLEN & KOLLABORATION (86 - 92)
    # =========================================================================
    {
        "id": "team_member_joined",
        "title": "Teamplayer",
        "description": "Einem Team im Server Manager beigetreten.",
        "category": "team",
        "points": 20,
        "icon": "users",
    },
    {
        "id": "team_created",
        "title": "Projektgründer",
        "description": "Ein eigenes Administrations-Team ins Leben gerufen.",
        "category": "team",
        "points": 30,
        "icon": "briefcase",
    },
    {
        "id": "team_permission_delegate",
        "title": "Vertrauensbeweis",
        "description": "Serverberechtigungen sicher an ein Teammitglied delegiert.",
        "category": "team",
        "points": 25,
        "icon": "user-check",
    },
    {
        "id": "team_shared_server",
        "title": "Gemeinsame Welten",
        "description": "Einen Server für das gesamte Team freigegeben.",
        "category": "team",
        "points": 30,
        "icon": "server",
    },
    {
        "id": "team_role_assigned",
        "title": "Aufgabenteilung",
        "description": "Rollen (Admin, Moderator, Operator) innerhalb des Teams verteilt.",
        "category": "team",
        "points": 20,
        "icon": "shield",
    },
    {
        "id": "team_five_collaborators",
        "title": "Starke Allianz",
        "description": "Erfolgreich mit mindestens 5 Teammitgliedern zusammengearbeitet.",
        "category": "team",
        "points": 45,
        "icon": "users",
    },
    {
        "id": "team_harmony",
        "title": "Gleichklang",
        "description": "Gemeinsam Server aktualisiert, Backups erstellt und Chats synchronisiert.",
        "category": "team",
        "points": 50,
        "icon": "award",
    },

    # =========================================================================
    # 7. AKTIVE SYSTEM- & SPIELZEIT („NUTZUNGSZEIT“) (93 - 100)
    # =========================================================================
    {
        "id": "activity_hour_1",
        "title": "Eingearbeitet",
        "description": "Mindestens 1 Stunde aktive Systemzeit im Panel verbracht.",
        "category": "activity",
        "points": 25,
        "icon": "clock",
    },
    {
        "id": "activity_hour_5",
        "title": "Aufmerksamer Operator",
        "description": "5 Stunden aktive Interaktion mit Servern, KI und Messenger.",
        "category": "activity",
        "points": 50,
        "icon": "timer",
    },
    {
        "id": "activity_hour_10",
        "title": "Erfahrener Verwalter",
        "description": "10 Stunden aktive Interaktionszeit mit Servern, KI und Verwaltung.",
        "category": "activity",
        "points": 75,
        "icon": "timer",
    },
    {
        "id": "activity_hour_25",
        "title": "Infrastruktur-Veteran",
        "description": "25 Stunden engagierte Verwaltungs- und Dialogzeit erreicht.",
        "category": "activity",
        "points": 100,
        "icon": "award",
    },
    {
        "id": "activity_hour_50",
        "title": "Prestige Administrator",
        "description": "Über 50 Stunden hochaktive Administrations- und Dialogzeit.",
        "category": "activity",
        "points": 150,
        "icon": "crown",
    },
    {
        "id": "activity_hour_100",
        "title": "Hundert-Stunden-Pionier",
        "description": "100 Stunden verlässlicher Einsatz im Herzen des Systems.",
        "category": "activity",
        "points": 200,
        "icon": "flame",
    },
    {
        "id": "activity_hour_250",
        "title": "System-Legende",
        "description": "250 Stunden meisterhafte Serverbeherrschung und Teamführung.",
        "category": "activity",
        "points": 300,
        "icon": "trophy",
    },
    {
        "id": "activity_hour_500",
        "title": "Unsterbliche Eminenz",
        "description": "500 Stunden unerschütterliche Hingabe für Sicherheit und Performance.",
        "category": "activity",
        "points": 500,
        "icon": "crown",
    },
]

ACHIEVEMENTS_BY_ID = {a["id"]: a for a in ACHIEVEMENTS_CATALOG}


class AchievementService:
    """Verwaltet Meilensteine, dynamische Seltenheit und aktive Nutzungszeit."""

    @classmethod
    def get_catalog(cls) -> list[dict[str, Any]]:
        return list(ACHIEVEMENTS_CATALOG)

    @classmethod
    def get_rarity_stats(cls, db: Session) -> dict[str, dict[str, Any]]:
        """Ermittelt die dynamische Seltenheit für alle Errungenschaften live aus der Datenbank."""
        total_users = db.query(func.count(User.id)).filter(User.is_active == True).scalar() or 1
        counts_raw = (
            db.query(UserAchievement.achievement_id, func.count(UserAchievement.id))
            .group_by(UserAchievement.achievement_id)
            .all()
        )
        counts = {aid: count for aid, count in counts_raw}

        result = {}
        for ach in ACHIEVEMENTS_CATALOG:
            aid = ach["id"]
            unlocked_count = counts.get(aid, 0)
            percentage = round((unlocked_count / total_users) * 100, 1)
            # Rarity Tiers
            if percentage > 50:
                tier = "common"
            elif percentage > 20:
                tier = "rare"
            elif percentage > 5:
                tier = "epic"
            else:
                tier = "legendary"

            result[aid] = {
                "unlocked_count": unlocked_count,
                "total_users": total_users,
                "percentage": percentage,
                "tier": tier,
                "rarity_text": f"Nur von {percentage}% aller Nutzer freigeschaltet",
            }
        return result

    @classmethod
    def get_user_achievements(cls, db: Session, user_id: int) -> list[dict[str, Any]]:
        """Liefert alle Errungenschaften inklusive Freischaltstatus und Rarity für einen Benutzer."""
        cls.check_automatic_achievements(db, user_id)
        rarity_map = cls.get_rarity_stats(db)
        unlocked_rows = (
            db.query(UserAchievement)
            .filter(UserAchievement.user_id == user_id)
            .all()
        )
        unlocked_map = {row.achievement_id: row.unlocked_at for row in unlocked_rows}

        results = []
        for ach in ACHIEVEMENTS_CATALOG:
            aid = ach["id"]
            rarity = rarity_map.get(
                aid,
                {"percentage": 0.0, "tier": "common", "rarity_text": "Noch nicht freigeschaltet"},
            )
            is_unlocked = aid in unlocked_map
            results.append({
                "id": aid,
                "title": ach["title"],
                "description": ach["description"],
                "category": ach["category"],
                "points": ach["points"],
                "icon": ach["icon"],
                "unlocked": is_unlocked,
                "unlocked_at": unlocked_map.get(aid),
                "global_unlocked_percentage": rarity["percentage"],
                "rarity_tier": rarity["tier"],
                "rarity_text": rarity["rarity_text"],
            })
        return results

    @classmethod
    def get_achievements_overview(cls, db: Session, user_id: int) -> dict[str, Any]:
        """Liefert die strukturierte Übersicht aller Errungenschaften samt Zähler und Prestige-Score."""
        achievements = cls.get_user_achievements(db, user_id)
        unlocked_count = sum(1 for a in achievements if a["unlocked"])
        earned_points = sum(a["points"] for a in achievements if a["unlocked"])
        return {
            "achievements": achievements,
            "total_unlocked": unlocked_count,
            "total_available": len(ACHIEVEMENTS_CATALOG),
            "prestige_score": earned_points,
        }

    @classmethod
    def get_user_stats(cls, db: Session, user_id: int) -> dict[str, Any]:
        """Ermittelt Gesamtpunkte, freigeschaltete Meilensteine und aktive Nutzungszeiten."""
        achievements = cls.get_user_achievements(db, user_id)
        total_points = sum(a["points"] for a in ACHIEVEMENTS_CATALOG)
        earned_points = sum(a["points"] for a in achievements if a["unlocked"])
        unlocked_count = sum(1 for a in achievements if a["unlocked"])

        # Nutzungszeiten
        times = (
            db.query(UserActivityTime)
            .filter(UserActivityTime.user_id == user_id)
            .all()
        )
        time_by_category = {t.category: t.seconds for t in times}
        total_seconds = sum(time_by_category.values())

        return {
            "total_achievements": len(ACHIEVEMENTS_CATALOG),
            "unlocked_achievements": unlocked_count,
            "total_points": total_points,
            "earned_points": earned_points,
            "active_time_seconds": total_seconds,
            "active_time_by_category": time_by_category,
            # Frontend aliases
            "achievements_unlocked": unlocked_count,
            "total_activity_seconds": total_seconds,
            "categories": time_by_category,
        }

    @classmethod
    def unlock_achievement(
        cls, db: Session, user_id: int, achievement_id: str, commit: bool = True
    ) -> bool:
        """Schaltet eine Errungenschaft frei, falls noch nicht errungen.

        Geteilter Pool: Jede Errungenschaft kann systemweit genau einmal errungen werden.
        """
        if achievement_id not in ACHIEVEMENTS_BY_ID:
            logger.warning("Unbekanntes Achievement: %s", achievement_id)
            return False

        existing = (
            db.query(UserAchievement)
            .filter_by(user_id=user_id, achievement_id=achievement_id)
            .first()
        )
        if existing:
            return False

        record = UserAchievement(
            user_id=user_id,
            achievement_id=achievement_id,
            unlocked_at=_now(),
        )
        db.add(record)
        if commit:
            db.commit()

        ach = ACHIEVEMENTS_BY_ID[achievement_id]
        # Benachrichtigung via SSE
        SyncEventService.publish(
            {
                "type": "achievement_unlocked",
                "achievement": {
                    "id": achievement_id,
                    "title": ach["title"],
                    "description": ach["description"],
                    "points": ach["points"],
                    "icon": ach["icon"],
                    "unlocked_at": record.unlocked_at.isoformat(),
                },
            },
            user_id=user_id,
        )
        logger.info("Achievement '%s' für User %d freigeschaltet", achievement_id, user_id)
        return True

    @classmethod
    def record_activity_time(
        cls, db: Session, user_id: int, category: str, seconds: int, commit: bool = True
    ) -> dict[str, Any]:
        """Erfasst aktive Interaktionszeit („Spielzeit“) und prüft Zeit-Meilensteine."""
        seconds = max(1, min(seconds, 3600))
        cat = category.strip()[:32] or "general"

        record = (
            db.query(UserActivityTime)
            .filter_by(user_id=user_id, category=cat)
            .first()
        )
        if not record:
            record = UserActivityTime(
                user_id=user_id,
                category=cat,
                seconds=seconds,
                last_active_at=_now(),
            )
            db.add(record)
        else:
            record.seconds += seconds
            record.last_active_at = _now()

        if commit:
            db.commit()

        # Prüfe Stundenmeilensteine über alle Kategorien
        total_seconds = (
            db.query(func.sum(UserActivityTime.seconds))
            .filter(UserActivityTime.user_id == user_id)
            .scalar()
            or 0
        )
        total_hours = total_seconds / 3600.0

        if total_hours >= 1.0:
            cls.unlock_achievement(db, user_id, "activity_hour_1", commit=commit)
        if total_hours >= 5.0:
            cls.unlock_achievement(db, user_id, "activity_hour_5", commit=commit)
        if total_hours >= 10.0:
            cls.unlock_achievement(db, user_id, "activity_hour_10", commit=commit)
        if total_hours >= 25.0:
            cls.unlock_achievement(db, user_id, "activity_hour_25", commit=commit)
        if total_hours >= 50.0:
            cls.unlock_achievement(db, user_id, "activity_hour_50", commit=commit)
        if total_hours >= 100.0:
            cls.unlock_achievement(db, user_id, "activity_hour_100", commit=commit)
        if total_hours >= 250.0:
            cls.unlock_achievement(db, user_id, "activity_hour_250", commit=commit)
        if total_hours >= 500.0:
            cls.unlock_achievement(db, user_id, "activity_hour_500", commit=commit)

        return {
            "category": cat,
            "category_seconds": record.seconds,
            "total_seconds": total_seconds,
            "total_hours": round(total_hours, 2),
        }

    @classmethod
    def check_automatic_achievements(cls, db: Session, user_id: int) -> None:
        """Prüft automatische Meilensteine anhand bestehender Daten."""
        user = db.query(User).filter_by(id=user_id).first()
        if not user:
            return

        # 1. Erster Schritt: Bei aktiver Session immer verdient
        cls.unlock_achievement(db, user_id, "starter_first_step", commit=False)

        # 2. 2FA aktiv
        if user.two_factor_enabled:
            cls.unlock_achievement(db, user_id, "starter_security_first", commit=False)

        # 3. Server-Anzahl (nur eigene oder freigegebene Server)
        if getattr(user, "is_owner", False):
            server_count = db.query(func.count(Server.id)).scalar() or 0
        else:
            server_count = db.query(func.count(func.distinct(ServerPermission.server_id))).filter_by(user_id=user_id).scalar() or 0

        if server_count >= 1:
            cls.unlock_achievement(db, user_id, "server_architect", commit=False)
        if server_count >= 3:
            cls.unlock_achievement(db, user_id, "server_fleet_admiral", commit=False)

        # 4. Backups vorhanden (nur eigene oder freigegebene)
        if getattr(user, "is_owner", False):
            backup_count = db.query(func.count(Backup.id)).scalar() or 0
        else:
            backup_count = (
                db.query(func.count(func.distinct(Backup.id)))
                .join(ServerPermission, Backup.server_id == ServerPermission.server_id)
                .filter(ServerPermission.user_id == user_id)
                .scalar()
                or 0
            )
        if backup_count >= 1:
            cls.unlock_achievement(db, user_id, "backup_guardian", commit=False)

        # 5. KI-Konversation
        conv_count = db.query(func.count(AiConversation.id)).filter_by(user_id=user_id).scalar() or 0
        if conv_count >= 1:
            cls.unlock_achievement(db, user_id, "ai_first_contact", commit=False)

        # 6. Freunde vorhanden
        friends_count = (
            db.query(func.count(UserFriend.id))
            .filter(
                ((UserFriend.user_id == user_id) | (UserFriend.friend_id == user_id))
                & (UserFriend.status == "accepted")
            )
            .scalar()
            or 0
        )
        if friends_count >= 1:
            cls.unlock_achievement(db, user_id, "social_handshake", commit=False)

        try:
            db.commit()
        except Exception:
            db.rollback()
