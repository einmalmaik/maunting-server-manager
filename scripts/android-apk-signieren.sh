#!/usr/bin/env bash
# Signiert die unsignierten Release-APKs der Android-App.
#
# Bis 5.0.4 lag der Signaturschluessel samt Passwort im oeffentlichen Repo.
# Wer ihn hat, kann ein APK bauen, das Android als Update der App annimmt und
# das an ihre Daten kommt. Deshalb die Rotation (APK Signature Scheme v3):
#   - v2 signiert weiter der alte Schluessel, fuer Android 8 (API 26/27), das
#     keine Rotation kennt,
#   - ab Android 9 (API 28) gilt der neue Schluessel. Die Abfolge
#     (smart-system/android-signatur/abfolge.bin) belegt, dass der alte ihn
#     eingesetzt hat, und nimmt dem alten das Recht, wieder Updates zu stellen
#     (rollback: false). Ein Geraet, das einmal ein so signiertes Update hatte,
#     weist Updates nur mit dem alten Schluessel ab.
#
# Aufruf: scripts/android-apk-signieren.sh <app-…-release-unsigned.apk>...
# Das signierte APK liegt danach neben dem unsignierten, ohne "-unsigned";
# das unsignierte wird entfernt.
#
# Umgebung (in der Pipeline aus den Repository-Secrets):
#   MSS_ANDROID_ALT_KEYSTORE_B64, MSS_ANDROID_ALT_KEYSTORE_PASSWORT  (alias mss-release)
#   MSS_ANDROID_KEYSTORE_B64,     MSS_ANDROID_KEYSTORE_PASSWORT      (alias mss-release-2026)
set -euo pipefail

: "${MSS_ANDROID_ALT_KEYSTORE_B64:?fehlt}" "${MSS_ANDROID_ALT_KEYSTORE_PASSWORT:?fehlt}"
: "${MSS_ANDROID_KEYSTORE_B64:?fehlt}" "${MSS_ANDROID_KEYSTORE_PASSWORT:?fehlt}"
[ "$#" -gt 0 ] || { echo "Keine APKs angegeben." >&2; exit 1; }

hier="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
abfolge="$hier/smart-system/android-signatur/abfolge.bin"
werkzeuge="${ANDROID_BUILD_TOOLS:-$(ls -d "${ANDROID_HOME:?ANDROID_HOME fehlt}"/build-tools/* | sort -V | tail -n 1)}"
# zipalign -P (16-KB-Seiten) gibt es erst ab den Build-Tools 35.
apksigner="$werkzeuge/apksigner"
zipalign="$werkzeuge/zipalign"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
printf '%s' "$MSS_ANDROID_ALT_KEYSTORE_B64" | base64 -d > "$tmp/alt.keystore"
printf '%s' "$MSS_ANDROID_KEYSTORE_B64" | base64 -d > "$tmp/neu.keystore"

for roh in "$@"; do
  case "$roh" in
    *-unsigned.apk) ziel="${roh%-unsigned.apk}.apk" ;;
    *) echo "Kein unsigniertes APK: $roh" >&2; exit 1 ;;
  esac
  "$zipalign" -f -P 16 4 "$roh" "$tmp/ausgerichtet.apk"
  "$apksigner" sign \
    --ks "$tmp/alt.keystore" --ks-key-alias mss-release --ks-pass env:MSS_ANDROID_ALT_KEYSTORE_PASSWORT \
    --next-signer \
    --ks "$tmp/neu.keystore" --ks-key-alias mss-release-2026 --ks-pass env:MSS_ANDROID_KEYSTORE_PASSWORT \
    --lineage "$abfolge" --rotation-min-sdk-version 28 \
    --out "$ziel" "$tmp/ausgerichtet.apk"
  "$apksigner" verify --verbose "$ziel" | grep -q "Verified using v3 scheme (APK Signature Scheme v3): true" \
    || { echo "v3-Signatur fehlt: $ziel" >&2; exit 1; }
  rm -f "$roh" "$ziel.idsig"
  echo "Signiert: $ziel"
done
