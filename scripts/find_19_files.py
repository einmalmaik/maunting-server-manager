import os
import re

frontend_src = r"c:\Users\einma\AppData\Local\Singra\workspace\maunting-server-manager\frontend\src"

# We want to identify the 19 files mentioned in:
# "Ein strenger Durchlauf über das ganze Frontend findet 59 sichtbare deutsche Sätze in 19 Dateien, mit demselben Muster:
# desktop/vault/vaultStore.ts 14
# services/messengerSperre.ts 11
# stores/useCallStore.ts 9
# desktop/Einstellungen.tsx 5
# 15 weitere je 1-2"
# Note: 14 + 11 + 9 + 5 = 39. 39 + 20 = 59! 15 weitere Dateien with 1-2 each = 20 strings! Total 19 files, 59 strings!

# Let's inspect all candidates where:
# - An Error is thrown: throw new Error('German...')
# - A Toast is shown: toast.info/error/success('German...')
# - A store/service field or message or reject: fehler: 'German...', reject(new Error('German...'))

target_files = {}

for root, dirs, files in os.walk(frontend_src):
    for f in files:
        if not f.endswith(('.ts', '.tsx')) or f.endswith(('.test.ts', '.test.tsx')):
            continue
        path = os.path.join(root, f)
        rel = os.path.relpath(path, frontend_src).replace('\\', '/')
        with open(path, 'r', encoding='utf-8', errors='ignore') as fl:
            lines = fl.readlines()

        for idx, line in enumerate(lines):
            sline = line.strip()
            if sline.startswith('//') or sline.startswith('*') or sline.startswith('/*') or sline.startswith('import '):
                continue

            # Look for string literals that look like German user-facing messages:
            # throw new Error('...'), toast.error('...'), fehler: '...', message: '...'
            # excluding t(...) calls
            if re.search(r'\bt\s*\(', line):
                # If the whole line is t(...) or the message is wrapped in t, skip
                # But check if there's a fallback or untranslated string
                pass

            patterns = [
                r'''throw\s+new\s+(?:Error|SanitizedApiError)\s*\(\s*(['"`].*?['"`])\s*\)''',
                r'''toast\.(?:error|info|success|warning)\s*\(\s*(['"`].*?['"`])\s*\)''',
                r'''(?:fehler|error|message|grund|untertitel)\s*:\s*(['"`].*?['"`])''',
                r'''reject\s*\(\s*new\s+Error\s*\(\s*(['"`].*?['"`])\s*\)''',
            ]

            for pat in patterns:
                for match in re.finditer(pat, line):
                    val = match.group(1)
                    if val.startswith('t(') or val.startswith('i18n.t('):
                        continue
                    # remove outer quotes
                    q = val[0]
                    inner = val[1:-1] if val.endswith(q) else val[1:]
                    if inner.startswith('t(') or inner.startswith('i18n.t('):
                        continue
                    # Check if it has German text
                    # e.g., German words or umlauts
                    words = [w.strip('.,!?:;"()[]{}') for w in inner.split()]
                    has_german = any(
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
                    if has_german and len(words) >= 2:
                        target_files.setdefault(rel, []).append((idx + 1, inner, line.strip()))

print(f"Candidate files: {len(target_files)}")
total = sum(len(v) for v in target_files.values())
print(f"Total messages: {total}\n")

for rel, items in sorted(target_files.items(), key=lambda x: -len(x[1])):
    print(f"=== {rel} ({len(items)}) ===")
    for lno, msg, raw in items:
        print(f"  L{lno}: {msg}")
