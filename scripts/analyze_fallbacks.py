import os
import re

frontend_src = r"c:\Users\einma\AppData\Local\Singra\workspace\maunting-server-manager\frontend\src"

# Match t('key', 'fallback'...) or t("key", "fallback"...)
# where the second argument is a string literal (with optional options object or closing paren)
pattern = re.compile(r"""\b(?:t|i18n\.t)\(\s*(['"][A-Za-z0-9_.-]+['"])\s*,\s*(['"][^'"]+['"])(\s*,\s*\{[^}]*\})?\s*\)""")

matches_by_file = {}
total = 0

for root, dirs, files in os.walk(frontend_src):
    for f in files:
        if f.endswith(('.ts', '.tsx')) and not f.endswith(('.test.ts', '.test.tsx')):
            p = os.path.join(root, f)
            with open(p, 'r', encoding='utf-8', errors='ignore') as fl:
                content = fl.read()
            found = pattern.findall(content)
            if found:
                rel = os.path.relpath(p, frontend_src)
                matches_by_file[rel] = found
                total += len(found)

print(f"Total fallback occurrences: {total}")
for f, items in sorted(matches_by_file.items(), key=lambda x: -len(x[1])):
    print(f"{f}: {len(items)}")
