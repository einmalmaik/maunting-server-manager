/**
 * HTTP-Client der Desktop-App.
 *
 * - Basis-URL kommt aus der App-Konfiguration (Rust: konfig_laden).
 * - Das Access-Token lebt ausschliesslich in diesem Modul im Speicher; das
 *   Refresh-Token liegt im OS-Tresor (Rust: refresh_token_*). Kein Token
 *   erreicht je localStorage, Logs oder Fehlermeldungen.
 * - Antwortet das Backend mit 401, wird genau einmal über den Tresor
 *   rotiert und die Anfrage wiederholt — dieselbe Familienrotation wie im
 *   Browser, nur mit Body statt Cookie (backend/routers/auth.py).
 */
import { invoke } from "@tauri-apps/api/core";

let backendUrl: string | null = null;
let accessToken: string | null = null;

export class ApiFehler extends Error {
  constructor(
    public status: number,
    detail: string,
  ) {
    super(detail);
    this.name = "ApiFehler";
  }
}

export function setzeBackendUrl(url: string): void {
  backendUrl = url.replace(/\/+$/, "");
}

export function gibBackendUrl(): string | null {
  return backendUrl;
}

export function setzeAccessToken(token: string | null): void {
  accessToken = token;
}

export function istAngemeldet(): boolean {
  return accessToken !== null;
}

/** Beim Logout: alles vergessen — Speicher und Tresor. */
export async function sitzungVerwerfen(): Promise<void> {
  accessToken = null;
  await invoke("refresh_token_loeschen");
}

interface AnfrageOptionen {
  method?: string;
  body?: unknown;
  /** Ohne Authorization-Header senden (Login, Captcha-Config). */
  ohneAuth?: boolean;
}

async function roheAnfrage(pfad: string, optionen: AnfrageOptionen): Promise<Response> {
  if (!backendUrl) {
    throw new ApiFehler(0, "Keine Backend-URL konfiguriert");
  }
  const headers: Record<string, string> = {};
  if (optionen.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (!optionen.ohneAuth && accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }
  return await fetch(`${backendUrl}${pfad}`, {
    method: optionen.method ?? (optionen.body !== undefined ? "POST" : "GET"),
    headers,
    body: optionen.body !== undefined ? JSON.stringify(optionen.body) : undefined,
  });
}

/**
 * Holt über das Tresor-Refresh-Token neue Tokens. Liefert false, wenn kein
 * Token hinterlegt ist oder das Backend die Rotation ablehnt — der Aufrufer
 * schickt den Benutzer dann zur Anmeldung.
 */
export async function stillAnmelden(): Promise<boolean> {
  const refresh = await invoke<string | null>("refresh_token_laden");
  if (!refresh || !backendUrl) {
    return false;
  }
  const antwort = await fetch(`${backendUrl}/api/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  if (!antwort.ok) {
    // Abgelehnte Rotation heisst: das Token ist verbrannt (Widerruf oder
    // Wiederverwendungserkennung). Aufheben waere sinnlos und riskant.
    await invoke("refresh_token_loeschen");
    return false;
  }
  const tokens = (await antwort.json()) as { access_token: string; refresh_token: string };
  accessToken = tokens.access_token;
  await invoke("refresh_token_speichern", { token: tokens.refresh_token });
  return true;
}

async function fehlerAus(antwort: Response): Promise<ApiFehler> {
  let detail = `HTTP ${antwort.status}`;
  try {
    const daten = (await antwort.json()) as { detail?: unknown };
    if (typeof daten.detail === "string") {
      detail = daten.detail;
    }
  } catch {
    // Kein JSON — der generische Text reicht.
  }
  return new ApiFehler(antwort.status, detail);
}

/**
 * Zentrale JSON-Anfrage mit einmaligem Refresh-Versuch bei 401.
 *
 * Eine Antwort ohne Körper (204) kommt als `null` zurück und nicht als
 * Ausnahme: „nichts zu tun" ist beim Abholen von Aufträgen der Normalfall,
 * und ein `SyntaxError` aus dem JSON-Leser wäre dafür die falsche Sprache.
 */
export async function api<T>(pfad: string, optionen: AnfrageOptionen = {}): Promise<T> {
  let antwort = await roheAnfrage(pfad, optionen);
  if (antwort.status === 401 && !optionen.ohneAuth) {
    if (await stillAnmelden()) {
      antwort = await roheAnfrage(pfad, optionen);
    }
  }
  if (!antwort.ok) {
    throw await fehlerAus(antwort);
  }
  if (antwort.status === 204 || antwort.status === 205) {
    return null as T;
  }
  const text = await antwort.text();
  return (text ? JSON.parse(text) : null) as T;
}

/**
 * SSE über fetch — EventSource kann kein POST und keinen Authorization-
 * Header, deshalb wird der Strom von Hand gelesen (dasselbe Muster wie
 * `apiStream` im Panel-Frontend). Ruft `onEreignis` für jedes vollständige
 * `event:`/`data:`-Paar; kaputte JSON-Zeilen werden übersprungen statt den
 * Strom zu reissen.
 */
export async function apiStrom(
  pfad: string,
  optionen: AnfrageOptionen,
  onEreignis: (ereignis: string, daten: unknown) => void,
): Promise<void> {
  let antwort = await roheAnfrage(pfad, optionen);
  if (antwort.status === 401) {
    if (await stillAnmelden()) {
      antwort = await roheAnfrage(pfad, optionen);
    }
  }
  if (!antwort.ok || !antwort.body) {
    throw await fehlerAus(antwort);
  }
  const leser = antwort.body.getReader();
  const decoder = new TextDecoder();
  let puffer = "";
  for (;;) {
    const { done, value } = await leser.read();
    if (done) {
      break;
    }
    puffer += decoder.decode(value, { stream: true });
    let schnitt: number;
    while ((schnitt = puffer.indexOf("\n\n")) !== -1) {
      const block = puffer.slice(0, schnitt);
      puffer = puffer.slice(schnitt + 2);
      let ereignis = "message";
      let daten = "";
      for (const zeile of block.split("\n")) {
        if (zeile.startsWith("event:")) {
          ereignis = zeile.slice(6).trim();
        } else if (zeile.startsWith("data:")) {
          daten += zeile.slice(5).trim();
        }
      }
      if (daten) {
        try {
          onEreignis(ereignis, JSON.parse(daten));
        } catch {
          // Halbes JSON in einem kaputten Block — überspringen.
        }
      }
    }
  }
}
