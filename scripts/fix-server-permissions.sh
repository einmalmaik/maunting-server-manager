#!/usr/bin/env bash
#
# Panel und Spielprozesse teilen sich die Serverdateien — ueber eine Gruppe
# statt ueber weltweite Schreibrechte.
#
# ── Das Problem ──────────────────────────────────────────────────────────
#
# Unter Rootless Docker laufen Panel und Spielprozess als **verschiedene
# Benutzer**: das Panel als `msm` (uid 994), der Server als gemappte uid aus
# `/etc/subuid` (z. B. 297607 = 296608 + Container-uid 999). Beide muessen
# dieselben Dateien lesen und schreiben — der Mensch im Dateimanager, die KI
# ueber `propose_config_patch`, und der Spielprozess selbst.
#
# Der schnelle Weg dorthin war `0666`/`0777`: jeder darf alles. Das
# funktioniert, ist aber weiter geoeffnet als noetig, und es faellt bei jedem
# neuen Server wieder an.
#
# ── Die Loesung ──────────────────────────────────────────────────────────
#
# Fuer jede gemappte GID, die Serverdateien besitzt, wird eine benannte
# Host-Gruppe angelegt (`msm-srv-<gid>`), `msm` wird Mitglied, und die
# Serververzeichnisse bekommen:
#
#   * Gruppenbesitz auf diese GID
#   * `g+rwX` — die Gruppe darf lesen und schreiben
#   * `g+s` auf Verzeichnissen — **neu angelegte** Dateien erben die Gruppe
#     automatisch, auch die, die der Spielprozess selbst schreibt
#   * `o-rwx` — fuer alle anderen bleibt zu
#
# Das `setgid`-Bit ist der Teil, der das Problem dauerhaft loest statt es
# einmal aufzuraeumen: ohne es traegt jede neue Datei wieder die Gruppe ihres
# Erzeugers, und in ein paar Tagen steht man wieder hier.
#
# ── Aufruf ───────────────────────────────────────────────────────────────
#
#   sudo scripts/fix-server-permissions.sh            # alle Server
#   sudo scripts/fix-server-permissions.sh --dry-run  # nur zeigen
#   sudo scripts/fix-server-permissions.sh /opt/msm/servers/ark_ascended_107
#
# Nach dem ersten Lauf muss `msm-panel` einmal neu starten: ein Prozess erbt
# seine Zusatzgruppen beim Start und sieht spaetere Aenderungen nicht.
#
set -euo pipefail

PANEL_USER="${PANEL_USER:-msm}"
SERVERS_DIR="${SERVERS_DIR:-/opt/msm/servers}"
TROCKEN=0
ZIELE=()

for arg in "$@"; do
    case "$arg" in
        --dry-run|-n) TROCKEN=1 ;;
        -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
        *) ZIELE+=("$arg") ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "Dieses Skript braucht root (chgrp/chmod auf fremde Dateien)." >&2
    exit 1
fi

if ! id "$PANEL_USER" >/dev/null 2>&1; then
    echo "Benutzer '$PANEL_USER' gibt es nicht." >&2
    exit 1
fi

if [[ ${#ZIELE[@]} -eq 0 ]]; then
    mapfile -t ZIELE < <(find "$SERVERS_DIR" -mindepth 1 -maxdepth 1 -type d | sort)
fi

if [[ ${#ZIELE[@]} -eq 0 ]]; then
    echo "Keine Serververzeichnisse unter $SERVERS_DIR gefunden."
    exit 0
fi

lauf() {
    if [[ $TROCKEN -eq 1 ]]; then
        echo "    [trocken] $*"
    else
        "$@"
    fi
}

echo "Panel-Benutzer: $PANEL_USER ($(id -u "$PANEL_USER"):$(id -g "$PANEL_USER"))"
[[ $TROCKEN -eq 1 ]] && echo "TROCKENLAUF — es wird nichts geaendert."
echo

NEUE_GRUPPEN=()
ANGELEGT=()
MITGLIED=()

for pfad in "${ZIELE[@]}"; do
    [[ -d "$pfad" ]] || { echo "  uebersprungen (kein Verzeichnis): $pfad"; continue; }
    name="$(basename "$pfad")"

    # Wem gehoert dieser Server? Die GID des Wurzelverzeichnisses ist die
    # Gruppe, unter der der Spielprozess schreibt.
    gid="$(stat -c '%g' "$pfad")"
    uid="$(stat -c '%u' "$pfad")"

    # Gehoert das Verzeichnis bereits dem Panel, gibt es nichts zu teilen —
    # dann ist der Server nicht containerisiert oder laeuft als msm.
    if [[ "$gid" == "$(id -g "$PANEL_USER")" ]]; then
        echo "  $name: gehoert bereits $PANEL_USER — nur Rechte pruefen"
        lauf find "$pfad" -type d -exec chmod u+rwx,g+rwx,g+s,o-rwx {} +
        lauf find "$pfad" -type f -exec chmod u+rw,g+rw,o-rwx {} +
        continue
    fi

    gruppe="msm-srv-${gid}"
    # Im Trockenlauf legt `groupadd` nichts an — ohne dieses Gedaechtnis
    # meldete das Skript dieselbe Gruppe fuer jeden Server erneut als "neu",
    # und der Leser haette nicht gesehen, dass es **eine** Gruppe fuer alle
    # Server desselben Mappings ist.
    if getent group "$gid" >/dev/null 2>&1; then
        gruppe="$(getent group "$gid" | cut -d: -f1)"
    elif [[ " ${ANGELEGT[*]-} " == *" $gid "* ]]; then
        :  # in diesem Lauf schon behandelt
    else
        echo "  $name: lege Gruppe $gruppe (gid $gid) an"
        lauf groupadd -g "$gid" "$gruppe"
        ANGELEGT+=("$gid")
        NEUE_GRUPPEN+=("$gruppe")
    fi

    if ! id -nG "$PANEL_USER" | tr ' ' '\n' | grep -qx "$gruppe" \
       && [[ " ${MITGLIED[*]-} " != *" $gruppe "* ]]; then
        echo "  $name: $PANEL_USER wird Mitglied von $gruppe"
        lauf usermod -aG "$gruppe" "$PANEL_USER"
        MITGLIED+=("$gruppe")
        NEUE_GRUPPEN+=("$gruppe")
    fi

    echo "  $name: Gruppenbesitz $gid, g+rwX, setgid auf Verzeichnisse, o-rwx"
    # `chgrp` statt `chown`: der **Eigentuemer** bleibt beim Spielprozess.
    # Nur die Gruppe wird geteilt, und genau das ist der Unterschied zu 0666.
    lauf chgrp -R "$gid" "$pfad"
    lauf find "$pfad" -type d -exec chmod u+rwx,g+rwx,g+s,o-rwx {} +
    lauf find "$pfad" -type f -exec chmod u+rw,g+rw,o-rwx {} +
    # Ausfuehrbare Dateien behalten ihr x — Spielserver starten sonst nicht.
    lauf find "$pfad" -type f -perm -u+x -exec chmod g+x {} +
done

echo
if [[ ${#NEUE_GRUPPEN[@]} -gt 0 && $TROCKEN -eq 0 ]]; then
    echo "Neue Gruppenmitgliedschaften fuer $PANEL_USER:"
    printf '  %s\n' "${NEUE_GRUPPEN[@]}" | sort -u
    echo
    echo "WICHTIG: msm-panel neu starten, sonst sieht der laufende Prozess die"
    echo "neuen Gruppen nicht (Zusatzgruppen werden beim Start geerbt):"
    echo
    echo "    systemctl restart msm-panel"
fi
echo "Fertig."
