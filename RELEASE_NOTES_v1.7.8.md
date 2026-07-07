# MSM v1.7.8 — False-Positive Update-Banner Fix

Behebt das fehlerhafte „Panel-Update verfügbar"-Banner, das auch dann erschien, wenn die installierte Version bereits aktuell oder sogar neuer als das letzte Release war.

## Root Cause

Der Update-Check in `/api/system/version` verglich die lokale Version (von `git describe --tags`) mit dem GitHub-Release `tag_name` per **einfachem String-Vergleich**:

```python
# ALT (fehlerhaft):
update_available = current != latest and latest != "unknown"
```

Das scheiterte an zwei Problemen:
1. **v-Prefix-Mismatch:** `git describe` kann `v1.7.7` zurückgeben, aber die interne Version war `1.7.7` → `"1.7.7" != "v1.7.7"` → falsches Update-Banner.
2. **git-describe-Suffixe:** Wenn HEAD nicht exakt auf dem Tag liegt (z.B. nach einem Hotfix-Commit), gibt `git describe` etwas wie `v1.7.7-2-gabcdef` zurück → immer ungleich dem Release-Tag.

## Fix

- Neue Hilfsfunktion `_strip_version()`: Normalisiert Versions-Strings, entfernt `v`-Prefix und git-describe-Suffixe (`v1.7.7-2-gabcdef` → `1.7.7`).
- Neue Hilfsfunktion `_version_newer()`: Numerischer SemVer-Vergleich (Major.Minor.Patch) statt String-Vergleich. Verhindert auch falsche Ergebnisse bei zweistelligen Segmenten (z.B. `1.10.0` > `1.9.0`).
- 24 Unit-Tests decken alle relevanten Szenarien ab (gleiche Version, Prefix-Mismatch, git-describe-Suffix, tatsächliches Update, lokale Version neuer als Release).

## Geänderte Dateien

- `backend/routers/system.py`: Versionsnormalisierung und SemVer-Vergleich.
- `backend/tests/test_version_check.py`: 24 neue Unit-Tests.
- `backend/main.py`: Version → `1.7.8`.
- `frontend/package.json` + `package-lock.json`: Version → `1.7.8`.

## Upgrade

```bash
sudo bash update.sh --force
```

— Maunting Studios
