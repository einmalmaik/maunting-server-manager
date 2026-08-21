/**
 * Auth-Fluesse gegen dieselben Endpunkte wie das Web-Panel — nur mit
 * `native_client: true`, damit die Tokens im Body kommen statt als Cookies
 * (Phase-1-Backend: backend/routers/auth.py).
 */
import { invoke } from "@tauri-apps/api/core";

import { api, setzeAccessToken, sitzungVerwerfen } from "./client";

export interface TokenAntwort {
  access_token: string;
  refresh_token: string;
  requires_2fa: boolean;
  requires_verification: boolean;
  email: string;
  expires_in: number;
}

export interface Benutzer {
  id: number;
  username: string;
  email: string;
  agent_name: string | null;
  time_zone: string | null;
}

export interface CaptchaConfig {
  enabled: boolean;
  provider: string;
  site_key: string;
}

/** Öffentlich; dient dem Assistenten auch als Erreichbarkeits-Test. */
export async function captchaConfig(): Promise<CaptchaConfig> {
  return await api<CaptchaConfig>("/api/auth/captcha-config", { ohneAuth: true });
}

/** Übernimmt die Tokens einer erfolgreichen Anmeldung (Speicher + Tresor). */
async function tokensUebernehmen(antwort: TokenAntwort): Promise<void> {
  if (!antwort.access_token) {
    return; // 2FA- oder Verifikationszwischenschritt — noch keine Sitzung.
  }
  setzeAccessToken(antwort.access_token);
  await invoke("refresh_token_speichern", { token: antwort.refresh_token });
}

export async function anmelden(
  username: string,
  password: string,
  otpCode?: string,
): Promise<TokenAntwort> {
  const antwort = await api<TokenAntwort>("/api/auth/login", {
    body: {
      username,
      password,
      otp_code: otpCode || null,
      native_client: true,
    },
    ohneAuth: true,
  });
  await tokensUebernehmen(antwort);
  return antwort;
}

/** E-Mail-Verifikationszweig: 6-stelliger Code aus der Mail. */
export async function anmeldungVerifizieren(
  username: string,
  password: string,
  code: string,
  otpCode?: string,
): Promise<TokenAntwort> {
  const antwort = await api<TokenAntwort>("/api/auth/login-verify", {
    body: {
      username,
      password,
      code,
      otp_code: otpCode || null,
      native_client: true,
    },
    ohneAuth: true,
  });
  await tokensUebernehmen(antwort);
  return antwort;
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
