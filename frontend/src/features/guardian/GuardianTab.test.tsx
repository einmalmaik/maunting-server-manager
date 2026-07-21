import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/i18n";
import { GuardianTab } from "./GuardianTab";
import { GuardianBadge } from "./GuardianBadge";
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

      const button = screen.getByRole("button", { name: /Quarantäne aufheben/i });
      fireEvent.click(button);

      await waitFor(() => {
        expect(client.api).toHaveBeenCalledWith(
          "/servers/1/incidents/42/resolve",
          { method: "POST" }
        );
        expect(onRefresh).toHaveBeenCalled();
      });
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
  });
});
