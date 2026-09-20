import os
import re

frontend_src = r"c:\Users\einma\AppData\Local\Singra\workspace\maunting-server-manager\frontend\src"

GERMAN_WORDS = [
    'nicht', 'Bitte', 'bitte', 'Fehler', 'fehlgeschlagen', 'erfolgreich',
    'gesperrt', 'hinterlegt', 'stimmt', 'Schlüssel', 'Schluessel', 'Gerät', 'Geraet',
    'Passwort', 'Eingabe', 'überprüfe', 'ueberpruefe', 'abgenommen', 'aufgelegt',
    'stummgeschaltet', 'Update', 'Aktualisierung', 'Gespräch', 'Gespraech',
    'Mailbox', 'temporär', 'temporaer', 'möglich', 'moeglich', 'erreichbar',
    'Fehlversuche', 'Sekunden', 'geladen', 'eingerichtet', 'überprüfen', 'ueberpruefen',
    'keine', 'kein', 'wurde', 'konnte', 'steht', 'bisherige', 'erneut'
]

german_candidates = {}
for root, dirs, files in os.walk(frontend_src):
    for f in files:
        if not f.endswith(('.ts', '.tsx')) or f.endswith(('.test.ts', '.test.tsx')):
            continue
        path = os.path.join(root, f)
        rel = os.path.relpath(path, frontend_src).replace('\\', '/')
        with open(path, 'r', encoding='utf-8', errors='ignore') as fl:
            content = fl.read()

        lines = content.split('\n')
        for i, line in enumerate(lines):
            sline = line.strip()
            if sline.startswith('//') or sline.startswith('*') or sline.startswith('/*') or sline.startswith('import '):
                continue
            str_matches = re.findall(r'''(?:'([^'\\]*(?:\\.[^'\\]*)*)'|"([^"\\]*(?:\\.[^"\\]*)*)"|`([^`\\]*(?:\\.[^`\\]*)*)`)''', line)
            for groups in str_matches:
                raw_str = next((g for g in groups if g), None)
                if not raw_str:
                    continue
                if any(c in raw_str for c in ['http://', 'https://', 'text-', 'bg-', 'flex', 'font-', 'px-', 'py-', 'rounded']):
                    continue
                if re.match(r'^[a-zA-Z0-9_.-]+$', raw_str) and '.' in raw_str:
                    continue
                words = [w.strip('.,!?:;"()[]{}') for w in raw_str.split()]
                matches_german = [w for w in words if w in GERMAN_WORDS or any(uml in w for uml in 'äöüÄÖÜß')]
                if matches_german and len(words) >= 2:
                    if re.search(r'''\b(?:t|i18n\.t)\([^)]*['"`]''' + re.escape(raw_str) + r'''['"`]''', line):
                        continue
                    german_candidates.setdefault(rel, []).append((i + 1, raw_str, sline))

print(f"Top 20 files:")
for rel, items in sorted(german_candidates.items(), key=lambda x: -len(x[1]))[:25]:
    print(f"{rel}: {len(items)}")
    for line_num, txt, sline in items:
        print(f"   L{line_num}: {txt}")
