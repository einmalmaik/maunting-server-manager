---
name: Shop an MSM anbinden
description: Der Betreiber hat einen Shop (WHMCS, Blesta, eigener) und will, dass eine Bestellung automatisch einen Gameserver anlegt. Nutzen, sobald jemand von Shop, Bestellung, Abo, Kündigung oder automatischer Serverbereitstellung spricht. Nicht nutzen für Fragen eines Kunden zu seinem eigenen gemieteten Server — der Kunde richtet nichts ein.
---

# Shop an MSM anbinden

Zwei Hälften. MSM kennt nur die eigene: Integration, Produkte, Rollen. Die
andere Hälfte — wann der Shop welchen Aufruf schickt — baut der Betreiber oder
sein Entwickler. Du richtest die erste ein und übergibst für die zweite den
Block aus `read_hoster_integration_guide` unverändert.

Geh in dieser Reihenfolge vor. Jeder Schritt braucht Werte aus dem
vorhergehenden, und keiner dieser Werte lässt sich erraten.

## 0. Erst nachsehen, dann reden

`read_hoster_setup`. Immer zuerst, auch wenn die Frage allgemein klingt.

Daraus kommt alles, was du sonst raten müsstest: vergebene Slugs, geeignete
Dienstbenutzer, vorhandene Produktkennungen und die Rollen, die **dieser
Benutzer** vergeben darf. Steht bei einer Liste `withheld`, gibt es sie, und du
darfst sie nur nicht sehen — sag das, statt sie für leer zu halten.

Existiert schon eine Integration, ist die Frage meist eine andere als
„einrichten": dann fehlt ein Produkt, ein Webhook-Secret oder der Shop schickt
etwas Falsches. Frag nach, bevor du eine zweite Integration vorschlägst.

## 1. Tarifrolle, falls die KI gestaffelt werden soll

Nur wenn der Betreiber danach fragt oder von unterschiedlichen KI-Kontingenten
je Tarif spricht. Sonst überspringen.

`propose_ai_tarif_role` legt eine globale Rolle **ohne jedes Recht** an, die nur
ein KI-Kontingent trägt. Das ist der einzige Weg, wie ein größerer Tarif mehr KI
bekommt: Kontingente hängen an globalen Rollen, nicht am Server und nicht am
Produkt.

Ein Feld auf `null` heißt unbegrenzt, nicht null. Setz nur, was genannt wurde.
Frag nach den Zahlen, statt sie zu wählen — ein geratenes Tageslimit merkt der
Kunde erst, wenn es greift.

## 2. Integration anlegen

`propose_hoster_integration`. Vier Werte entscheiden:

- **Kurzname (Slug)** — panelweit eindeutig, steht in jedem Auditeintrag und
  bildet den Benutzernamen-Präfix angelegter Kunden. Nachträglich ändern bricht
  die Zuordnung in bestehenden Protokollen.
- **Dienstbenutzer** — aus `service_user_candidates`. In seinem Namen legt der
  Shop die Server an; er begrenzt zugleich, welche Rollen die Integration
  überhaupt vergeben kann.
- **Webhook-Ziel** — HTTPS. Ohne Ziel meldet MSM dem Shop nie, dass ein Server
  fertig ist. Das Secret entsteht im selben Zug; ein Ziel ohne Secret stellt
  nichts zu.
- **Aufbewahrung nach Kündigung** — so viele Tage bleiben Server und Daten nach
  einer Kündigung erhalten. 0 heißt sofort löschbar.

Sag **vor** der Bestätigung, dass der API-Key genau einmal erscheint. Du
bekommst ihn nie zu sehen und kannst ihn nicht wiederholen; danach hilft nur
eine Rotation, und die macht den alten Schlüssel sofort ungültig.

## 3. Produkte zuordnen

`propose_hoster_product`, einmal je Tarif im Shop.

Die Produktkennung heißt **exakt** so wie im Shop. MSM-interne IDs kennt der
Shop nie — er schickt seine eigene Kennung, MSM findet den Rest. Frag den
Betreiber nach der Schreibweise, statt eine plausible zu bilden.

Leere Grenzen bedeuten die Voreinstellung des Blueprints, nicht null. Trägt das
Produkt eine Rolle, bekommt jeder Käufer sie für die Laufzeit seines Vertrags
und verliert sie bei Sperre oder Kündigung.

## 4. Den Block übergeben

`read_hoster_integration_guide` mit der Kennung aus Schritt 2. Was dabei
herauskommt, gibst du **unverändert** weiter: Adresse, Header, Endpunkte,
Zustände, Eventnamen und die real hinterlegten Produktkennungen. Erklär
ringsherum so ausführlich wie nötig, aber lass die Werte in Ruhe. Ein abgetippter
Header und ein angepasster Pfad sind die zwei häufigsten Gründe, warum eine
Anbindung nicht läuft.

Danach: der Shop ruft bei Bestellung `active`, bei Zahlungsverzug `suspended`,
bei Kündigung `terminated`. Mehr Zustände gibt es nicht.

## Was du nicht behaupten darfst

**Nichts über Formate, Header, Fehlercodes oder das Zustandsmodell aus dem
Gedächtnis.** Ruf `read_docs` mit `page: "hoster-api"` und lies den Abschnitt,
bevor du eine solche Aussage triffst. MSM ist nicht Pterodactyl und nicht
Pelican; was dort gilt, gilt hier fast nie, und der Betreiber kann den
Unterschied nicht sehen.

**Nicht behaupten, der Shop sei fertig angebunden.** Du richtest die
Panelseite ein. Ob der Shop wirklich aufruft, weiß erst die erste echte
Bestellung — bis dahin ist alles, was du sagen kannst, „panelseitig steht es".

**Keine Zahlungs- oder Abrechnungslogik.** MSM kennt keine Rechnungen, keine
Beträge und keine Zahlungsfristen. Der Shop entscheidet, wann ein Vertrag
gesperrt wird; MSM setzt es um.

**Nicht selbst prüfen wollen, ob das Webhook-Ziel erreichbar ist.** Das kann
MSM nicht messen. Was du sagen kannst: ob Ziel und Secret hinterlegt sind.
