/**
 * Der Einrichtungs-Assistent: Backend → Anmeldung → Personalisierung →
 * Sandbox → Wake-Word → fertig.
 *
 * Jeder Schritt ist eine kleine lokale Komponente mit einem einzigen
 * Auftrag; der Wizard hält nur den Schrittzeiger und reicht Ergebnisse
 * weiter. Persistiert wird sofort nach jedem Schritt (konfig_speichern) —
 * ein Abbruch mittendrin verliert nichts außer dem Rest des Weges.
 */
import { useState } from "react";
import { open as ordnerDialog } from "@tauri-apps/plugin-dialog";

import {
  agentNamenSetzen,
  anmelden,
  anmeldungVerifizieren,
  captchaConfig,
  ich,
  zeitzoneSetzen,
  type Benutzer,
} from "../api/auth";
import { setzeBackendUrl } from "../api/client";
import { konfigSpeichern, type AppKonfig } from "../lib/tauri";
import { Eingabe, Fehlertext, Karte, Knopf } from "../ui";
import WakewordEinrichtung from "../WakewordEinrichtung";

export type Schritt = "backend" | "anmeldung" | "personalisierung" | "sandbox" | "wakeword";

const SCHRITT_TITEL: Record<Schritt, string> = {
  backend: "Backend verbinden",
  anmeldung: "Anmelden",
  personalisierung: "Personalisieren",
  sandbox: "Sandbox festlegen",
  wakeword: "Wake-Word einrichten",
};

const REIHENFOLGE: Schritt[] = ["backend", "anmeldung", "personalisierung", "sandbox", "wakeword"];

interface WizardProps {
  konfig: AppKonfig;
  /** "anmeldung": Sitzung abgelaufen — nur neu anmelden, Rest ist eingerichtet. */
  startSchritt?: Schritt;
  onFertig: (konfig: AppKonfig, benutzer: Benutzer) => void;
}

export default function Wizard({ konfig, startSchritt = "backend", onFertig }: WizardProps) {
  const [schritt, setSchritt] = useState<Schritt>(startSchritt);
  const [stand, setStand] = useState<AppKonfig>(konfig);
  const [benutzer, setBenutzer] = useState<Benutzer | null>(null);
  // Nur-Anmeldung-Modus: nach dem Login direkt fertig, nichts neu einrichten.
  const nurAnmeldung = startSchritt === "anmeldung" && konfig.eingerichtet;

  async function weiter(neuerStand?: AppKonfig, neuerBenutzer?: Benutzer) {
    const k = neuerStand ?? stand;
    const b = neuerBenutzer ?? benutzer;
    if (neuerStand) setStand(neuerStand);
    if (neuerBenutzer) setBenutzer(neuerBenutzer);

    if (nurAnmeldung && b) {
      onFertig(k, b);
      return;
    }
    const index = REIHENFOLGE.indexOf(schritt);
    const naechster = REIHENFOLGE[index + 1];
    if (naechster) {
      setSchritt(naechster);
      return;
    }
    const fertig = { ...k, eingerichtet: true };
    await konfigSpeichern(fertig);
    if (b) onFertig(fertig, b);
  }

  return (
    <main className="flex h-full items-center justify-center bg-background p-6 text-foreground">
      <div className="flex w-full max-w-lg flex-col gap-4">
        <header className="flex flex-col items-center gap-1">
          <h1 className="text-2xl font-semibold tracking-tight">Maunting Smart System</h1>
          <p className="text-xs text-muted-foreground">
            Schritt {REIHENFOLGE.indexOf(schritt) + 1} von {REIHENFOLGE.length}:{" "}
            {SCHRITT_TITEL[schritt]}
          </p>
        </header>
        <Karte>
          {schritt === "backend" && <SchrittBackend stand={stand} onWeiter={weiter} />}
          {schritt === "anmeldung" && <SchrittAnmeldung onWeiter={weiter} />}
          {schritt === "personalisierung" && benutzer && (
            <SchrittPersonalisierung benutzer={benutzer} onWeiter={weiter} />
          )}
          {schritt === "sandbox" && <SchrittSandbox stand={stand} onWeiter={weiter} />}
          {schritt === "wakeword" && (
            <div className="flex flex-col gap-4">
              <WakewordEinrichtung wortVorschlag={benutzer?.agent_name ?? "Singra"} />
              <div className="flex justify-end gap-2">
                <Knopf stimme="leise" onClick={() => void weiter()}>
                  Später einrichten
                </Knopf>
                <Knopf onClick={() => void weiter()}>Fertigstellen</Knopf>
              </div>
            </div>
          )}
        </Karte>
      </div>
    </main>
  );
}

