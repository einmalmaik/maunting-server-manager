"""Minimaler INI-Setter für UE-Engine-/Game-Configs.

Wir nutzen bewusst KEIN Python-`configparser`: UE-INIs erlauben Duplikat-Keys,
mehrzeilige Werte und vorhandene Kommentare, die `configparser` zerstören würde.
Stattdessen ein zeilen-orientierter Ansatz wie in der Pterodactyl-`start.sh`.

KISS: nur die Operationen, die wir hier wirklich brauchen — `set_ini_value`.
Keine Sektion-Reordering, kein Kommentar-Handling.
"""

from __future__ import annotations

import os


def set_ini_value(file_path: str, section: str, key: str, value: str) -> None:
    """Setzt `key=value` in `[section]`. Erzeugt Section/Key, falls fehlt.

    - Wenn die Datei nicht existiert, wird sie angelegt.
    - Wenn die Section nicht existiert, wird sie am Ende angehängt.
    - Wenn der Key in der Section existiert, wird er überschrieben (erste
      Vorkommen). Spätere Duplikate bleiben erhalten — UE-Configs nutzen
      manchmal absichtlich Duplikate (z. B. Mod-Listen).
    """
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    if not os.path.exists(file_path):
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"[{section}]\n{key}={value}\n")
        return

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    section_header = f"[{section}]"
    in_section = False
    found_section = False
    wrote_key = False
    out: list[str] = []

    for line in lines:
        stripped = line.strip()

        # Section-Header detektieren
        if stripped.startswith("[") and stripped.endswith("]"):
            # Verlassen wir gerade die Ziel-Section, ohne den Key gesetzt zu haben?
            if in_section and not wrote_key:
                out.append(f"{key}={value}\n")
                wrote_key = True
            in_section = stripped == section_header
            if in_section:
                found_section = True
            out.append(line)
            continue

        # In Ziel-Section: Key überschreiben, wenn erste Vorkommen
        if in_section and not wrote_key and stripped.startswith(f"{key}="):
            out.append(f"{key}={value}\n")
            wrote_key = True
            continue

        out.append(line)

    # Falls Section am Dateiende war und Key nicht gesetzt wurde:
    if in_section and not wrote_key:
        if out and not out[-1].endswith("\n"):
            out[-1] = out[-1] + "\n"
        out.append(f"{key}={value}\n")
        wrote_key = True

    # Section gar nicht gefunden → am Ende anhängen
    if not found_section:
        if out and not out[-1].endswith("\n"):
            out[-1] = out[-1] + "\n"
        # Leerzeile vor neuer Section, falls vorher nicht leer
        if out and out[-1].strip():
            out.append("\n")
        out.append(f"{section_header}\n")
        out.append(f"{key}={value}\n")

    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(out)


def remove_ini_key(file_path: str, section: str, key: str) -> None:
    """Entfernt `key=*` aus `[section]`. No-op wenn Datei oder Key fehlt.

    Entfernt das erste Vorkommen in der Ziel-Section.
    """
    if not os.path.exists(file_path):
        return

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    section_header = f"[{section}]"
    in_section = False
    removed = False
    out: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped == section_header
            out.append(line)
            continue

        if in_section and not removed and stripped.startswith(f"{key}="):
            removed = True
            continue

        out.append(line)

    if removed:
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(out)
