"""Entfernt Zugangsdaten aus Text, bevor er gespeichert oder verschickt wird.

Eigenes Modul, weil die Funktion an neun Stellen gebraucht wird und mit
Kontextaufbau nichts zu tun hat. Vorher lag sie in `ai_context_service` — und
weil `ai_memory_service` sie auf Modulebene importierte, waehrend
`ai_context_service` seinerseits `ai_memory_service` brauchte, bestand ein
Importzyklus. Er krachte nur deshalb nicht, weil eine der beiden Richtungen
verzoegert in einer Funktion stand. Wer diesen verzoegerten Import fuer
Unordnung hielt und ihn nach oben zog, brachte das Panel zum Stillstand.

Die Muster sind bewusst konservativ: lieber ein `[REDACTED]` zu viel als ein
Token in einem Log, einer Zusammenfassung oder einer Anfrage an einen externen
KI-Anbieter.

Zwei Funktionen, weil es zwei Arten von Text gibt:

* `redact_sensitive_text` — ueberall. Zugangsdaten und E-Mail-Adressen.
* `redact_freetext`       — zusaetzlich fremde IP-Adressen, fuer Text, der von
  aussen in den Server kam (Logzeilen, Dateiinhalte, Vorfallbeschreibungen).

Die Trennung ist kein Feinschliff, sondern notwendig. Eine IP kann beides sein:
die Adresse eines Spielers in einer Logzeile — personenbezogen, ohne jeden
Nutzen fuer die Diagnose — oder die Bind-Adresse des Servers in den
Netzwerkangaben, ohne die sich eine falsche Bindung weder erkennen noch
berichtigen laesst. Ein gemeinsames Muster fuer beides muesste eines von
beiden falsch behandeln.
"""

from __future__ import annotations

import ipaddress
import re


