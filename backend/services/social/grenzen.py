"""Grenzen, Fristen und das Rechtevokabular der Social-Domänen.

Die Werte stehen hier, werden aber über ``services.social_service``
weiterveröffentlicht. Zwei davon sind bewusst über ``aktuelle_grenze``
lesbar: Tests und Betreiber stellen sie am öffentlichen Modul um, und die
Domänen sollen genau den dort gesetzten Wert sehen — nicht eine Kopie, die
beim Import entstanden ist.
"""

from __future__ import annotations

import sys


#: Vorhaltefrist für E2EE-Umschläge auf dem Relais-Server (30 Tage).
#: Schützt vor Datenanhäufung bei Datenlecks und erzwingt das Zero-Knowledge-Prinzip.
E2EE_ENVELOPE_RETENTION_DAYS: int = 30

#: Wie viel ein Konto am Tag ins Relais schreiben darf, in KiB. Die Größe je
#: Umschlag begrenzt das Schema; ohne Tagesbudget füllte ein Konto die Platte
#: trotzdem, nur in vielen Nachrichten statt einer. 256 MiB am Tag sind weit
#: über dem, was Text, Steuerpakete und Schlüsselverteilung je erreichen.
E2EE_RELAY_KIB_PRO_TAG: int = 256 * 1024

#: Stories je Konto, die gleichzeitig laufen dürfen. Jede trägt bis zu 8 MB
#: Bild; ohne Grenze füllte ein Konto die Platte in 24 Stunden.
MAX_AKTIVE_STORIES: int = 20


#: Alle Rechte, die eine Gruppenrolle tragen kann. Wer hier nichts stehen hat,
#: kann nicht gesetzt werden — siehe ``SocialService.assert_known_permissions``.
GROUP_PERMISSIONS: frozenset[str] = frozenset(
    {
        "send_messages",
        "attach_media",
        "invite_members",
        "start_group_calls",
        "join_group_calls",
        "share_screen",
        "mute_in_calls",
        "kick_from_calls",
        "kick_members",
        "delete_messages",
        "manage_roles",
        "mention_everyone",
        "pin_messages",
        "set_disappearing_messages",
    }
)

#: Rechte rund um den Gruppenanruf. Eigentümer und Administratoren haben sie
#: ohne Eintrag — sie können sie sich ohnehin jederzeit selbst geben.
GROUP_CALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        "start_group_calls",
        "join_group_calls",
        "share_screen",
        "mute_in_calls",
        "kick_from_calls",
    }
)

#: Rechte, die in die laufende Unterhaltung eingreifen: alle auf einmal wecken,
#: eine Nachricht über den Verlauf heften und die Frist stellen, nach der neue
#: Nachrichten für alle verschwinden. Dieselbe Begründung wie bei den
#: Anrufrechten — wer die Rollen verwaltet, kann sie sich ohnehin selbst
#: eintragen, und ein Eigentümer, der seine eigene Gruppe nicht erreichen darf,
#: wäre kein Schutz, sondern ein Rätsel.
#:
#: Durchgesetzt wird keines davon hier: die Handlung reist verschlüsselt, und
#: der Server liest sie nie. Er rechnet nur die Marke je Mitglied aus
#: (``list_user_groups``), gegen die das **empfangende** Gerät prüft.
GROUP_MODERATION_PERMISSIONS: frozenset[str] = frozenset(
    {
        "mention_everyone",
        "pin_messages",
        "set_disappearing_messages",
    }
)

#: Rechte, die nur an einer **Rolle** hängen dürfen, nie an den Standardrechten
#: für alle (@everyone).
#:
#: ``manage_roles`` ist das eine: wer Rollen verwalten darf, kann sich jedes
#: andere Recht selbst eintragen. Als Standard für alle gesetzt, ist das keine
#: Einstellung, sondern die Abschaffung der Rollen — jedes einfache Mitglied
#: wäre dann Administrator.
#:
#: Der Dialog bietet es unter den Standardrechten schon länger nicht an
#: (``NICHT_ALS_STANDARD`` in ``GroupPermissionsModal.tsx``). Das war aber nur
#: ein ausgeblendeter Haken: ein einziger PATCH auf
#: ``/groups/<id>/permissions`` mit ``manage_roles`` wurde bis 09/2026 klaglos
#: angenommen und gespeichert. Eine Regel, die nur die Oberfläche kennt, ist
#: keine Regel. Wer die Liste hier ändert, ändert sie auch dort.
GROUP_ROLE_ONLY_PERMISSIONS: frozenset[str] = frozenset({"manage_roles"})

#: Was der Rechte-Dialog vor dem 17.09.2026 geschrieben hat. Wird beim Lesen
#: übersetzt, damit bereits gesetzte Haken nicht verloren gehen.
GROUP_PERMISSION_ALIASES: dict[str, tuple[str, ...]] = {
    "call_start": ("start_group_calls",),
    "call_join": ("join_group_calls",),
    "call_share": ("share_screen",),
    "call_moderate": ("mute_in_calls", "kick_from_calls"),
}


def aktuelle_grenze(name: str, standard: int) -> int:
    """Liest eine Grenze aus dem öffentlichen Modul, samt Test- und Betreiberumbau.

    Das öffentliche Modul ist ``services.social_service``; dorthin schreiben
    bestehende Tests ihre ``monkeypatch``-Werte. Der Zugriff läuft über
    ``sys.modules`` und nicht über einen Import, damit zwischen Domänen und
    Kompatibilitätsmodul kein Importzyklus entsteht.
    """
    modul = sys.modules.get("services.social_service")
    return getattr(modul, name, standard) if modul is not None else standard
