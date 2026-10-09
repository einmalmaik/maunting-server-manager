#!/usr/bin/env bash
# Signiert die unsignierten Release-APKs des Maunting Secure Browsers.
#
# Der Schluessel liegt nur in den Repository-Secrets, nie im Repo
# (AGENTS.md, Punkt 70). Anders als beim Smart System gibt es keinen
# verbrannten Vorgaenger, also einen Signierer ohne Abfolge. Der Browser laeuft
# ab Android 10 (minSdk 29); dort zaehlt die v3-Signatur, und nur die meldet
# `apksigner verify` fuer dieses APK.
#
# Aufruf: scripts/android-apk-signieren-msb.sh <app-…-release-unsigned.apk>...
# Das signierte APK liegt danach neben dem unsignierten, ohne "-unsigned";
# das unsignierte wird entfernt.
#
# Umgebung (in der Pipeline aus den Repository-Secrets):
#   MSB_ANDROID_KEYSTORE_B64, MSB_ANDROID_KEYSTORE_PASSWORT  (alias msb-release)
set -euo pipefail

: "${MSB_ANDROID_KEYSTORE_B64:?fehlt}" "${MSB_ANDROID_KEYSTORE_PASSWORT:?fehlt}"
[ "$#" -gt 0 ] || { echo "Keine APKs angegeben." >&2; exit 1; }

werkzeuge="${ANDROID_BUILD_TOOLS:-$(ls -d "${ANDROID_HOME:?ANDROID_HOME fehlt}"/build-tools/* | sort -V | tail -n 1)}"
# zipalign -P (16-KB-Seiten) gibt es erst ab den Build-Tools 35.
apksigner="$werkzeuge/apksigner"
zipalign="$werkzeuge/zipalign"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
printf '%s' "$MSB_ANDROID_KEYSTORE_B64" | base64 -d > "$tmp/msb.keystore"

for roh in "$@"; do
  case "$roh" in
    *-unsigned.apk) ziel="${roh%-unsigned.apk}.apk" ;;
    *) echo "Kein unsigniertes APK: $roh" >&2; exit 1 ;;
  esac
  "$zipalign" -f -P 16 4 "$roh" "$tmp/ausgerichtet.apk"
  "$apksigner" sign \
    --ks "$tmp/msb.keystore" --ks-key-alias msb-release --ks-pass env:MSB_ANDROID_KEYSTORE_PASSWORT \
    --out "$ziel" "$tmp/ausgerichtet.apk"
  "$apksigner" verify --verbose "$ziel" | grep -q "Verified using v3 scheme (APK Signature Scheme v3): true" \
    || { echo "v3-Signatur fehlt: $ziel" >&2; exit 1; }
  rm -f "$roh" "$ziel.idsig"
  echo "Signiert: $ziel"
done
