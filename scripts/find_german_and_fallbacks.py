import os
import re

frontend_src = r"c:\Users\einma\AppData\Local\Singra\workspace\maunting-server-manager\frontend\src"

# Words that strongly indicate German user-visible sentences
GERMAN_WORDS = [
    'nicht', 'Bitte', 'bitte', 'Fehler', 'fehlgeschlagen', 'erfolgreich',
    'gesperrt', 'hinterlegt', 'stimmt', 'Schlüssel', 'Schluessel', 'Gerät', 'Geraet',
    'Passwort', 'Eingabe', 'überprüfe', 'ueberpruefe', 'abgenommen', 'aufgelegt',
    'stummgeschaltet', 'Update', 'Aktualisierung', 'Gespräch', 'Gespraech',
    'Mailbox', 'temporär', 'temporaer', 'möglich', 'moeglich', 'erreichbar',
    'Fehlversuche', 'Sekunden', 'geladen', 'eingerichtet', 'überprüfen', 'ueberpruefen',
    'keine', 'kein', 'wurde', 'konnte', 'steht', 'bisherige', 'erneut'
]

# We want to find:
# 1) String literals that contain German text in services/stores/hooks/etc (not test files)
# 2) t('key', 'literal fallback' [, options]) calls where the 2nd argument is a literal string

def scan():
    german_candidates = {}
    fallback_calls = {}

    # Regex for t('key', 'fallback' [, options])
    # Note: t('key', 'fallback') or i18n.t('key', 'fallback')
    t_pattern = re.compile(
        r'''\b(?:t|i18n\.t)\(\s*['"]([a-zA-Z0-9_.-]+)['"]\s*,\s*(['"](?:(?!\s*\{).)+?['"])\s*(?:,\s*(\{.*?\}))?\s*\)''',
        re.DOTALL
    )

    for root, dirs, files in os.walk(frontend_src):
        for f in files:
            if not f.endswith(('.ts', '.tsx')) or f.endswith(('.test.ts', '.test.tsx')):
                continue
            path = os.path.join(root, f)
            rel = os.path.relpath(path, frontend_src).replace('\\', '/')
            with open(path, 'r', encoding='utf-8', errors='ignore') as fl:
                content = fl.read()

            # Find fallbacks
            # We also want to check line by line or finditer
            for match in re.finditer(r'''\b(?:t|i18n\.t)\(\s*(['"][a-zA-Z0-9_.-]+['"])\s*,\s*(['"](?:(?!\s*\{).)+?['"])(\s*,\s*\{[^}]*\})?\s*\)''', content):
                key = match.group(1).strip('\'"')
                fb = match.group(2).strip('\'"')
                # Only if fb looks like text (has German characters, spaces, or words, not an empty or interpolation object)
                if len(fb) > 1 and not fb.startswith('{'):
                    fallback_calls.setdefault(rel, []).append((match.start(), key, fb, match.group(0)))

            # Find German string literals outside t() calls
            # Search for strings containing German words
            lines = content.split('\n')
            for i, line in enumerate(lines):
                # skip comments or import lines
                sline = line.strip()
                if sline.startswith('//') or sline.startswith('*') or sline.startswith('/*') or sline.startswith('import '):
                    continue
                # check string literals in line
                # match '...' or "..." or `...`
                str_matches = re.findall(r'''(?:'([^'\\]*(?:\\.[^'\\]*)*)'|"([^"\\]*(?:\\.[^"\\]*)*)"|`([^`\\]*(?:\\.[^`\\]*)*)`)''', line)
                for groups in str_matches:
                    raw_str = next((g for g in groups if g), None)
                    if not raw_str:
                        continue
                    # ignore css classes, html tags, keys, paths, urls, formats
                    if any(c in raw_str for c in ['http://', 'https://', 'text-', 'bg-', 'flex', 'font-', 'px-', 'py-', 'rounded']):
                        continue
                    if re.match(r'^[a-zA-Z0-9_.-]+$', raw_str) and '.' in raw_str:
                        continue # likely a key or class
                    # Check if it has German words and spaces (sentence-like)
                    words = [w.strip('.,!?:;"()[]{}') for w in raw_str.split()]
                    matches_german = [w for w in words if w in GERMAN_WORDS or any(uml in w for uml in 'äöüÄÖÜß')]
                    if matches_german and len(words) >= 2:
                        # Ensure it's not inside t('...')
                        if f"'{raw_str}'" in line or f'"{raw_str}"' in line or f'`{raw_str}`' in line:
                            # check if this line is a t(..., 'raw_str') call
                            if re.search(r'''\b(?:t|i18n\.t)\([^)]*['"`]''' + re.escape(raw_str) + r'''['"`]''', line):
                                continue # handled as fallback
                            german_candidates.setdefault(rel, []).append((i + 1, raw_str, sline))

    print(f"=== GERMAN RAW STRINGS ({len(german_candidates)} files) ===")
    total_raw = 0
    for rel, items in sorted(german_candidates.items(), key=lambda x: -len(x[1])):
        print(f"\n{rel} ({len(items)} items):")
        total_raw += len(items)
        for line_num, txt, sline in items[:15]:
            print(f"  L{line_num}: {txt}  -->  {sline[:80]}")
    print(f"\nTotal raw German strings: {total_raw}")

    print(f"\n=== T() FALLBACK ARGUMENTS ({len(fallback_calls)} files) ===")
    total_fb = sum(len(v) for v in fallback_calls.values())
    print(f"Total fallback occurrences: {total_fb}")
    for rel, items in sorted(fallback_calls.items(), key=lambda x: -len(x[1])):
        print(f"  {rel}: {len(items)}")

if __name__ == '__main__':
    scan()