#: Zuweisungen der Form ``SCHLUESSEL = wert``, in den Schreibweisen, die in
#: Spieleserver-Konfigurationen tatsächlich vorkommen.
#:
#: Hier stand einmal ``\b(password|…)\b\s*[:=]``. Das sah vollständig aus und
#: hatte zwei Löcher, die genau die häufigsten Fälle trafen:
#:
#: 1. ``\b`` scheitert am Unterstrich, weil der ein Wortzeichen ist. Damit ging
#:    ``RCON_PASSWORD=hunter2`` unverändert an den KI-Anbieter — und das ist
#:    nicht irgendeine Schreibweise, sondern die übliche für Umgebungsvariablen.
#:    Ebenso ``MYSQL_ROOT_PASSWORD``, ``OPENAI_API_KEY``, ``DB_SECRET``.
#: 2. In JSON steht zwischen Schlüssel und Doppelpunkt ein Anführungszeichen,
#:    das ``\s*[:=]`` nicht zuließ: ``{"password": "hunter2"}`` blieb stehen.
#:
#: Deshalb jetzt: ein optionaler Präfix aus Wortteilen vor dem Schlüsselwort,
#: und optionale Anführungszeichen um Trennzeichen und Wert. Die Anführungs-
#: zeichen werden mitgeschrieben, damit aus gültigem JSON wieder gültiges JSON
#: wird — der Text geht als Kontext an ein Modell und soll lesbar bleiben.
#:
#: 3. Ein drittes Loch, gefunden am 14.08.2026: der Präfix verlangte hinter
#:    jedem Wortteil ein Trennzeichen (``[._-]``), und davor stand ein
#:    ``(?<![A-Za-z0-9])``. Beides zusammen ließ jede zusammengeschriebene
#:    Schreibweise durch — ``ServerAdminPassword=geheim``, ``AdminSecret``,
#:    ``rconPassword``. Das ist ausgerechnet die Schreibweise der INI-Dateien
#:    von ARK, Palworld, DayZ und SCUM, also der Dateien, die ``read_config``
#:    liest und weiterreicht. Der Präfix frisst deshalb jetzt auch Wortteile
#:    ohne Trennzeichen.
#:
#: Die Grenze davor bleibt — aber sie umfasst jetzt **denselben** Zeichenvorrat
#: wie der Präfix. Das ist der Punkt, an dem die alte Fassung scheiterte: sie
#: verbot mit ``(?<![A-Za-z0-9])`` nur Buchstaben und Ziffern, der Präfix konnte
#: aber gar nicht bis zum Wortanfang laufen, also begann der Versuch mitten im
#: Wort und fiel an der Grenze durch. Jetzt läuft der Präfix bis zum Wortanfang,
#: und die Grenze prüft genau dort.
#:
#: Ohne diese Grenze wäre das Muster nicht nur ungenauer, sondern quadratisch:
#: der Präfix darf an **jeder** Stelle eines langen Wortes neu ansetzen und
#: jedes Mal bis zum Ende laufen. Gemessen an einer Zeichenkette aus 50.000
#: Wortzeichen — wie sie in einer Logzeile oder einer Konfigurationsdatei
#: vorkommt — waren das 101 Sekunden statt 0,002. Mit der Grenze gibt es je Wort
#: genau einen Ansatzpunkt.
#: Der Wortkern, der einen Schluessel zu einem Geheimnis macht — **eine**
#: Liste fuer beide Muster (Zuweisung und alleinstehender Woerterbuchschluessel).
#: Waeren es zwei, wuerde die eine irgendwann erweitert und die andere nicht;
#: genau so fehlte hier `credentials` im Plural, waehrend der Kommentar in
#: `ai_stream_service._ergebnis_schwaerzen` die Teilbaum-Schwaerzung
#: ausgerechnet mit diesem Wort begruendete.
#:
#: Vier Nachtraege vom 19.08.2026, jeder ein gemessenes Loch:
#:
#: * ``secret`` durfte kein Suffix tragen: ``SECRET_KEY``, ``SECRET_KEY_BASE``
#:   und ``AWS_SECRET_ACCESS_KEY`` gingen unveraendert an den Anbieter, weil
#:   das Kernwort am Ende stehen musste. Wortteile hinter ``secret`` zaehlen
#:   jetzt mit, aber nur nach einem Trenner — ``secretary=anna`` ist ein Name
#:   und bleibt stehen (der Bestandstest haelt das fest). ``SecretKey`` ohne
#:   Trenner faengt die ``…key``-Regel darunter.
#: * ``pass``/``pwd`` fehlten ganz (``db_pass=…``). Sie zaehlen nur als
#:   eigenes Wortteil (Trenner davor oder Wortanfang): ``Compass=N`` und
#:   ``bypass=true`` sind Spieleinstellungen und bleiben lesbar.
#: * ``…_KEY`` zaehlt, wenn das Wort davor Geheimnis- oder Krypto-Bedeutung
#:   traegt (``LICENSE_KEY``, ``EncryptionKey``, ``SessionKey``). Bewusst
#:   **keine** generische ``_key``-Regel: ``skill_key``, ``message_key`` und
#:   ``external_product_key`` sind Verweise, keine Geheimnisse, und
#:   ``api_key_hint`` ist die absichtlich zeigbare Kurzform. Eine Denyliste
#:   laesst schlimmstenfalls etwas durch — eine generische Regel macht
#:   Werkzeugantworten unlesbar, und das faellt erst auf, wenn ein Feature
#:   bricht.
#: * ``credential`` galt nur im Singular; ``credentials`` traegt jetzt auch
#:   Wortteile dahinter (``credentials_file``).
#: * ``passwort``/``kennwort`` fehlten ganz — das Panel ist deutschsprachig,
#:   und "das passwort: hunter2 bitte merken" ist genau der Satz, den ein
#:   Benutzer in den Chat schreibt.
_GEHEIM_KERN = (
    r"(?:password|passwd|passwort|kennwort"
    r"|(?:(?<=[._-])|(?<![A-Za-z0-9._-]))(?:pass|pwd)"
    r"|secret(?:[._-][A-Za-z0-9._-]*)?"
    r"|token"
    r"|api[_-]?key"
    r"|authorization"
    r"|credentials?(?:[._-][A-Za-z0-9._-]*)?"
    r"|(?:license|licence|access|encryption|signing|session|master|private|auth|secret)[._-]?key"
    r")"
)

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)"
    r"(?<![A-Za-z0-9._-])"
    r"(?P<key>"
    r"[A-Za-z0-9._-]*"
    + _GEHEIM_KERN +
    r")"
    # Trennzeichen, davor optional das schliessende Anführungszeichen des
    # Schlüssels. Es wird mitgenommen und unverändert wieder ausgegeben.
    r"(?P<sep>[\"']?\s*[:=]\s*)"
    r"(?P<quote>[\"'])?"
    # Mit Anführungszeichen läuft der Wert bis zum nächsten; ohne bis zum
    # nächsten Trennzeichen. Ein Wert in Anführungszeichen darf Leerzeichen
    # enthalten — ein Passwort mit Leerzeichen wäre sonst nur halb entfernt.
    # Der Wert steht in einer eigenen Gruppe, damit `enthaelt_zugangsdaten`
    # ihn ansehen kann: die Schwaerzung ersetzt auch "Token: 2", die
    # Abweisung beim Merken soll das nicht (siehe dort).
    r"(?(quote)(?P<wq>[^\"'\n]*)[\"']|(?P<wu>[^\s,;]+))"
)

