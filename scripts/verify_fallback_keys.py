import os
import json
import re

root = r"c:\Users\einma\AppData\Local\Singra\workspace\maunting-server-manager\frontend"
de_json_path = os.path.join(root, "src", "locales", "de.json")
en_json_path = os.path.join(root, "src", "locales", "en.json")

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
with open(en_json_path, 'r', encoding='utf-8') as f:
    en_dict = flatten(json.load(f))

frontend_src = os.path.join(root, "src")
pattern = re.compile(r"""\b(?:t|i18n\.t)\(\s*(['"][a-zA-Z0-9_.-]+['"])\s*,\s*(['"](?:(?!\s*\{).)+?['"])\s*(?:,\s*(\{[^}]*\}))?\s*\)""")

missing_in_de = []
missing_in_en = []
total = 0

for r, d, files in os.walk(frontend_src):
    for f in files:
        if not f.endswith(('.ts', '.tsx')) or f.endswith(('.test.ts', '.test.tsx')):
            continue
        p = os.path.join(r, f)
        with open(p, 'r', encoding='utf-8', errors='ignore') as fl:
            content = fl.read()
        for m in pattern.finditer(content):
            key = m.group(1).strip('\'"')
            total += 1
            if key not in de_dict:
                missing_in_de.append((os.path.relpath(p, frontend_src), key))
            if key not in en_dict:
                missing_in_en.append((os.path.relpath(p, frontend_src), key))

print(f"Total fallback calls: {total}")
print(f"Missing in de: {len(missing_in_de)} -> {missing_in_de}")
print(f"Missing in en: {len(missing_in_en)} -> {missing_in_en}")
