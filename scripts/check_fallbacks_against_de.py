import os
import json
import re

root = r"c:\Users\einma\AppData\Local\Singra\workspace\maunting-server-manager\frontend"
de_json_path = os.path.join(root, "src", "locales", "de.json")

def flatten(d, prefix=''):
    res = {}
    for k, v in d.items():
        nxt = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            res.update(flatten(v, nxt))
        else:
            res[nxt] = v
    return res

with open(de_json_path, 'r', encoding='utf-8') as f:
    de_dict = flatten(json.load(f))

frontend_src = os.path.join(root, "src")

# Find t('key', 'fallback') where key is in de_dict
pattern = re.compile(r"""\b(?:t|i18n\.t)\(\s*['"]([a-zA-Z0-9_.-]+)['"]\s*,\s*(['"](?:(?!\s*\{).)+?['"])\s*(?:,\s*(\{[^}]*\}))?\s*\)""")

exact_matches = []
all_string_second_arg = []

for r, d, files in os.walk(frontend_src):
    for f in files:
        if not f.endswith(('.ts', '.tsx')) or f.endswith(('.test.ts', '.test.tsx')):
            continue
        p = os.path.join(r, f)
        with open(p, 'r', encoding='utf-8', errors='ignore') as fl:
            content = fl.read()
        for m in pattern.finditer(content):
            key = m.group(1)
            fb = m.group(2)[1:-1]
            opts = m.group(3)
            all_string_second_arg.append((os.path.relpath(p, frontend_src), key, fb, opts, m.group(0)))
            if key in de_dict:
                exact_matches.append((os.path.relpath(p, frontend_src), key, fb, opts, m.group(0)))

print(f"Total calls with 2nd arg string literal: {len(all_string_second_arg)}")
print(f"Total where key exists in de.json: {len(exact_matches)}")

# Breakdown by quote type and format
sq = [x for x in all_string_second_arg if x[4].startswith("t('")]
print(f"Single quotes: {len(sq)}")