#: XML-Element: ``<password>geheim</password>``.
#:
#: Die Zuweisungsform oben deckt INI (``key=wert``), JSON (``"key": "wert"``)
#: und YAML (``key: wert``) ab — XML fiel durch, weil dort weder ``=`` noch
#: ``:`` zwischen Schluessel und Wert steht. Das blieb unbemerkt, solange
#: dauerhafte Werte nur fuer INI-artige Dateien moeglich waren; mit dem Schritt
#: auf jedes Dateiformat waere es eine Luecke geworden.
#:
#: Der Schluessel kommt aus derselben ``_GEHEIM_KERN``-Liste wie die uebrigen
#: Muster — ein neues Geheimniswort wird an genau einer Stelle eingetragen und
#: wirkt in allen Formen.
_SECRET_XML_ELEMENT_RE = re.compile(
    r"(?i)"
    r"(?P<open><\s*(?P<key>[A-Za-z0-9._-]*" + _GEHEIM_KERN + r")\b[^>]*>)"
    r"(?P<wert>[^<]*)"
    r"(?P<close></\s*(?P=key)\s*>)"
)

#: XML-Attribut in zwei Auspraegungen:
#:   ``<ServerPassword value="geheim"/>``          — Schluessel ist der Tagname
#:   ``<property name="password" value="geheim"/>`` — Schluessel im Nachbarattribut
#:
#: Die zweite Form steht so in Minecraft-, Terraria- und diversen
#: UE-Konfigurationen. Gesucht wird deshalb nicht nach dem Tag, sondern nach
#: einem Geheimniswort **irgendwo im Element** vor einem ``value=``.
_SECRET_XML_ATTRIBUT_RE = re.compile(
    r"(?i)"
    r"(?P<vorn><[^>]*?[A-Za-z0-9._-]*" + _GEHEIM_KERN + r"\b[^>]*?"
    r"\bvalue\s*=\s*)"
    r"(?P<quote>[\"'])"
    r"(?P<wert>[^\"']*)"
    r"(?P=quote)"
)
_AUTHORIZATION_BEARER_RE = re.compile(
    r"(?i)\bauthorization\b\s*[:=]\s*bearer\s+[A-Za-z0-9._~+\-/]+=*"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+\-/]+=*")
_KNOWN_TOKEN_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"
)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL,
)

#: E-Mail-Adressen. Sie stehen in Serverlogs (Registrierungen, Whitelist-Fehler,
#: Mailversand eines Plugins) und in Konfigurationsdateien, und sie sind
#: personenbezogen ohne jeden Zweifel und ohne jeden Nutzen fuer eine Diagnose.
#: Deshalb **global**: es gibt keinen Fall, in dem die KI eine echte Adresse
#: braucht, um einen Server wieder zum Laufen zu bringen.
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)