// ── Schritt 1: Backend-URL ────────────────────────────────────────────────

function SchrittBackend({
  stand,
  onWeiter,
}: {
  stand: AppKonfig;
  onWeiter: (stand: AppKonfig) => Promise<void>;
}) {
  const [url, setUrl] = useState(stand.backend_url ?? "");
  const [fehler, setFehler] = useState<string | null>(null);
  const [prueft, setPrueft] = useState(false);

  async function verbinden() {
    setFehler(null);
    setPrueft(true);
    try {
      const bereinigt = url.trim().replace(/\/+$/, "");
      if (!/^https?:\/\/.+/.test(bereinigt)) {
        throw new Error("Die Adresse muss mit https:// oder http:// beginnen");
      }
      setzeBackendUrl(bereinigt);
      // Öffentlicher Endpunkt als Erreichbarkeits-Test — schlägt er fehl,
      // ist die URL falsch oder das Panel nicht erreichbar.
      await captchaConfig();
      const neu = { ...stand, backend_url: bereinigt };
      await konfigSpeichern(neu);
      await onWeiter(neu);
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
    } finally {
      setPrueft(false);
    }
  }

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        void verbinden();
      }}
    >
      <p className="text-sm text-muted-foreground">
        Gesucht ist die Adresse, unter der die <strong>Panel-API</strong> antwortet. Bei
        einer Standardinstallation ist das dieselbe Adresse, die du im Browser öffnest.
        Liegt die Oberfläche getrennt vom Backend, ist es die Adresse des Backends —
        lokal meist <code>http://localhost:8000</code>.
      </p>
      <Eingabe
        label="Panel-Adresse"
        placeholder="https://panel.example.com"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        hinweis="Nicht sicher? Panel im Browser öffnen, F12 → Netzwerk, anmelden — der Host der Anfrage auf /api/auth/login ist die richtige Adresse."
        autoFocus
      />
      <Fehlertext text={fehler} />
      <div className="flex justify-end">
        <Knopf type="submit" disabled={prueft || url.trim() === ""}>
          {prueft ? "Prüfe …" : "Verbinden"}
        </Knopf>
      </div>
    </form>
  );
}

// ── Schritt 2: Anmeldung (Passwort, 2FA, E-Mail-Verifikation) ────────────

function SchrittAnmeldung({
  onWeiter,
}: {
  onWeiter: (stand?: undefined, benutzer?: Benutzer) => Promise<void>;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [mailCode, setMailCode] = useState("");
  const [zweig, setZweig] = useState<"passwort" | "2fa" | "verifikation">("passwort");
  const [fehler, setFehler] = useState<string | null>(null);
  const [laeuft, setLaeuft] = useState(false);

  async function absenden() {
    setFehler(null);
    setLaeuft(true);
    try {
      const antwort =
        zweig === "verifikation"
          ? await anmeldungVerifizieren(username, password, mailCode, otp || undefined)
          : await anmelden(username, password, otp || undefined);
      if (antwort.requires_verification) {
        setZweig("verifikation");
        return;
      }
      if (antwort.requires_2fa) {
        setZweig("2fa");
        return;
      }
      await onWeiter(undefined, await ich());
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
    } finally {
      setLaeuft(false);
    }
  }

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        void absenden();
      }}
    >
      <p className="text-sm text-muted-foreground">
        Melde dich mit deinem Panel-Konto an. Ohne Konto auf dem gewählten Backend gibt es
        keinen Zugriff.
      </p>
      <Eingabe
        label="Benutzername"
        value={username}
        onChange={(e) => setUsername(e.target.value)}
        autoComplete="username"
        autoFocus
        disabled={zweig !== "passwort"}
      />
      <Eingabe
        label="Passwort"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        autoComplete="current-password"
        disabled={zweig !== "passwort"}
      />
      {zweig === "2fa" && (
        <Eingabe
          label="2FA-Code oder Backup-Code"
          value={otp}
          onChange={(e) => setOtp(e.target.value)}
          placeholder="123456 oder XXXX-XXXX"
          hinweis="Sechsstelliger Code aus deiner Authenticator-App — oder ein Backup-Code."
          autoFocus
        />
      )}
      {zweig === "verifikation" && (
        <Eingabe
          label="Verifikationscode aus der E-Mail"
          value={mailCode}
          onChange={(e) => setMailCode(e.target.value)}
          placeholder="123456"
          hinweis="Deine E-Mail-Adresse ist noch nicht bestätigt — der Code wurde dir zugesandt."
          autoFocus
        />
      )}
      <Fehlertext text={fehler} />
      <div className="flex justify-end">
        <Knopf type="submit" disabled={laeuft || username === "" || password === ""}>
          {laeuft ? "Melde an …" : "Anmelden"}
        </Knopf>
      </div>
    </form>
  );
}

