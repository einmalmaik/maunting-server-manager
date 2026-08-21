import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect, beforeEach } from "vitest";

// ── Tauri-Mocks ───────────────────────────────────────────────────────────
// invoke wird gemockt: die Tests prüfen, dass die UI die richtigen Commands
// mit den richtigen Argumenten ruft — nicht Tauri selbst.

let konfigAntwort: Record<string, unknown> = { backend_url: null, sandbox_pfad: null, eingerichtet: false };
let tresorToken: string | null = null;

const invokeMock = vi.fn(async (...args: unknown[]) => {
  switch (args[0]) {
    case "konfig_laden":
      return konfigAntwort;
    case "refresh_token_laden":
      return tresorToken;
    case "wakeword_stand":
      return { aufnahmen: 0, trainiert: false, lauscht: false };
    default:
      return undefined;
  }
});
vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}));
vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(async () => () => {}),
}));
vi.mock("@tauri-apps/plugin-dialog", () => ({
  open: vi.fn(async () => "C:\\Users\\test\\Sandbox"),
}));

// ── Backend-Mock (fetch) ──────────────────────────────────────────────────

const TOKENS = {
  access_token: "acc-1",
  refresh_token: "ref-1",
  token_type: "bearer",
  requires_2fa: false,
  requires_verification: false,
  email: "",
  expires_in: 900,
};
const BENUTZER = {
  id: 1,
  username: "user1",
  email: "user1@test.de",
  agent_name: "Jarvis",
  time_zone: "Europe/Berlin",
};

const fetchMock = vi.fn(async (url: string | URL | Request, _init?: RequestInit) => {
  const pfad = String(url);
  const json = (daten: unknown, status = 200) =>
    new Response(JSON.stringify(daten), { status, headers: { "Content-Type": "application/json" } });
  if (pfad.endsWith("/api/auth/captcha-config")) {
    return json({ enabled: false, provider: "none", site_key: "" });
  }
  if (pfad.endsWith("/api/auth/login")) {
    return json(TOKENS);
  }
  if (pfad.endsWith("/api/auth/refresh")) {
    return tresorToken ? json(TOKENS) : json({ detail: "Kein Refresh-Token" }, 401);
  }
  if (pfad.endsWith("/api/auth/me")) {
    return json(BENUTZER);
  }
  if (pfad.endsWith("/api/auth/me/agent-name")) {
    return json({ agent_name: "Jarvis" });
  }
  if (pfad.endsWith("/api/auth/me/timezone")) {
    return json({ time_zone: "Europe/Berlin" });
  }
  return json({ detail: `Unerwarteter Aufruf: ${pfad}` }, 500);
});
vi.stubGlobal("fetch", fetchMock);

import App from "./App";
import Overlay from "./Overlay";
import { setzeAccessToken } from "./api/client";

beforeEach(() => {
  invokeMock.mockClear();
  fetchMock.mockClear();
  setzeAccessToken(null);
  konfigAntwort = { backend_url: null, sandbox_pfad: null, eingerichtet: false };
  tresorToken = null;
});

describe("App-Start", () => {
  it("zeigt den Einrichtungs-Assistenten bei frischer Installation", async () => {
    render(<App />);
    expect(await screen.findByText(/Backend verbinden/)).toBeInTheDocument();
  });

  it("springt zur Anmeldung, wenn die Sitzung abgelaufen ist", async () => {
    konfigAntwort = { backend_url: "https://panel.test", sandbox_pfad: null, eingerichtet: true };
    tresorToken = null;
    render(<App />);
    expect(await screen.findByRole("button", { name: "Anmelden" })).toBeInTheDocument();
  });

  it("meldet still ueber den Tresor an und zeigt die Hauptansicht", async () => {
    konfigAntwort = { backend_url: "https://panel.test", sandbox_pfad: null, eingerichtet: true };
    tresorToken = "ref-alt";
    render(<App />);
    expect(await screen.findByText("Jarvis")).toBeInTheDocument();
    expect(screen.getByText(/Angemeldet als user1/)).toBeInTheDocument();
    // Das rotierte Refresh-Token wandert zurueck in den Tresor.
    expect(invokeMock).toHaveBeenCalledWith("refresh_token_speichern", { token: "ref-1" });
  });
});

describe("Einrichtungs-Assistent", () => {
  it("fuehrt von der Backend-URL ueber den Login zur Personalisierung", async () => {
    render(<App />);
    const nutzer = userEvent.setup();

    // Schritt 1: Backend
    await nutzer.type(await screen.findByLabelText("Panel-Adresse"), "https://panel.test");
    await nutzer.click(screen.getByRole("button", { name: "Verbinden" }));

    // Schritt 2: Anmeldung — native_client muss im Login-Body stehen
    await nutzer.type(await screen.findByLabelText("Benutzername"), "user1");
    await nutzer.type(screen.getByLabelText("Passwort"), "geheim123");
    await nutzer.click(screen.getByRole("button", { name: "Anmelden" }));

    expect(await screen.findByText("Name des Assistenten")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Jarvis")).toBeInTheDocument();
    const loginAufruf = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/auth/login"));
    expect(loginAufruf).toBeDefined();
    const body = JSON.parse(String(loginAufruf![1]?.body));
    expect(body.native_client).toBe(true);
  });
});

describe("Hauptansicht", () => {
  beforeEach(() => {
    konfigAntwort = { backend_url: "https://panel.test", sandbox_pfad: null, eingerichtet: true };
    tresorToken = "ref-alt";
  });

  it("setzt den Tray-Status ueber das Rust-Command", async () => {
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Denkt" }));
    expect(invokeMock).toHaveBeenCalledWith("setze_status", { status: "denkt" });
  });

  it("schaltet das Overlay ueber das Rust-Command um", async () => {
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Overlay einblenden" }));
    expect(invokeMock).toHaveBeenCalledWith("overlay_sichtbar", { sichtbar: true });
  });

  it("zeigt die Wake-Word-Einrichtung mit dem Agenten-Namen als Vorschlag", async () => {
    render(<App />);
    const wortfeld = await screen.findByPlaceholderText("Agenten-Name, z. B. Singra");
    expect(wortfeld).toHaveValue("Jarvis");
  });
});

describe("Overlay (Sprachblase)", () => {
  it("rendert die pulsierende Blase", () => {
    render(<Overlay />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});