#: IP-Adressen — **nicht** global, sondern nur in Freitext (siehe
#: `redact_freetext`). Der Unterschied ist der Zweck: die Adresse eines Spielers
#: in einer Logzeile ist personenbezogen, die Bind-Adresse des Servers in
#: `read_server_network` ist eine Betriebsangabe, die die KI braucht, um eine
#: falsche Bindung zu erkennen und mit `propose_bind_ip_update` zu berichtigen.
#: Ein gemeinsames Muster fuer beides wuerde entweder die eine durchlassen oder
#: die andere unbrauchbar machen.
#:
#: Stehen bleibt, was **keine Person bezeichnet**: `0.0.0.0` und `::` (jede
#: Adresse), Loopback, Link-Local und die privaten Bereiche (RFC 1918, ULA).
#: Eine private Adresse in einem Gameserver-Log ist die Bindeadresse des Dienstes
#: oder ein anderer Rechner im Netz des Betreibers — also dessen eigene Anlage.
#: Wer sie schwaerzt, nimmt der Diagnose genau die Zeile, an der man erkennt,
#: dass ein Dienst auf der falschen Adresse horcht. Das ist der haeufigste Fall
#: von "laeuft, aber niemand kommt drauf", und ihn unlesbar zu machen schuetzt
#: niemanden.
#:
#: Geschwaerzt wird also nur, was oeffentlich routbar ist — und das ist bei einer
#: Adresse in einer Verbindungszeile praktisch immer ein Spieler.
_IPV4_RE = re.compile(r"(?<![\w.])((?:\d{1,3}\.){3}\d{1,3})(?![\w.])")
#: IPv6 wird bewusst **grob** eingesammelt und erst danach geprueft. Ein
#: regulaerer Ausdruck, der die zusammengefasste Schreibweise (`2a02:8109::1`)
#: vollstaendig und korrekt trifft, ist berüchtigt lang und trifft trotzdem
#: Sonderfaelle nicht — ein erster Entwurf hier liess ausgerechnet jede Adresse
#: mit `::` durch, also die uebliche Schreibweise.
#:
#: Stattdessen: alles einsammeln, was nach Hex und Doppelpunkten aussieht, und
#: `ipaddress` entscheiden lassen. Was keine Adresse ist — eine Uhrzeit `12:34:56`
#: in einer Logzeile etwa — faellt bei der Pruefung durch und bleibt stehen.
_IPV6_RE = re.compile(
    r"(?<![\w:.])([A-Fa-f0-9]{0,4}(?::[A-Fa-f0-9]{0,4}){2,7})(?![\w:.])"
)


#: Derselbe Schluesselteil wie in `_SECRET_ASSIGNMENT_RE`, nur allein stehend.
#:
#: Gebraucht wird er dort, wo Schluessel und Wert **nicht** in einer Zeichenkette
#: stehen, sondern als Paar in einer Datenstruktur — also in jedem
#: Werkzeugergebnis, das ein Woerterbuch liefert. `read_blueprint` gibt
#: ``{"runtime": {"env": {"RCON_PASSWORD": "hunter2"}}}`` zurueck; die Rekursion
#: der Schwaerzung reicht dort nur ``"hunter2"`` weiter, und darauf passt kein
#: Zuweisungsmuster. Das Passwort ging im Klartext an den Modellanbieter.
#:
#: Ein zweites Muster und keine zweite Wortliste: waeren es zwei Listen, wuerde
#: die eine irgendwann um `credential` erweitert und die andere nicht. Genau das
#: war passiert — hier stand `credential` nur im Singular, und der Teilbaum
#: unter ``credentials`` ging im Klartext hinaus, waehrend der Kommentar an der
#: Aufrufstelle die Schwaerzung ausgerechnet mit diesem Wort begruendete.
#: Seitdem teilen sich beide Muster den Kern `_GEHEIM_KERN`.
_SECRET_KEY_RE = re.compile(
    r"(?i)^[A-Za-z0-9._-]*" + _GEHEIM_KERN + r"$"
)


def ist_geheimer_schluessel(name: object) -> bool:
    """Ob ein Woerterbuchschluessel einen Wert bezeichnet, der nicht hinausdarf."""
    return isinstance(name, str) and bool(_SECRET_KEY_RE.match(name.strip()))


def _wert_ist_geheimnis(match: re.Match[str]) -> bool:
    """Ob der Wert einer getroffenen Zuweisung nach einem echten Geheimnis aussieht.

    Das Zuweisungsmuster kennt nur den Schluessel; ob dahinter ein Passwort
    steht oder eine Zahl, sieht erst der Wert. Reine Zahlen sind Kontingente,
    Jahreszahlen und Budgets ("Serverwechsel-Token: 2", "Token-Budget: 100000")
    — kein Mensch legt sie als Zugangsdatum an, aber jede davon machte einen
    Gedaechtniseintrag unspeicherbar. Sehr kurze Werte ("ja", "an") sind
    Schalter. Alles andere gilt als Geheimnis — im Zweifel wird abgewiesen.
    """
    wert = (match.group("wq") if match.group("quote") else match.group("wu")) or ""
    wert = wert.strip()
    if not wert or wert.isdigit():
        return False
    return len(wert) >= 4


