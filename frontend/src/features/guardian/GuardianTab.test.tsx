import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/i18n";
import { GuardianTab } from "./GuardianTab";
import { GuardianBadge, getGuardianDisplayState } from "./GuardianBadge";
import { GuardianQuarantineBanner } from "./GuardianQuarantineBanner";
import * as client from "@/api/client";
import { usePermissionsStore } from "@/stores/permissionsStore";
import type { Server, GuardianIncident, GuardianIncidentAi } from "@/types";

vi.mock("@/api/client", () => ({
  api: vi.fn(),
}));

const mockServerGuardianEnabled: Server = {
  id: 1,
  name: "Test Conan Server",
  game_type: "conan_exiles_ue5",
  status: "running",
  status_message: null,
  guardian_observed_state: "healthy",
  guardian_enabled: true,
  guardian_probe_timestamp: "2026-07-21T14:00:00Z",
  guardian_sync_error_statistics: null,
  auth_required: false,
  auto_restart: false,
  restart_interval_hours: null,
  restart_time_utc: null,
  restart_times_utc: null,
  last_auto_restart_attempt_at: null,
  last_auto_restart_completed_at: null,
  last_auto_restart_status: null,
  next_auto_restart_at: null,
  restart_ai_managed: false,
  started_at: null,
  uptime_seconds: null,
  cpu_limit_percent: null,
  ram_limit_mb: null,
  disk_limit_gb: null,
  disk_usage_mb: null,
  game_port: 7777,
  query_port: 27015,
  rcon_port: 25575,
  public_bind_ip: "127.0.0.1",
  created_at: "2026-07-21T12:00:00Z",
};

const mockServerGuardianDisabled: Server = {
  ...mockServerGuardianEnabled,
  guardian_enabled: false,
};

