# MSM LiveKit Sidecar

Medienserver für Sprach-, Video- und Gruppenanrufe im Messenger.

## Zweck

Vor LiveKit sprachen zwei Browser direkt miteinander. Das funktionierte, solange
beide Seiten erreichbar waren, und scheiterte an strengen NATs, in Mobilfunknetzen
und ab dem dritten Teilnehmer. Der Sidecar nimmt die Ströme entgegen und verteilt
sie weiter (Selective Forwarding). Jeder Client hält damit genau eine Verbindung,
egal wie viele im Raum sind.

## Was er sieht und was nicht

Medien sind Ende-zu-Ende verschlüsselt: die Browser verschlüsseln jeden Frame mit
einem Raumschlüssel, den der Sidecar nicht kennt und nicht bekommt. Er leitet
Chiffrat weiter.

Sichtbar sind ihm dagegen Raumnamen, Teilnehmerkennungen und Zeiten. Raumnamen
sind deshalb Zufallstoken und keine Gruppen-IDs. Aufzeichnung, Egress und ein
eigener TURN-Dienst sind abgeschaltet.

## Ports

| Port | Protokoll | Sichtbarkeit | Zweck |
|---|---|---|---|
| 7880 | TCP | nur 127.0.0.1 | Signalisierung, von Caddy als `wss://<domain>/livekit` veröffentlicht |
| 7881 | TCP | öffentlich | Medien-Rückfall für Netze, die UDP sperren |
| 7882 | UDP | öffentlich | Medien (ein Mux-Port für alle Teilnehmer) |

`install.sh` öffnet 7881/tcp und 7882/udp in UFW. Ohne diese beiden Regeln baut
sich zwar die Signalisierung auf, aber es kommt kein Ton an.

## Schlüssel

`install.sh` erzeugt Schlüssel und Geheimnis einmalig und schreibt sie in
`.env` (`LIVEKIT_KEYS`) sowie in die Backend-Environment
(`MSM_LIVEKIT_API_KEY`, `MSM_LIVEKIT_API_SECRET`). Beide Seiten müssen dasselbe
Paar tragen. Zum Rotieren: neues Paar an beiden Stellen eintragen,
`msm-livekit.service` und `msm-panel.service` neu starten. Laufende Gespräche
brechen dabei ab.

## Statt des Sidecars ein externer LiveKit

Im Panel unter *Einstellungen → Messenger* lässt sich auf LiveKit Cloud oder
einen eigenen Server umstellen (Adresse, API-Key, API-Secret). Der Sidecar darf
dann laufen bleiben; das Panel spricht ihn nicht mehr an.