def enthaelt_zugangsdaten(text: str) -> bool:
    """Ob ein Text echte Zugangsdaten traegt — fuer die Abweisung beim Merken.

    Bewusst **enger** als `redact_sensitive_text`, und das ist kein
    Widerspruch, sondern die Folge der verschiedenen Ausgaenge. Die
    Schwaerzung darf grosszuegig sein: ihr Ergebnis bleibt lesbar, ein
    faelschlich ersetzter Wert kostet eine Logzeile. Eine Abweisung hat
    diesen Ausweg nicht — sie macht den ganzen Eintrag unspeicherbar und
    behauptet dem Benutzer gegenueber einen Grund. Zwei gemessene
    Fehlabweisungen (19.08.2026), beide mit der Meldung "keine Zugangsdaten":

    * "Rechnungen gehen an billing@firma.de" — eine E-Mail-Adresse ist ein
      personenbezogenes Datum, aber kein Zugangsdatum. Beim Merken legt der
      Benutzer sie **absichtlich** in seinen eigenen, einwilligungspflichtigen
      Vorrat; die Schwaerzung fuer Logs und Werkzeugtext bleibt davon
      unberuehrt.
    * "Serverwechsel-Token: 2 pro Monat" — ein Kontingent, kein Token. Der
      Schluessel klingt nach Geheimnis, der Wert ist eine Ziffer.

    Die Fehlermeldung an beiden Aufrufstellen stimmt damit wieder: was diese
    Funktion abweist, **sind** Zugangsdaten.
    """
    if _PRIVATE_KEY_RE.search(text) or _AUTHORIZATION_BEARER_RE.search(text):
        return True
    if _BEARER_RE.search(text) or _KNOWN_TOKEN_RE.search(text):
        return True
    return any(_wert_ist_geheimnis(m) for m in _SECRET_ASSIGNMENT_RE.finditer(text))


def _ersetze_zuweisung(match: re.Match[str]) -> str:
    """Schreibt Schlüssel und Trennzeichen zurück, ersetzt nur den Wert.

    Vorher wurde jede Zuweisung auf ``schluessel=[REDACTED]`` normalisiert. Das
    machte aus ``{"password": "geheim"}`` ein ``{password=[REDACTED]}`` — der
    Wert war weg, die Datei aber auch kaputt. Da der Text als Kontext an ein
    Modell geht, ist die erhaltene Form die brauchbarere.
    """
    quote = match.group("quote") or ""
    return f"{match.group('key')}{match.group('sep')}{quote}[REDACTED]{quote}"


def _ersetze_xml_element(match: re.Match[str]) -> str:
    """``<password>geheim</password>`` → ``<password>[REDACTED]</password>``.

    Wie bei der Zuweisung bleibt die Form erhalten und nur der Wert geht: der
    Text geht als Kontext an ein Modell, und eine zerschossene XML-Datei ist
    dort weniger wert als eine mit geschwaerztem Feld.
    """
    return f"{match.group('open')}[REDACTED]{match.group('close')}"


def _ersetze_xml_attribut(match: re.Match[str]) -> str:
    """``<ServerPassword value="geheim"/>`` → ``… value="[REDACTED]"/>``."""
    quote = match.group("quote")
    return f"{match.group('vorn')}{quote}[REDACTED]{quote}"


def redact_and_count(value: str) -> tuple[str, int]:
    """Wie `redact_sensitive_text`, aber sagt auch, wie oft es zugeschlagen hat.

    Die Zahl braucht, wer den Benutzer unterrichten will statt ihn abzuweisen.
    Bei Anhaengen ist genau das der Unterschied: ein Serverlog enthaelt fast
    immer irgendwo ein Tokenmuster, und "abgelehnt" hilft niemandem weiter —
    "drei Stellen unkenntlich gemacht" schon.

    Gezaehlt werden Ersetzungen, nicht Geheimnisse. Ein und dasselbe Passwort,
    das zehnmal im Log steht, zaehlt zehnmal; das ist fuer den Zweck — "hier
    wurde etwas veraendert, sieh es dir an" — die brauchbarere Zahl.
    """
    text, a = _PRIVATE_KEY_RE.subn("[REDACTED_PRIVATE_KEY]", value)
    text, b = _AUTHORIZATION_BEARER_RE.subn("Authorization=[REDACTED]", text)
    text, c = _SECRET_ASSIGNMENT_RE.subn(_ersetze_zuweisung, text)
    # Die XML-Formen laufen **nach** der Zuweisung: `<a key="k" value="v"/>`
    # traefe sonst schon dort zu, und das Ergebnis waere zweimal geschwaerzt.
    text, g = _SECRET_XML_ELEMENT_RE.subn(_ersetze_xml_element, text)
    text, h = _SECRET_XML_ATTRIBUT_RE.subn(_ersetze_xml_attribut, text)
    text, d = _BEARER_RE.subn("Bearer [REDACTED]", text)
    text, e = _KNOWN_TOKEN_RE.subn("[REDACTED_TOKEN]", text)
    return text, a + b + c + d + e + g + h


