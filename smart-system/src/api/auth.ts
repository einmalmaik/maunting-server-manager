/**
 * Der Weg hinein — und es ist genau einer.
 *
 * Diese Datei kannte einmal Benutzername, Passwort, 2FA-Code und den
 * E-Mail-Verifikationszweig. Alles davon ist am 21.08.2026 verschwunden, aus
 * einem handfesten Grund: bei aktiviertem Captcha prüft `/api/auth/login` den
 * Turnstile-Token als allererste Anweisung, und ein Captcha-Widget in einem
 * Tauri-Fenster scheitert daran, dass Cloudflare-Schlüssel an Domains hängen —
 * `tauri.localhost` ist keine, die man dort hinterlegen kann. Jede Anmeldung
 * endete in „CAPTCHA-Verifizierung erforderlich".
 *
 * Stattdessen koppelt man: im Panel entsteht ein Code, hier wird er
 * eingelöst. Passwort, 2FA und Captcha bleiben, wo sie hingehören — im
 * Browser, hinter dem Vorhang, den diese App gar nicht erst anfasst. Und die
 * so entstandene Sitzung trägt im Token, dass sie von einem Gerät kommt;
 * daran hängt, ob die KI die Werkzeuge dieses Rechners angeboten bekommt.
 */
import { invoke } from "@tauri-apps/api/core";

import { api, setzeAccessToken, sitzungVerwerfen } from "./client";

export interface TokenAntwort {
  access_token: string;
  refresh_token: string;
  expires_in: number;
}

export interface Benutzer {
  id: number;
  username: string;
  email: string;
  agent_name: string | null;
  time_zone: string | null;
}

/**
 * Öffentlich und ohne Nebenwirkung — der Erreichbarkeits-Test beim Einrichten.
 *
 * Vorher diente `captcha-config` dazu. Das war naheliegend, solange die App
 * sich mit Passwort anmeldete; jetzt interessiert sie das Captcha nicht mehr,
 * und ein Endpunkt, dessen Antwort niemand liest, ist der falsche Beweis.
 */
export async function erreichbar(): Promise<void> {
  await api("/api/auth/setup-status", { ohneAuth: true });
}

/**
 * Löst einen Kopplungscode ein und übernimmt die Sitzung.
 *
 * Der Code wird so geschickt, wie der Mensch ihn eingegeben hat — das Panel
 * liest ihn nachsichtig (Kleinschreibung, fehlende Striche, Leerzeichen).
 * Streng ist erst der Vergleich dort.
 */
export async function koppeln(code: string, bezeichnung: string): Promise<void> {
  const antwort = await api<TokenAntwort>("/api/auth/devices/redeem", {
    body: { code, label: bezeichnung },
    ohneAuth: true,
  });
  setzeAccessToken(antwort.access_token);
  await invoke("refresh_token_speichern", { token: antwort.refresh_token });
}

export async function abmelden(): Promise<void> {
  const refresh = await invoke<string | null>("refresh_token_laden");
  try {
    // Widerruft jti und Refresh-Familie serverseitig; ein bereits
    // abgelaufenes Token macht das Abmelden lokal trotzdem sauber.
    await api("/api/auth/logout", { body: { refresh_token: refresh } });
  } finally {
    await sitzungVerwerfen();
  }
}

export async function ich(): Promise<Benutzer> {
  return await api<Benutzer>("/api/auth/me");
}

export async function agentNamenSetzen(name: string | null): Promise<void> {
  await api("/api/auth/me/agent-name", { method: "PATCH", body: { agent_name: name } });
}

export async function zeitzoneSetzen(zeitzone: string | null): Promise<void> {
  await api("/api/auth/me/timezone", { method: "PATCH", body: { time_zone: zeitzone } });
}
