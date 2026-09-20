import os
import re

frontend_src = r"c:\Users\einma\AppData\Local\Singra\workspace\maunting-server-manager\frontend\src"

files_data = {}

for root, dirs, files in os.walk(frontend_src):
    for f in files:
        if not f.endswith(('.ts', '.tsx')) or f.endswith(('.test.ts', '.test.tsx')):
            continue
        p = os.path.join(root, f)
        rel = os.path.relpath(p, frontend_src).replace('\\', '/')
        with open(p, 'r', encoding='utf-8', errors='ignore') as fl:
            lines = fl.readlines()

        for idx, line in enumerate(lines):
            sline = line.strip()
            if sline.startswith('//') or sline.startswith('*') or sline.startswith('/*') or sline.startswith('import '):
                continue
            
            # Match throw new Error('...'), toast.xxx('...'), fehler: '...', message: '...'
            # where string contains German words
            matches = re.finditer(r'''(?:throw\s+new\s+(?:Error|SanitizedApiError)\s*\(\s*|toast\.(?:error|info|success|warning)\s*\(\s*|(?:fehler|error|message|grund)\s*:\s*|reject\s*\(\s*new\s+Error\s*\(\s*)(['"`].*?['"`])''', line)
            for m in matches:
                raw_val = m.group(1)
                if raw_val.startswith("t(") or raw_val.startswith("i18n.t("):
                    continue
                q = raw_val[0]
                val = raw_val[1:-1] if raw_val.endswith(q) else raw_val[1:]
                if val.startswith("t(") or val.startswith("i18n.t("):
                    continue
                # German indicators
                words = [w.strip('.,!?:;"()[]{}') for w in val.split()]
                has_de = any(
                    w.lower() in [
                        'nicht', 'bitte', 'fehler', 'fehlgeschlagen', 'erfolgreich', 'gesperrt',
                        'hinterlegt', 'stimmt', 'schlüssel', 'schluessel', 'gerät', 'geraet',
                        'passwort', 'eingabe', 'überprüfe', 'ueberpruefe', 'abgenommen', 'aufgelegt',
                        'stummgeschaltet', 'update', 'aktualisierung', 'gespräch', 'gespraech',
                        'mailbox', 'temporär', 'temporaer', 'möglich', 'moeglich', 'erreichbar',
                        'fehlversuche', 'sekunden', 'geladen', 'eingerichtet', 'überprüfen',
                        'ueberpruefen', 'keine', 'kein', 'wurde', 'konnte', 'steht', 'bisherige',
                        'erneut', 'beendet', 'abgelehnt', 'ausführung', 'abgelehnt', 'moderator',
                        'verbindung', 'anrufer', 'übertragen', 'beigetreten', 'verlassen',
                        'wiederhergestellt', 'entfernen', 'senden', 'annehmen', 'ablehnen',
                        'prüfen', 'pruefen', 'blockierung', 'aufgehoben', 'anzeigen', 'kopiert',
                        'gespeichert', 'erstellt', 'gelöscht'
                    ] or any(u in w for u in 'äöüÄÖÜß')
                    for w in words
                )
                if has_de and len(words) >= 2:
                    files_data.setdefault(rel, []).append((idx + 1, val, sline))

print(f"Found {len(files_data)} files:")
for rel, items in sorted(files_data.items(), key=lambda x: -len(x[1])):
    print(f"\n{rel} ({len(items)}):")
    for lno, val, sline in items:
        print(f"  [{lno}] {val}")

