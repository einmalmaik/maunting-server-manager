#!/usr/bin/env bash
# Die Testsuite auf einem Rechner starten, auf dem MSM **produktiv laeuft**.
#
# Warum es dieses Skript gibt: `backend/.env` traegt hier Produktivwerte, und
# `config.py` laedt sie ueber `pydantic-settings`. Deren Rangfolge stellt die
# Datei **ueber** Shell-Variablen — ein `MSM_COOKIE_CROSS_SITE=false` davor
# bewirkt nichts. Mit `cookie_cross_site=true` heissen alle Auth-Cookies
# `__Secure-…` und werden mit `secure=True` gesetzt; der TestClient spricht
# `http://testserver`, verwirft sie damit, und rund vierzehn Tests scheitern
# mit einem 401, dessen Ursache nirgends steht.
#
# In der CI faellt das nicht auf: dort gibt es keine `.env`, also greifen die
# Vorgabewerte (`cookie_cross_site=False`).
#
# Das Skript legt die Datei fuer die Dauer des Laufs beiseite und stellt sie
# per `trap` zurueck — auch bei Abbruch mit Strg-C oder wenn pytest scheitert.
#
# Aufruf:
#   scripts/run-backend-tests.sh                 # alles
#   scripts/run-backend-tests.sh tests/test_x.py # gezielt
#   scripts/run-backend-tests.sh tests/ -q       # mit pytest-Argumenten
set -uo pipefail

cd /opt/msm/backend || exit 1

ENVDATEI=".env"
BEISEITE=".env.testlauf-beiseite"

zurueck() {
    if [ -f "$BEISEITE" ]; then
        mv "$BEISEITE" "$ENVDATEI"
        echo "[run-backend-tests] .env zurueckgelegt"
    fi
}
# Auch bei Strg-C, kill oder Fehler: die Produktivdatei muss zurueck. Ohne das
# liefe das Panel nach einem abgebrochenen Testlauf ohne Konfiguration weiter.
trap zurueck EXIT INT TERM

if [ -f "$ENVDATEI" ]; then
    mv "$ENVDATEI" "$BEISEITE"
    echo "[run-backend-tests] .env beiseite gelegt (Produktivwerte)"
fi

# Dieselben Werte, die die CI setzt. Getrennte Datenbank, eigener Schluessel —
# nichts davon zeigt auf den Produktivbestand.
export MSM_DATABASE_URL="sqlite:///:memory:"
export MSM_SECRET_KEY="test-secret-key-32-chars-long!!!"
export MSM_DEBUG="true"

./venv/bin/python -m pytest "$@"
ERGEBNIS=$?

exit $ERGEBNIS
