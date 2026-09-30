# Signaturabfolge der Android-App

`abfolge.bin` ist die Abfolge der Signaturzertifikate (APK Signature Scheme v3,
`apksigner rotate`). Sie enthält nur Zertifikate und die Unterschrift des
früheren Schlüssels unter dem neuen, keinen privaten Schlüssel.

1. `mss-release`, SHA-256 `524432a5955ded0d254420851b9bcb86fdcb31a48ba12bdd7b6bf82d6fa8198c`.
   Lag bis 5.0.4 samt Passwort öffentlich im Repo. Fähigkeiten: installierte
   Daten und Berechtigungen ja, Rollback nein.
2. `mss-release-2026`, SHA-256 `91fb4eb5a1624ce53114af904e3fc178e98bf53f8bae13333188a5548308f822`.

Signiert wird mit `scripts/android-apk-signieren.sh` in der Release-Pipeline.
