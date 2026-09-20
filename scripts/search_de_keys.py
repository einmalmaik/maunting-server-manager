import json

with open(r"c:\Users\einma\AppData\Local\Singra\workspace\maunting-server-manager\frontend\src\locales\de.json", "r", encoding="utf-8") as f:
    de = json.load(f)

def search_keys(d, term, prefix=""):
    res = {}
    for k, v in d.items():
        nxt = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            res.update(search_keys(v, term, nxt))
        elif term.lower() in k.lower() or term.lower() in str(v).lower():
            res[nxt] = v
    return res

print("Searching for 'abgenommen':", search_keys(de, "abgenommen"))
print("Searching for 'messengerLock':", search_keys(de, "messengerLock"))
print("Searching for 'Master-Passwort':", list(search_keys(de, "Master-Passwort").items())[:10])
print("Searching for 'PIN':", list(search_keys(de, "PIN").items())[:10])
print("Searching for 'call':", list(search_keys(de, "call").items())[:10])
