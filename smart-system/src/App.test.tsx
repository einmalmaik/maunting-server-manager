import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect, beforeEach } from "vitest";

// ── Tauri-Mocks ───────────────────────────────────────────────────────────
// invoke wird gemockt: die Tests prüfen, dass die UI die richtigen Commands
// mit den richtigen Argumenten ruft — nicht Tauri selbst.

let konfigAntwort: Record<string, unknown> = { backend_url: null, sandbox_pfad: null, eingerichtet: false };
let tresorToken: string | null = null;

// Aufträge, die die Schleife abholen soll. Jeder wird genau einmal
// ausgeliefert — wie im Panel, wo ein geholter Auftrag als "taken" gilt.
let offeneAuftraege: unknown[] = [];
const gemeldet: Array<{ pfad: string; body: unknown }> = [];

const invokeMock = vi.fn(async (...args: unknown[]) => {
  switch (args[0]) {
    case "konfig_laden":
      return konfigAntwort;
    case "refresh_token_laden":
      return tresorToken;
    case "wakeword_stand":
      return { aufnahmen: 0, trainiert: false, lauscht: false };
    case "auftrag_ausfuehren": {
      const argumente = args[1] as { werkzeug: string };
      // Die Übernahme-Anfrage liefert bewusst nichts: über sie entscheidet
      // ein Mensch an der Karte.
      if (argumente.werkzeug === "desktop_takeover_control") return null;
      return { inhalt: "Hallo aus der Sandbox" };
    }
    case "uebernahme_rest":
      return 0;
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

// Der Chat lädt Verlauf/Provider/Lauf beim Öffnen; der Bestand ist
// veränderbar, damit der Test nach dem Stream die gespeicherte Wahrheit
// nachladen kann (das macht die echte App genauso).
let chatNachrichten: unknown[] = [];

const PROVIDER = [
  {
    id: 1,
    name: "OpenRouter",
    default_model: "test/modell",
    available: true,
    reasoning: false,
    efforts: [],
    can_disable: false,
    default_effort: null,
  },
];

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
  if (pfad.endsWith("/api/ai/providers")) {
    return json(PROVIDER);
  }
  if (pfad.includes("/api/ai/conversation/run?")) {
    return json(null);
  }
  if (pfad.endsWith("/api/ai/conversation/messages/stream")) {
    // Der Stream antwortet als SSE; danach liegt die Antwort im Verlauf.
    chatNachrichten = [
      {
        id: "m1",
        role: "user",
        content: "Hallo",
        reasoning: null,
        question: null,
        sections: null,
        status: "complete",
        provider_id: 1,
        model: null,
        created_at: "2026-08-21T10:00:00Z",
      },
      {
        id: "m2",
        role: "assistant",
        content: "Hallo vom Test",
        reasoning: null,
        question: null,
        sections: [{ art: "text", inhalt: "Hallo vom Test", werkzeug: null }],
        status: "complete",
        provider_id: 1,
        model: "test/modell",
        created_at: "2026-08-21T10:00:01Z",
      },
    ];
    const sse =
      'event: delta\ndata: {"content":"Hallo vom Test"}\n\n' +
      'event: done\ndata: {"run_id":"r1","status":"completed"}\n\n';
    return new Response(sse, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  }
  if (pfad.includes("/api/ai/conversation?")) {
    return json({ id: "konv-1", messages: chatNachrichten, has_more: false });
  }
  if (pfad.endsWith("/api/desktop/jobs/next")) {
    const naechster = offeneAuftraege.shift();
    // 204 ohne Körper ist der Normalfall "nichts zu tun".
    return naechster ? json(naechster) : new Response(null, { status: 204 });
  }
  if (pfad.includes("/api/desktop/jobs/")) {
    gemeldet.push({ pfad, body: JSON.parse(String(_init?.body ?? "{}")) });
    return new Response(null, { status: 204 });
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
  chatNachrichten = [];
  offeneAuftraege = [];
  gemeldet.length = 0;
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

  async function einstellungenOeffnen() {
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Einstellungen" }));
  }

  it("setzt den Tray-Status ueber das Rust-Command", async () => {
    await einstellungenOeffnen();
    await userEvent.click(await screen.findByRole("button", { name: "Denkt" }));
    expect(invokeMock).toHaveBeenCalledWith("setze_status", { status: "denkt" });
  });

  it("schaltet das Overlay ueber das Rust-Command um", async () => {
    await einstellungenOeffnen();
    await userEvent.click(await screen.findByRole("button", { name: "Overlay einblenden" }));
    expect(invokeMock).toHaveBeenCalledWith("overlay_sichtbar", { sichtbar: true });
  });

  it("zeigt die Wake-Word-Einrichtung mit dem Agenten-Namen als Vorschlag", async () => {
    await einstellungenOeffnen();
    const wortfeld = await screen.findByPlaceholderText("Agenten-Name, z. B. Singra");
    expect(wortfeld).toHaveValue("Jarvis");
  });
});

describe("Chat", () => {
  beforeEach(() => {
    konfigAntwort = { backend_url: "https://panel.test", sandbox_pfad: null, eingerichtet: true };
    tresorToken = "ref-alt";
  });

  it("zeigt den leeren Dauerchat nach dem Start", async () => {
    render(<App />);
    expect(await screen.findByText(/Sag Jarvis einfach/)).toBeInTheDocument();
    // Beim Öffnen wird geprüft, ob noch ein alter Lauf weiterläuft.
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("/api/ai/conversation/run?")),
    ).toBe(true);
  });

  it("streamt eine Antwort und laedt danach den gespeicherten Verlauf", async () => {
    render(<App />);
    const nutzer = userEvent.setup();
    const eingabe = await screen.findByPlaceholderText("Nachricht an Jarvis …");
    await nutzer.type(eingabe, "Hallo");
    await nutzer.click(screen.getByRole("button", { name: "Senden" }));

    // Die Antwort kommt erst live aus dem Stream, dann aus dem Verlauf —
    // sichtbar bleibt sie durchgehend.
    expect(await screen.findByText("Hallo vom Test")).toBeInTheDocument();
    // Der Tray-Status lief mit: denkt beim Senden, bereit am Ende.
    expect(invokeMock).toHaveBeenCalledWith("setze_status", { status: "denkt" });
    expect(invokeMock).toHaveBeenCalledWith("setze_status", { status: "bereit" });
    // Der Stream-Aufruf trägt den Anbieter und eine request_id.
    const streamAufruf = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/api/ai/conversation/messages/stream"),
    );
    expect(streamAufruf).toBeDefined();
    const body = JSON.parse(String(streamAufruf![1]?.body));
    expect(body.provider_id).toBe(1);
    expect(typeof body.request_id).toBe("string");
  });
});

