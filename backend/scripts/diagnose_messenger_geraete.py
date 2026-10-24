#!/usr/bin/env python3
"""Diagnose: Geräteverzeichnis, Freigaben und die Vertrauensfrage.

Liest **nur**. Kein Schreibzugriff auf `user_e2ee_devices`, keine Migration,
kein Aufräumen — das Skript beantwortet die Frage, die man vor jedem Fix
zuerst beantworten muss: steht in der Datenbank wirklich ein Gerät, das der
Server als freigegeben führt, für das aber keine Unterschrift vorliegt?

Genau daraus entsteht die Warnung aus `AGENTS.md`-Punkt A der Diagnose:

* `is_approved` ist ein Wort des Servers, und die Liste unter *Profil → Geräte*
  zeigt es unverändert an.
* Die Warnung im Messenger hängt an der **Signaturkette** im Client
  (`vertrauteGeraete` in `frontend/src/services/e2eeGeraet.ts`). Fehlt die
  Unterschrift über die aktuellen Schlüssel, gilt das Gerät dort als
  unbestätigt, und der Chat sagt: „Es bekommt nichts."

Auseinanderfallen können beide Sichten, weil der Client **zusätzlich** einen
Fingerabdruck vergleicht, den nur er kennt: die Vertrauensliste im
`localStorage` (`msm_vertraute_geraete:<userId>`) enthält `sha256(public_key)`.
Ein Gerät, dessen Schlüssel sich dort verändert hat, gilt im Client als
unbestätigt — und der Sendeweg lässt es aus, es bekommt dann wirklich nichts.

**Das Skript behauptet das nicht.** Aus der Datenbank ist ein solcher Wechsel
nicht ablesbar: seit 10/2026 setzt `_auffrischen` bei einem neuen Schlüssel
`is_approved` zurück, eine freigegebene Zeile ohne Unterschrift ist also
**kein** Beweis für einen Wechsel. Sie ist der Normalfall beim ersten Gerät
eines Kontos und nach `wieder_freigeben` („freigegeben“ ≠ „beglaubigt“). Das
Urteil lautet deshalb `ohne-unterschrift` und nennt den erwarteten Abdruck zum
Vergleich; ob das Gerät ankommt, entscheidet erst dieser Vergleich.

Aufruf:

    python backend/scripts/diagnose_messenger_geraete.py
    python backend/scripts/diagnose_messenger_geraete.py --konto anna
    python backend/scripts/diagnose_messenger_geraete.py --konto 42 --json

Der letzte Schritt bleibt Handarbeit im Browser: die Vertrauensliste liegt im
`localStorage` des jeweiligen Geräts (`msm_vertraute_geraete:<userId>`), nicht
in der Datenbank. Der Abdruck dort ist `sha256(public_key)` — das Skript gibt
den erwarteten Hash deshalb mit aus.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

# Damit `config`, `database` und `models` importierbar sind — wie in den
# übrigen Skripten unter `backend/scripts/`.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Eine Windows-Konsole ist cp1252 und bricht an „→“ mit UnicodeEncodeError ab —
# ein Diagnoseskript, das beim Ausgeben stirbt, beantwortet die Frage nicht.
for _strom in (sys.stdout, sys.stderr):
    if hasattr(_strom, "reconfigure"):
        _strom.reconfigure(encoding="utf-8", errors="replace")

from database import SessionLocal  # noqa: E402
from models import User, UserE2eeDevice  # noqa: E402

#: Urteile, in der Reihenfolge, in der sie in der Zusammenfassung erscheinen.
URTEIL_GEDECKT = "gedeckt"
URTEIL_ERSTES = "erstes-geraet"
URTEIL_UNSIGNIERT = "ohne-unterschrift"
URTEIL_WARTEND = "wartend"

URTEIL_TEXT = {
    URTEIL_GEDECKT: "Freigabe unterschrieben — Liste und Warnung stimmen überein.",
    URTEIL_ERSTES: (
        "einziges freigegebenes Gerät — ohne Unterschrift ist hier normal "
        "(es gab niemanden, der freigeben konnte)."
    ),
    URTEIL_UNSIGNIERT: (
        "freigegeben ohne Unterschrift, und es gibt weitere freigegebene "
        "Geräte. Beim ersten Gerät und nach dem Wiederzulassen ist das normal "
        "und sagt nichts über die Zustellung. Ob der Client es für vertraut "
        "hält, entscheidet der gespeicherte Fingerabdruck — Vergleich unten."
    ),
    URTEIL_WARTEND: "wartet auf Freigabe — bekommt nichts, ist aber sichtbar.",
}


def fingerabdruck(public_key_jwk: str) -> str:
    """Derselbe Hash, den `vertrauteGeraete` im Client bildet."""
    return hashlib.sha256((public_key_jwk or "").encode("utf-8")).hexdigest()


def _zeit(wert: datetime | None) -> str:
    if wert is None:
        return "—"
    if wert.tzinfo is None:
        wert = wert.replace(tzinfo=timezone.utc)
    return wert.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def urteil(geraet: UserE2eeDevice, andere_freigegebene: int) -> str:
    """Wie der Client dieses Gerät sieht — die Regel aus `vertrauteGeraete`.

    Nachgebaut ist nur der Teil, den die Datenbank beantworten kann:
    Unterschrift vorhanden oder nicht. Der Fingerabdruck-Vergleich im
    `localStorage` bleibt beim Menschen (siehe Kopf).
    """
    if not geraet.is_approved:
        return URTEIL_WARTEND
    if (geraet.approval_signature or "").strip() and (geraet.approved_by or "").strip():
        return URTEIL_GEDECKT
    if andere_freigegebene == 0:
        return URTEIL_ERSTES
    # Kein „Geist“: die fehlende Unterschrift allein ist beim ersten Gerät und
    # nach `wieder_freigeben` der Normalfall. Aus der Datenbank ist nicht
    # ablesbar, ob der Client dieses Gerät noch für vertraut hält — das steht
    # nur in seiner eigenen Vertrauensliste.
    return URTEIL_UNSIGNIERT


def befund(db, user: User) -> dict:
    """Alle Geräte eines Kontos mit Urteil und Hinweisen."""
    eintraege = (
        db.query(UserE2eeDevice)
        .filter(UserE2eeDevice.user_id == user.id)
        .order_by(UserE2eeDevice.last_seen_at.desc())
        .all()
    )
    freigegeben = [e for e in eintraege if e.is_approved]

    geraete = []
    for e in eintraege:
        andere = len([f for f in freigegeben if f.id != e.id])
        hinweise: list[str] = []
        if not (e.label or "").strip():
            # Seit 10/2026 liefert die Schnittstelle dafür den Standardnamen
            # (`e2ee_device_service.STANDARD_BEZEICHNUNG`). Die Zeile bleibt
            # trotzdem auffällig: niemand hat diesen Namen gewählt.
            hinweise.append(
                "ohne eigenen Namen — erscheint als „Web-Frontend“ (Standardname)."
            )
        if e.is_approved and not (e.approval_signature or "").strip() and andere > 0:
            hinweise.append(
                "Ohne Unterschrift freigegeben: normal beim ersten Gerät und nach "
                "`wieder_freigeben`. Ein Schlüsselwechsel setzt `is_approved` "
                "zurück (`_auffrischen`), die Zeile allein belegt also keinen "
                "Wechsel — der Fingerabdruck unten entscheidet."
            )
        if not e.auth_family:
            hinweise.append(
                "keine Sitzungskette (`auth_family`) — Bestand von vor 09/2026 oder "
                "ohne Familie gemeldet; Entfernen sperrt dann keine Sitzung."
            )
        geraete.append(
            {
                "device_id": e.device_id,
                "kurz": e.device_id[:12],
                "label": e.label or "",
                "is_approved": bool(e.is_approved),
                "approved_by": e.approved_by or "",
                "hat_unterschrift": bool((e.approval_signature or "").strip()),
                "signaturschluessel": bool((e.signing_public_key_jwk or "").strip()),
                "auth_family": e.auth_family or "",
                "erstellt": _zeit(e.created_at),
                "zuletzt": _zeit(e.last_seen_at),
                "fingerabdruck_public_key": fingerabdruck(e.public_key_jwk),
                "urteil": urteil(e, andere),
                "hinweise": hinweise,
            }
        )

    zaehlung = {u: 0 for u in URTEIL_TEXT}
    for g in geraete:
        zaehlung[g["urteil"]] += 1
    return {
        "user_id": user.id,
        "username": user.username,
        "geraete": geraete,
        "zaehlung": zaehlung,
    }


def _drucke(befunde: list[dict], nur_probleme: bool) -> None:
    for befund_eines in befunde:
        kopf = f"Konto {befund_eines['user_id']} ({befund_eines['username']})"
        print()
        print(kopf)
        print("=" * len(kopf))
        geraete = befund_eines["geraete"]
        if not geraete:
            print("  keine Geräte eingetragen")
            continue
        print(
            f"  {'Gerät':<12}  {'frei':<5} {'sig':<4} {'von':<12} "
            f"{'Name':<20} {'zuletzt':<16} Urteil"
        )
        for g in geraete:
            if nur_probleme and g["urteil"] in (URTEIL_GEDECKT, URTEIL_ERSTES):
                continue
            name = (g["label"] or "—")[:20]
            print(
                f"  {g['kurz']:<12}  {'ja' if g['is_approved'] else 'nein':<5} "
                f"{'ja' if g['hat_unterschrift'] else 'nein':<4} "
                f"{(g['approved_by'] or '—')[:12]:<12} {name:<20} {g['zuletzt']:<16} "
                f"{g['urteil']}"
            )
            print(f"  {'':12}  {URTEIL_TEXT[g['urteil']]}")
            for h in g["hinweise"]:
                print(f"  {'':12}  · {h}")

        z = befund_eines["zaehlung"]
        print()
        print(
            f"  Zusammenfassung: {z[URTEIL_GEDECKT]} gedeckt, "
            f"{z[URTEIL_ERSTES]} erstes Gerät, {z[URTEIL_UNSIGNIERT]} ohne "
            f"Unterschrift, {z[URTEIL_WARTEND]} wartend"
        )
        if z[URTEIL_UNSIGNIERT]:
            print(
                "  → Ohne Unterschrift heisst nicht „kommt nicht an“. Ob der Client "
                "das Gerät für vertraut hält, steht nur in seiner eigenen Liste. "
                "Zum Gegenprüfen im Browser dieses Kontos:"
            )
            g = next(g for g in geraete if g["urteil"] == URTEIL_UNSIGNIERT)
            print(
                "     JSON.parse(localStorage['msm_vertraute_geraete:"
                f"{befund_eines['user_id']}'] || 'null')"
            )
            print(
                f"     erwarteter Abdruck für {g['kurz']}: {g['fingerabdruck_public_key']}"
            )
            print(
                "     Weicht der gespeicherte `k` davon ab (oder fehlt der Eintrag), "
                "gilt das Gerät im Client als unbestätigt und bekommt nichts."
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Liest Geräteverzeichnis und Freigaben eines Kontos (nur lesend)."
    )
    parser.add_argument(
        "--konto",
        help="Benutzername oder Kennung. Ohne Angabe: jedes Konto mit Geräten.",
    )
    parser.add_argument("--json", action="store_true", help="Maschinenlesbar ausgeben.")
    parser.add_argument(
        "--nur-probleme",
        action="store_true",
        help="Nur wartende und ungedeckte Geräte zeigen.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        abfrage = db.query(User)
        if args.konto:
            konto = args.konto.strip()
            if konto.isdigit():
                abfrage = abfrage.filter(User.id == int(konto))
            else:
                abfrage = abfrage.filter(User.username.ilike(konto))
            user = abfrage.first()
            if user is None:
                print(f"Kein Konto für {args.konto!r} gefunden.", file=sys.stderr)
                return 1
            benutzer = [user]
        else:
            # Ohne Angabe nur Konten, die überhaupt ein Gerät eingetragen haben —
            # sonst rauscht die Ausgabe eines Panels mit hunderten Konten.
            mit_geraeten = {
                uid for (uid,) in db.query(UserE2eeDevice.user_id).distinct().all()
            }
            benutzer = [u for u in abfrage.order_by(User.id.asc()).all() if u.id in mit_geraeten]

        befunde = [befund(db, u) for u in benutzer]
    finally:
        db.close()

    if args.json:
        print(json.dumps({"konten": befunde}, ensure_ascii=False, indent=2))
    else:
        _drucke(befunde, args.nur_probleme)
        print()
        print(
            "Hinweis: Dieses Skript liest nur. Ob ein Gerät im Client als vertraut "
            "gilt, entscheidet zusätzlich der Fingerabdruck im localStorage des "
            "Geräts — den Vergleich nennt die Ausgabe oben."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