// ── Schritt 3: Personalisierung (Agent-Name + Zeitzone) ──────────────────

function SchrittPersonalisierung({
  benutzer,
  onWeiter,
}: {
  benutzer: Benutzer;
  onWeiter: (stand?: undefined, benutzer?: Benutzer) => Promise<void>;
}) {
  const [name, setName] = useState(benutzer.agent_name ?? "Singra");
  const [zeitzone, setZeitzone] = useState(
    benutzer.time_zone ?? Intl.DateTimeFormat().resolvedOptions().timeZone,
  );
  const [fehler, setFehler] = useState<string | null>(null);
  const [laeuft, setLaeuft] = useState(false);

  async function speichern() {
    setFehler(null);
    setLaeuft(true);
    try {
      await agentNamenSetzen(name.trim() || null);
      await zeitzoneSetzen(zeitzone || null);
      await onWeiter(undefined, {
        ...benutzer,
        agent_name: name.trim() || null,
        time_zone: zeitzone || null,
      });
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
    } finally {
      setLaeuft(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        Gib deinem Assistenten einen Namen — so sprichst du ihn an, und so meldet er sich.
        Beides gilt überall: hier und im Panel.
      </p>
      <Eingabe
        label="Name des Assistenten"
        value={name}
        onChange={(e) => setName(e.target.value)}
        hinweis="2–32 Zeichen; leer lassen für den Standardnamen Singra."
      />
      <label className="flex flex-col gap-1.5">
        <span className="text-xs font-medium text-muted-foreground">Zeitzone</span>
        <select
          value={zeitzone}
          onChange={(e) => setZeitzone(e.target.value)}
          className="rounded-[var(--radius-control)] border border-input bg-secondary px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {Intl.supportedValuesOf("timeZone").map((tz) => (
            <option key={tz} value={tz}>
              {tz}
            </option>
          ))}
        </select>
      </label>
      <Fehlertext text={fehler} />
      <div className="flex justify-end">
        <Knopf onClick={() => void speichern()} disabled={laeuft}>
          {laeuft ? "Speichert …" : "Weiter"}
        </Knopf>
      </div>
    </div>
  );
}

// ── Schritt 4: Sandbox-Ordner ─────────────────────────────────────────────

function SchrittSandbox({
  stand,
  onWeiter,
}: {
  stand: AppKonfig;
  onWeiter: (stand: AppKonfig) => Promise<void>;
}) {
  const [pfad, setPfad] = useState(stand.sandbox_pfad ?? "");
  const [fehler, setFehler] = useState<string | null>(null);

  async function waehlen() {
    const auswahl = await ordnerDialog({ directory: true, multiple: false });
    if (typeof auswahl === "string") {
      setPfad(auswahl);
    }
  }

  async function speichern() {
    setFehler(null);
    try {
      const neu = { ...stand, sandbox_pfad: pfad || null };
      await konfigSpeichern(neu);
      await onWeiter(neu);
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        Die Sandbox ist der einzige Ordner, in dem die KI später Dateien anlegen und
        bearbeiten darf — zum Beispiel für Coding-Aufgaben. Systemverzeichnisse sind
        grundsätzlich tabu.
      </p>
      <div className="flex items-end gap-2">
        <div className="flex-1">
          <Eingabe
            label="Sandbox-Ordner"
            value={pfad}
            onChange={(e) => setPfad(e.target.value)}
            placeholder="C:\\Users\\du\\MSS-Sandbox"
          />
        </div>
        <Knopf stimme="leise" onClick={() => void waehlen()}>
          Ordner wählen
        </Knopf>
      </div>
      <Fehlertext text={fehler} />
      <div className="flex justify-end gap-2">
        <Knopf stimme="leise" onClick={() => void onWeiter(stand)}>
          Später festlegen
        </Knopf>
        <Knopf onClick={() => void speichern()} disabled={pfad === ""}>
          Weiter
        </Knopf>
      </div>
    </div>
  );
}