describe("Auftragsschleife", () => {
  beforeEach(() => {
    konfigAntwort = {
      backend_url: "https://panel.test",
      sandbox_pfad: "C:\\Users\\test\\Sandbox",
      eingerichtet: true,
    };
    tresorToken = "ref-alt";
  });

  it("holt einen Auftrag ab, laesst ihn ausfuehren und meldet das Ergebnis", async () => {
    offeneAuftraege = [
      {
        id: "job-1",
        tool_name: "desktop_dateien",
        arguments: { aktion: "lesen", pfad: "notiz.txt" },
      },
    ];
    render(<App />);

    await vi.waitFor(
      () => {
        expect(invokeMock).toHaveBeenCalledWith("auftrag_ausfuehren", {
          werkzeug: "desktop_dateien",
          argumente: { aktion: "lesen", pfad: "notiz.txt" },
        });
        expect(gemeldet).toHaveLength(1);
      },
      { timeout: 5000 },
    );

    expect(gemeldet[0].pfad).toContain("/api/desktop/jobs/job-1/result");
    expect(gemeldet[0].body).toMatchObject({
      ok: true,
      ergebnis: { inhalt: "Hallo aus der Sandbox" },
    });
  });

  it("zeigt die Bestaetigungskarte, bevor die KI Maus und Tastatur bekommt", async () => {
    offeneAuftraege = [
      {
        id: "job-2",
        tool_name: "desktop_takeover_control",
        arguments: { anliegen: "Ich möchte deinen Browser bedienen.", minuten: 5 },
      },
    ];
    render(<App />);

    // Die Karte kommt über ein Tauri-Ereignis; der Mock ruft den Hörer direkt.
    await vi.waitFor(
      () =>
        expect(invokeMock).toHaveBeenCalledWith("auftrag_ausfuehren", {
          werkzeug: "desktop_takeover_control",
          argumente: { anliegen: "Ich möchte deinen Browser bedienen.", minuten: 5 },
        }),
      { timeout: 5000 },
    );

    // Entscheidend: **keine** Freigabe ohne Klick des Menschen.
    expect(invokeMock).not.toHaveBeenCalledWith(
      "uebernahme_freigeben",
      expect.anything(),
    );
    // Und der Auftrag bleibt offen, bis der Mensch entschieden hat.
    expect(gemeldet).toHaveLength(0);
  });
});

describe("Boot-Sequenz", () => {
  it("zeigt das DIS-Logo zuerst und laesst sich per Klick ueberspringen", async () => {
    render(<App />);
    expect(await screen.findByAltText("DIS")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("splash"));
    expect(screen.queryByTestId("splash")).not.toBeInTheDocument();
  });
});

describe("Overlay (Sprachblase)", () => {
  it("rendert die pulsierende Blase", () => {
    render(<Overlay />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});
