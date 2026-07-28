import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/i18n";
import { GuardianTab } from "./GuardianTab";
import { GuardianBadge, getGuardianDisplayState } from "./GuardianBadge";
import { GuardianQuarantineBanner } from "./GuardianQuarantineBanner";
import * as client from "@/api/client";
import type { Server, GuardianIncident } from "@/types";

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
});