describe("Guardian UI Components", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    await i18n.changeLanguage("de");
  });

  describe("GuardianBadge", () => {
    it("renders nothing when guardian_enabled is false", () => {
      const { container } = render(
        <GuardianBadge server={mockServerGuardianDisabled} />
      );
      expect(container.firstChild).toBeNull();
    });

    it("renders active badge when guardian_enabled is true and state is healthy", () => {
      render(<GuardianBadge server={mockServerGuardianEnabled} />);
      expect(screen.getByText(/Autopilot Aktiv/i)).toBeInTheDocument();
    });

    it("renders quarantined badge when state is quarantined", () => {
      const server = {
        ...mockServerGuardianEnabled,
        guardian_observed_state: "quarantined",
      };
      render(<GuardianBadge server={server} />);
      expect(screen.getByText(/Autopilot Quarantäne/i)).toBeInTheDocument();
    });

    it("never presents missing or offline observed state as healthy", () => {
      const missing = { ...mockServerGuardianEnabled, guardian_observed_state: undefined };
      const { rerender } = render(<GuardianBadge server={missing} />);
      expect(screen.getByText(/Autopilot unbekannt/i)).toBeInTheDocument();
      expect(screen.queryByText(/Autopilot Aktiv/i)).toBeNull();

      rerender(<GuardianBadge server={{ ...missing, status: "offline", guardian_observed_state: "healthy" }} />);
      expect(screen.getByText(/Autopilot offline/i)).toBeInTheDocument();
      expect(screen.queryByText(/Autopilot Aktiv/i)).toBeNull();
    });

    it("lets a stopped server override a stale healthy Guardian observation", () => {
      const stopped = {
        ...mockServerGuardianEnabled,
        status: "stopped",
        guardian_observed_state: "healthy",
      };
      render(<GuardianBadge server={stopped} />);
      expect(getGuardianDisplayState(stopped)).toBe("stopped");
      expect(screen.getByText(/Autopilot gestoppt/i)).toBeInTheDocument();
      expect(screen.queryByText(/Autopilot Aktiv/i)).toBeNull();
    });

    it("classifies every supported observed state without treating it as healthy", () => {
      const expected = {
        healthy: "healthy",
        starting: "activity",
        recovering: "activity",
        verifying: "activity",
        degraded: "warning",
        unhealthy: "warning",
        quarantined: "quarantined",
        stopped: "stopped",
        unknown: "unknown",
        disabled: "unknown",
      } as const;

      for (const [guardian_observed_state, displayState] of Object.entries(expected)) {
        expect(
          getGuardianDisplayState({ ...mockServerGuardianEnabled, guardian_observed_state })
        ).toBe(displayState);
      }
    });
  });

  describe("GuardianQuarantineBanner", () => {
    it("renders banner when server is quarantined and calls resolve API on button click", async () => {
      const server = {
        ...mockServerGuardianEnabled,
        guardian_observed_state: "quarantined",
      };
      const incidents: GuardianIncident[] = [
        {
          id: 42,
          title: "Autopilot: process_not_running",
          description: "GameThread hang detected",
          type: "process_not_running",
          status: "quarantined",
          fingerprint: "fp123",
          created_at: "2026-07-21T12:00:00Z",
          resolved_at: null,
          attempts: [],
        },
      ];

      vi.mocked(client.api).mockResolvedValue({ ok: true });

      const onRefresh = vi.fn();
      render(
        <GuardianQuarantineBanner
          server={server}
          incidents={incidents}
          onRefresh={onRefresh}
        />
      );

      expect(screen.getByText(/Server in Quarantäne versetzt/i)).toBeInTheDocument();

      const button = screen.getByRole("button", { name: /Freigabe anfordern/i });
      fireEvent.click(button);

      await waitFor(() => {
        expect(client.api).toHaveBeenCalledWith(
          "/servers/1/incidents/42/resolve",
          { method: "POST" }
        );
        expect(onRefresh).toHaveBeenCalled();
      });
      expect(screen.getByRole("button", { name: /Freigabe ausstehend/i })).toBeDisabled();
      expect(screen.getByText(/bleibt als quarantiniert markiert/i)).toBeInTheDocument();
    });

    it("does not resolve an arbitrary open incident when no quarantine incident is associated", () => {
      const server = { ...mockServerGuardianEnabled, guardian_observed_state: "quarantined" };
      render(
        <GuardianQuarantineBanner
          server={server}
          incidents={[{
            id: 99,
            title: "Other incident",
            description: "Not quarantine",
            type: "process_not_running",
            status: "open",
            fingerprint: "other",
            created_at: "2026-07-21T12:00:00Z",
            resolved_at: null,
            attempts: [],
          }]}
        />,
      );
      const button = screen.getByRole("button", { name: /Freigabe anfordern/i });
      expect(button).toBeDisabled();
      fireEvent.click(button);
      expect(client.api).not.toHaveBeenCalled();
      expect(screen.getByText(/Keine eindeutig zugeordnete/i)).toBeInTheDocument();
    });

    it("restores authoritative pending state after remount with resolved incident data", () => {
      const server = {
        ...mockServerGuardianEnabled,
        guardian_observed_state: "quarantined",
        guardian_quarantine_clear_pending: true,
      };
      const resolvedIncident: GuardianIncident = {
        id: 42,
        title: "Autopilot: process_not_running",
        description: "Synthetic incident",
        type: "process_not_running",
        status: "resolved",
        fingerprint: "synthetic-fingerprint",
        created_at: "2026-07-21T12:00:00Z",
        resolved_at: "2026-07-21T12:05:00Z",
        attempts: [],
      };

      const { unmount } = render(
        <GuardianQuarantineBanner server={server} incidents={[resolvedIncident]} />,
      );
      expect(screen.getByRole("button", { name: /Freigabe ausstehend/i })).toBeDisabled();
      expect(screen.getByText(/noch nicht vom Agenten bestätigt|bleibt als quarantiniert markiert/i)).toBeInTheDocument();
      expect(screen.queryByText(/Keine eindeutig zugeordnete/i)).toBeNull();
      unmount();

      render(<GuardianQuarantineBanner server={server} incidents={[resolvedIncident]} />);
      expect(screen.getByRole("button", { name: /Freigabe ausstehend/i })).toBeDisabled();
      expect(client.api).not.toHaveBeenCalled();
    });
  });

  describe("GuardianTab", () => {
    it("fetches and renders incidents list", async () => {
      const incidents: GuardianIncident[] = [
        {
          id: 10,
          title: "Autopilot: process_not_running",
          description: "GameThread hang detected in Conan Sandbox",
          type: "process_not_running",
          status: "open",
          fingerprint: "fp_conan",
          created_at: "2026-07-21T12:00:00Z",
          resolved_at: null,
          attempts: [{ attempt: 1, action: "restart", result: "success" }],
        },
      ];

      vi.mocked(client.api).mockResolvedValue(incidents);

      render(
        <MemoryRouter>
          <GuardianTab server={mockServerGuardianEnabled} />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText("Autopilot: process_not_running")).toBeInTheDocument();
        expect(screen.getByText(/GameThread hang detected/i)).toBeInTheDocument();
      });
    });

    it("allows resolving an incident from the tab", async () => {
      const incidents: GuardianIncident[] = [
        {
          id: 10,
          title: "Autopilot: process_not_running",
          description: "Hang detected",
          type: "process_not_running",
          status: "open",
          fingerprint: "fp_conan",
          created_at: "2026-07-21T12:00:00Z",
          resolved_at: null,
          attempts: [],
        },
      ];

      vi.mocked(client.api).mockImplementation(async (path) => {
        if (path === "/servers/1/incidents") return incidents;
        if (path === "/servers/1/incidents/10/resolve") return { ok: true };
        return null;
      });

      render(
        <MemoryRouter>
          <GuardianTab server={mockServerGuardianEnabled} />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText("Autopilot: process_not_running")).toBeInTheDocument();
      });

      const resolveBtn = screen.getByRole("button", { name: /Incident als gelöst markieren/i });
      fireEvent.click(resolveBtn);

      await waitFor(() => {
        expect(client.api).toHaveBeenCalledWith("/servers/1/incidents/10/resolve", {
          method: "POST",
        });
      });
    });

    it("handles null or non-array incidents prop safely in GuardianQuarantineBanner without throwing", () => {
      const server = {
        ...mockServerGuardianEnabled,
        guardian_observed_state: "quarantined",
      };
      expect(() => {
        render(
          <GuardianQuarantineBanner
            server={server}
            incidents={null as any}
          />
        );
      }).not.toThrow();
      expect(screen.getByText(/Server in Quarantäne versetzt/i)).toBeInTheDocument();
    });

    it("handles API errors gracefully when fetching incidents in GuardianTab", async () => {
      vi.mocked(client.api).mockRejectedValue(new Error("503 Service Unavailable"));

      expect(() => {
        render(
          <MemoryRouter>
            <GuardianTab server={mockServerGuardianEnabled} />
          </MemoryRouter>
        );
      }).not.toThrow();

      await waitFor(() => {
        expect(screen.getByText(/Incident-Historie nicht verfügbar/i)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /Erneut versuchen/i })).toBeInTheDocument();
      });
    });

    it("handles non-array response gracefully in GuardianTab", async () => {
      vi.mocked(client.api).mockResolvedValue({ error: "Service Unavailable" } as any);

      render(
        <MemoryRouter>
          <GuardianTab server={mockServerGuardianEnabled} />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText(/Incident-Historie nicht verfügbar/i)).toBeInTheDocument();
      });
    });

    it("does not present a resolved quarantine incident as complete while release is pending", async () => {
      const incident: GuardianIncident = {
        id: 42,
        title: "Autopilot: process_not_running",
        description: "Synthetic quarantine incident",
        type: "process_not_running",
        status: "resolved",
        fingerprint: "synthetic-fingerprint",
        created_at: "2026-07-21T12:00:00Z",
        resolved_at: "2026-07-21T12:05:00Z",
        attempts: [],
      };
      vi.mocked(client.api).mockResolvedValue([incident]);

      render(
        <MemoryRouter>
          <GuardianTab
            server={{
              ...mockServerGuardianEnabled,
              guardian_observed_state: "quarantined",
              guardian_quarantine_clear_pending: true,
            }}
          />
        </MemoryRouter>,
      );

      await waitFor(() => {
        expect(screen.getByText(/noch nicht vom Agenten bestätigt/i)).toBeInTheDocument();
      });
      expect(screen.getAllByText(/Freigabe ausstehend/i).length).toBeGreaterThan(0);
      expect(screen.queryByText("Gelöst")).toBeNull();
    });
  });

  /**
   * Die KI-Zeile unter einem Vorfall ist die einzige Stelle, an der das Panel
   * einem Nutzer sagt, wie ein Heilungslauf ausgegangen ist. Denselben Ausgang
   * bescheinigt ihm die Ergebnis-Mail aus `ai_guardian_report.py`, die dafuer
   * `run.status in ("completed",) and vorfall.status == "resolved"` prueft. Wenn
   * Panel und Mail hier auseinanderlaufen, glaubt der Nutzer der einen Quelle
   * und laesst einen weiterhin offenen Vorfall liegen — das ist der Fehler, den
   * dieser Block ausschliesst.
   */
  describe("GuardianTab: KI-Zeile am Vorfall", () => {
    const kiUeberschrift = () => `${i18n.t("servers.guardian.tab.aiHeading")}:`;
    const satz = (name: string) => i18n.t(`servers.guardian.tab.${name}`);

    function vorfall(
      ai: GuardianIncidentAi | null | undefined,
      status = "open",
    ): GuardianIncident {
      return {
        id: 77,
        title: "Autopilot: process_not_running",
        description: "Synthetischer Vorfall fuer die KI-Zeile",
        type: "process_not_running",
        status,
        fingerprint: "fp_ai",
        created_at: "2026-08-12T12:00:00Z",
        resolved_at: status === "resolved" ? "2026-08-12T12:05:00Z" : null,
        attempts: [],
        ai,
      };
    }

    async function zeigeVorfall(inc: GuardianIncident) {
      vi.mocked(client.api).mockResolvedValue([inc]);
      render(
        <MemoryRouter>
          <GuardianTab server={mockServerGuardianEnabled} />
        </MemoryRouter>,
      );
      await screen.findByText(inc.title);
    }

    /**
     * Ohne KI-Notiz darf keine KI-Zeile stehen: ein leerer Kasten mit
     * Bot-Symbol legt nahe, die KI haette sich den Vorfall angesehen — der
     * haeufigste Fall ist aber, dass sie ihn nie gesehen hat.
     */
    it.each([
      ["ohne Feld", undefined],
      ["mit null", null],
    ])("zeigt keine KI-Zeile, wenn die Notiz fehlt (%s)", async (_name, ai) => {
      await zeigeVorfall(vorfall(ai));
      expect(screen.queryByText(kiUeberschrift())).toBeNull();
    });

    /**
     * "briefed" heisst: die KI hat nichts getan, sie wird den Vorfall beim
     * naechsten Chat erwaehnen. Diese Zeile darf nie nach Eingriff klingen —
     * `run_status` ist hier bedeutungslos und wird gar nicht erst gelesen.
     */
    it("meldet bei mode 'briefed' nur die Ankuendigung, nicht einen Lauf", async () => {
      await zeigeVorfall(
        vorfall({ mode: "briefed", run_status: null, mine: true, at: "2026-08-12T12:01:00Z" }),
      );
      expect(screen.getByText(satz("aiBriefed"))).toBeInTheDocument();
      expect(screen.queryByText(satz("aiHealing"))).toBeNull();
      expect(screen.queryByText(satz("aiUnknown"))).toBeNull();
    });

    /**
     * Ein Lauf, der noch arbeitet oder auf eine Bestaetigung wartet, ist weder
     * gelungen noch gescheitert. Wuerde das Panel ihn schon als gescheitert
     * ausweisen, wuerde ein Nutzer eingreifen, waehrend die KI noch laeuft —
     * `waiting_confirmation` wartet gerade auf genau diesen Nutzer.
     */
    it.each(["running", "waiting_confirmation", "waiting_user"])(
      "zaehlt run_status '%s' als laufend",
      async (run_status) => {
        await zeigeVorfall(
          vorfall({ mode: "healing", run_status, mine: true, at: "2026-08-12T12:01:00Z" }),
        );
        expect(screen.getByText(satz("aiHealing"))).toBeInTheDocument();
        expect(screen.queryByText(satz("aiFailed"))).toBeNull();
        expect(screen.queryByText(satz("aiHealed"))).toBeNull();
      },
    );

    /** Beide Haelften erfuellt: der Lauf ist sauber zu Ende, der Vorfall ist zu. */
    it("meldet 'behoben' nur, wenn Lauf und Vorfall beide fertig sind", async () => {
      await zeigeVorfall(
        vorfall(
          { mode: "healing", run_status: "completed", mine: true, at: "2026-08-12T12:01:00Z" },
          "resolved",
        ),
      );
      expect(screen.getByText(satz("aiHealed"))).toBeInTheDocument();
      expect(screen.queryByText(satz("aiFailed"))).toBeNull();
    });

    /**
     * Der Fall, der die Und-Verknuepfung traegt: die KI ist fertig geworden, der
     * Vorfall steht aber weiter offen — sie hat ihn also nicht behoben. Ein
     * "behoben" allein aufgrund des Laufstatus waere die schlechteste Art von
     * Fehler, denn die Ergebnis-Mail prueft zusaetzlich `vorfall.status ==
     * "resolved"` und wuerde demselben Vorfall den gegenteiligen Ausgang
     * bescheinigen.
     */
    it("meldet trotz beendetem Lauf 'nicht behoben', solange der Vorfall offen ist", async () => {
      await zeigeVorfall(
        vorfall(
          { mode: "healing", run_status: "completed", mine: true, at: "2026-08-12T12:01:00Z" },
          "open",
        ),
      );
      expect(screen.getByText(satz("aiFailed"))).toBeInTheDocument();
      expect(screen.queryByText(satz("aiHealed"))).toBeNull();
    });

    /** Ein abgebrochener Lauf ist ein Scheitern, auch wenn der Vorfall spaeter von Hand zuging. */
    it("meldet einen gescheiterten Lauf als 'nicht behoben'", async () => {
      await zeigeVorfall(
        vorfall({ mode: "healing", run_status: "failed", mine: true, at: "2026-08-12T12:01:00Z" }),
      );
      expect(screen.getByText(satz("aiFailed"))).toBeInTheDocument();
      expect(screen.queryByText(satz("aiHealed"))).toBeNull();
    });

    /**
     * Ist der Lauf abgeraeumt, kennt niemand mehr seinen Ausgang. Dann muss die
     * Zeile das offen sagen, statt sich fuer eine der beiden Behauptungen zu
     * entscheiden — "nicht behoben" waere hier genauso erfunden wie "behoben".
     */
    it("meldet einen verschwundenen Lauf als unbekannt statt als Scheitern", async () => {
      await zeigeVorfall(
        vorfall({ mode: "healing", run_status: null, mine: true, at: "2026-08-12T12:01:00Z" }),
      );
      expect(screen.getByText(satz("aiUnknown"))).toBeInTheDocument();
      expect(screen.queryByText(satz("aiFailed"))).toBeNull();
      expect(screen.queryByText(satz("aiHealed"))).toBeNull();
    });

    /**
     * Es gibt je Benutzer *und Art* eine Unterhaltung: der Lauf eines anderen
     * Freigebers laesst sich nicht oeffnen. Ein Verweis, der ins Leere fuehrt
     * oder in den eigenen, voellig anderen Verlauf, waere schlechter als gar
     * keiner — deshalb haengt der Link an `mine` und nicht daran, dass
     * ueberhaupt ein Lauf existiert.
     *
     * Und er zeigt auf das **Guardian-Fenster**, nicht auf den Dauerchat: dort
     * steht die Reparatur, seit sie ein eigenes Fenster hat. `/ai` allein
     * oeffnete den Chat, in dem von diesem Lauf keine Zeile steht.
     */
    it("bietet den Weg in das Guardian-Fenster nur beim eigenen Lauf an", async () => {
      await zeigeVorfall(
        vorfall({ mode: "healing", run_status: "running", mine: true, at: "2026-08-12T12:01:00Z" }),
      );
      const link = screen.getByRole("link", { name: satz("aiOpenChat") });
      expect(link).toHaveAttribute("href", "/ai?ansicht=guardian");
    });

    it("verschweigt den Weg in den Chat beim Lauf eines anderen Freigebers", async () => {
      await zeigeVorfall(
        vorfall({ mode: "healing", run_status: "running", mine: false, at: "2026-08-12T12:01:00Z" }),
      );
      // Die Zeile selbst bleibt sichtbar — nur der Verweis fehlt.
      expect(screen.getByText(satz("aiHealing"))).toBeInTheDocument();
      expect(screen.queryByRole("link", { name: satz("aiOpenChat") })).toBeNull();
    });

    /**
     * Bei "briefed" gibt es noch keinen Lauf, den man aufschlagen koennte; der
     * Hinweis entsteht erst mit der naechsten Nachricht des Nutzers.
     */
    it("bietet bei einer blossen Ankuendigung keinen Weg in den Chat an", async () => {
      await zeigeVorfall(
        vorfall({ mode: "briefed", run_status: null, mine: true, at: "2026-08-12T12:01:00Z" }),
      );
      expect(screen.queryByRole("link", { name: satz("aiOpenChat") })).toBeNull();
    });
  });
  /**
   * Warum es diese Tests gibt: die KI darf im Reparaturlauf die
   * Guardian-Stellschrauben dieses Servers ohne Klick verstellen. Eine
   * Verhaltensaenderung, die nirgends steht, waere schlimmer als der Vorfall,
   * den sie behebt — wer danach eine unerwartete Startfrist sucht, sucht sie im
   * Blueprint, wo sie nicht steht.
   *
   * Geprueft wird deshalb genau das Sichtbarmachen: die geltenden Zahlen, die
   * Herkunft, der Rueckweg — und dass der Rueckweg ohne `server.config.write`
   * als gesperrt zu erkennen ist statt wortlos zu verpuffen.
   */
  describe("GuardianTab: Uebersteuerung", () => {
    const satz = (name: string) => i18n.t(`servers.guardian.override.${name}`);

    function antworten(uebersteuerung: unknown) {
      vi.mocked(client.api).mockImplementation(async (path, init?: RequestInit) => {
        if (path === "/servers/1/incidents") return [];
        if (path === "/servers/1/guardian/overrides") {
          if (init?.method === "DELETE") return { ok: true, overrides: {} };
          return uebersteuerung;
        }
        return null;
      });
    }

    function zeichnen() {
      render(
        <MemoryRouter>
          <GuardianTab server={mockServerGuardianEnabled} />
        </MemoryRouter>,
      );
    }

    beforeEach(() => {
      usePermissionsStore.setState({ me: null, isLoading: false });
    });

    it("zeigt keine Karte, solange nichts uebersteuert ist", async () => {
      antworten({ overrides: {}, bounds: {}, origin: null });
      zeichnen();
      await waitFor(() => {
        expect(client.api).toHaveBeenCalledWith("/servers/1/guardian/overrides");
      });
      expect(screen.queryByText(satz("title"))).toBeNull();
    });

    it("zeigt die geltenden Werte samt Herkunft", async () => {
      antworten({
        overrides: { startup_grace_period_seconds: 600 },
        bounds: {},
        origin: { source: "ai", incident_id: 5, changed_at: "2026-08-16T10:00:00Z" },
      });
      zeichnen();
      expect(await screen.findByText(satz("title"))).toBeInTheDocument();
      expect(
        screen.getByText(i18n.t("servers.guardian.override.knob.startup_grace_period_seconds")),
      ).toBeInTheDocument();
      expect(screen.getByText("600")).toBeInTheDocument();
      // Die Herkunft nennt den Vorfall. Ohne ihn koennte die Karte zwar sagen
      // "von der KI gesetzt", aber nicht, woraufhin — und genau das fragt, wer
      // eine unerwartete Zahl sieht.
      expect(screen.getByText(/#5/)).toBeInTheDocument();
    });

    it("sperrt den Rueckweg ohne server.config.write sichtbar", async () => {
      antworten({
        overrides: { probe_interval_seconds: 42 },
        bounds: {},
        origin: null,
      });
      zeichnen();
      const knopf = await screen.findByRole("button", { name: satz("reset") });
      expect(knopf).toBeDisabled();
    });

    it("setzt mit dem Recht wirklich zurueck", async () => {
      usePermissionsStore.setState({
        me: {
          is_owner: false,
          role_id: 2,
          role_name: "user",
          global_keys: [],
          server_keys: { "1": ["server.config.write"] },
        },
        isLoading: false,
      });
      antworten({
        overrides: { probe_interval_seconds: 42 },
        bounds: {},
        origin: null,
      });
      zeichnen();
      const knopf = await screen.findByRole("button", { name: satz("reset") });
      fireEvent.click(knopf);
      await waitFor(() => {
        expect(client.api).toHaveBeenCalledWith("/servers/1/guardian/overrides", {
          method: "DELETE",
        });
      });
    });

    /**
     * Eine unerwartete Antwort darf nicht dazu fuehren, dass die Karte etwas
     * herausliest: sie zeigte sonst Zahlen an, die nirgends gelten. Und der
     * uebrige Reiter muss stehen bleiben — die Uebersteuerung ist eine
     * Zusatzauskunft, kein Grund, Vorfaelle und Zustand zu verbergen.
     */
    it("verschweigt sich bei einer unerwarteten Antwort", async () => {
      antworten([{ nichts: "davon" }]);
      zeichnen();
      await waitFor(() => {
        expect(client.api).toHaveBeenCalledWith("/servers/1/guardian/overrides");
      });
      expect(screen.queryByText(satz("title"))).toBeNull();
      expect(
        screen.getByText(i18n.t("servers.guardian.tab.overviewTitle")),
      ).toBeInTheDocument();
    });
  });
});