def redact_sensitive_text(value: str) -> str:
    """Entfernt typische Credentials (Passwörter, Tokens, Keys) vor Persistenz und Providertransfer."""
    return redact_and_count(value)[0]


def maskiere_email(adresse: str) -> str:
    """``maik@example.com`` wird zu ``m***@example.com``.

    Der eine Fall, in dem eine Adresse ueberhaupt in die Naehe des Modells
    kommt: der Benutzer laesst mit `send_test_email` seinen Mailweg pruefen und
    will wissen, **wohin** die Testmail ging. Hat er mehrere Konten, ist das die
    ganze Auskunft; ohne sie liest er "ist raus" und weiss nicht, in welchem
    Postfach er nachsehen soll.

    Der erste Buchstabe und die Domain reichen dafuer, und beides zusammen ist
    keine Adresse mehr: es fehlt genau der Teil, den man zum Schreiben braucht.
    `_EMAIL_RE` laesst das Ergebnis stehen — `*` gehoert nicht zum erlaubten
    Zeichenvorrat eines lokalen Teils, das Muster greift also nicht mehr.

    Das ist **kein** Schlupfloch in der globalen Regel darueber, sondern ihre
    Ausnahme mit Ansage: geschwaerzt wird, was eine Person **bezeichnet**. Ein
    Anfangsbuchstabe tut das nicht.

    Was nicht wie eine Adresse aussieht, wird ganz unkenntlich gemacht — lieber
    zu viel als ein halb stehengebliebener Wert, dessen Herkunft niemand kennt.
    """
    if not isinstance(adresse, str) or adresse.count("@") != 1:
        return "***"
    lokal, _, domain = adresse.partition("@")
    if not lokal or not domain:
        return "***"
    return f"{lokal[0]}***@{domain}"


def _ersetze_ip(match: re.Match[str]) -> str:
    """Schwaerzt nur oeffentlich routbare Adressen.

    Die Entscheidung faellt ueber `ipaddress` und nicht ueber Bereichsmuster im
    regulaeren Ausdruck: die Bereiche sind in der Standardbibliothek bereits
    richtig hinterlegt, und `172.16.0.0/12` von Hand als Muster zu schreiben ist
    genau die Art Kleinarbeit, bei der man sich vertut.

    Was sich nicht als Adresse lesen lässt, war keine — `999.1.2.3` fällt hier
    durch und bleibt stehen.

    Eine vierteilige Versionsnummer fällt **nicht** durch: `1.20.4.1` ist eine
    gültige, öffentlich routbare Adresse, und `ipaddress` kann den Unterschied
    nicht kennen. Hier stand das Gegenteil, und es war schlicht falsch. Der Preis
    ist zu benennen, seit `read_config` und `search_server_files` als Freitext
    gelten: in einer Konfigurationsdatei kann eine solche Versionsangabe zu
    `[REDACTED_IP]` werden. Das ist die richtige Richtung — wer im Zweifel
    schwärzt, verliert eine Zeile Diagnose; wer im Zweifel stehenlässt, verliert
    die Adresse eines Spielers an einen externen Anbieter.
    """
    roh = match.group(1)
    try:
        adresse = ipaddress.ip_address(roh)
    except ValueError:
        return roh
    if (
        adresse.is_private
        or adresse.is_loopback
        or adresse.is_link_local
        or adresse.is_unspecified
        or adresse.is_multicast
        or adresse.is_reserved
    ):
        return roh
    return "[REDACTED_IP]"


def redact_freetext(value: str) -> str:
    """Wie `redact_sensitive_text`, zusätzlich ohne fremde E-Mail- und IP-Adressen.

    Gedacht für Text, der **von aussen** in den Server hineingekommen ist:
    Logzeilen, Inhalte von Konfigurations- und Weltdateien, Beschreibungen von
    Guardian-Vorfaellen. Dort steht die Adresse oder E-Mail eines Spielers, und
    die ist ein personenbezogenes Datum, das kein Modellanbieter zu sehen braucht.

    Ausdrücklich **nicht** für strukturierte Serverangaben oder Benutzer-E-Mail-Tools.
    """
    text = redact_sensitive_text(value)
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _IPV4_RE.sub(_ersetze_ip, text)
    return _IPV6_RE.sub(_ersetze_ip, text)
