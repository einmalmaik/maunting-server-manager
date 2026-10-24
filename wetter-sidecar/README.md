# Wetter-Sidecar für Maunting Pontis
#
# Das Gerät fragt ausschließlich MSM. Der Sidecar holt globale offene
# Modelldaten (NOAA GFS 0,50°) und hält sie unter ./daten. Es werden keine
# Nutzerdaten an Dritte gesendet.
#
# DWD-Warnungen: Der Sidecar lädt die offenen Warnpolygone des DWD
# (`dwd:Warnungen_Gemeinden_vereinigt`, WFS) serverseitig, hält sie zehn
# Minuten im Speicher und ordnet sie Punkten lokal zu. DWD erhält dabei nur
# eine allgemeine WFS-Anfrage des Servers, keinen Standort eines Nutzers.
#
# Endpunkte (nur Loopback):
#   GET /health
#   GET /orte?q=10115 (oder Ort / PLZ weltweit: 90210, 75001, Berlin)
#   GET /wetter?lat=52.52&lon=13.405
#
# Quellen und Lizenzen:
#   NOAA GFS – öffentliche Daten der NOAA/NCEP (public domain).
#   Ortsliste & Postleitzahlen – GeoNames cities5000 & postalCodes, CC BY 4.0 (https://www.geonames.org/).
#   DWD-Warnungen – Deutscher Wetterdienst, GeoNutzV; Pflichtzeile
#   „Quelle: Deutscher Wetterdienst“.
#
# Erststart: Der Sidecar lädt die Postleitzahlen (postalCodes.zip) in eine lokale
# SQLite-Datenbank (plz.sqlite), die Ortsliste und den aktuellen GFS-Lauf. Bis
# dahin antwortet /health mit bereit=false; MSM zeigt dann „wird vorbereitet“.
