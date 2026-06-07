/**
 * Tests fuer Schritt 12.1: CloudMigrationBanner.
 *
 * Deckt die Dashboard-Banner-Komponente fuer die laufende
 * Auto-Migration ab (Plan 3.10):
 * - Renders nothing bei idle/cancelled
 * - Running-Status mit Progress + Cancel-Button
 * - Completed-Status (gruen, dismissbar)
 * - Failed-Status (rot, mit letzter Fehlermeldung)
 * - Cancel-Button ruft POST /migration-cancel
 * - Defensive percent-clamping
 * - data-testid Hooks
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { CloudMigrationBanner } from "./CloudMigrationBanner";
import * as client from "@/api/client";
import i18n from "@/i18n";

vi.mock("@/api/client", () => ({
  api: vi.fn(),
}));

vi.mock("@/stores/toastStore", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

function mockStatus(status: any) {
  vi.mocked(client.api).mockImplementation(async (path: string) => {
    if (path === "/setup/migration-status") return status as any;
    if (path === "/setup/migration-cancel") return { ok: true, message: "ok" } as any;
    return undefined as any;
  });
}

const baseStatus = {
  status: "idle",
  total: 0,
  migrated: 0,
  failed: 0,
  current_server_id: null,
  current_filename: null,
  started_at: null,
  finished_at: null,
  last_error: null,
  target_provider: "s3",
};

beforeEach(async () => {
  vi.mocked(client.api).mockReset();
  await i18n.changeLanguage("en");
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

describe("CloudMigrationBanner (Schritt 12.1)", () => {
  it("renders nothing when status=idle", async () => {
    mockStatus({ ...baseStatus, status: "idle" });
    const { container } = render(<CloudMigrationBanner />);
    await waitFor(() => {
      expect(client.api).toHaveBeenCalled();
    });
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when status=cancelled", async () => {
    mockStatus({ ...baseStatus, status: "cancelled" });
    const { container } = render(<CloudMigrationBanner />);
    await waitFor(() => {
      expect(client.api).toHaveBeenCalled();
    });
    expect(container.firstChild).toBeNull();
  });

  it("renders running status with progress bar", async () => {
    mockStatus({
      ...baseStatus,
      status: "running",
      total: 10,
      migrated: 3,
      current_filename: "5/myserver.tar.gz",
    });
    render(<CloudMigrationBanner />);

    await waitFor(() => {
      expect(screen.getByTestId("cloud-migration-banner")).toBeInTheDocument();
    });
    expect(screen.getByTestId("cloud-migration-banner")).toHaveAttribute("data-status", "running");
    // Progress-Bar (role progressbar)
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    // Cancel-Button
    expect(screen.getByTestId("cloud-migration-banner-cancel")).toBeInTheDocument();
  });

  it("clamps progress percent to 0-100 range", async () => {
    mockStatus({ ...baseStatus, status: "running", total: 5, migrated: 1000 }); // 20000%
    render(<CloudMigrationBanner />);

    await waitFor(() => {
      expect(screen.getByTestId("cloud-migration-progress-bar")).toBeInTheDocument();
    });
    const bar = screen.getByTestId("cloud-migration-progress-bar");
    expect(bar).toHaveStyle({ width: "100%" });
  });

  it("renders completed status (green, dismissable)", async () => {
    mockStatus({ ...baseStatus, status: "completed", total: 5, migrated: 5 });
    render(<CloudMigrationBanner />);

    await waitFor(() => {
      expect(screen.getByTestId("cloud-migration-banner")).toBeInTheDocument();
    });
    expect(screen.getByTestId("cloud-migration-banner")).toHaveAttribute("data-status", "completed");
    // Dismiss-Button
    expect(screen.getByTestId("cloud-migration-banner-dismiss")).toBeInTheDocument();
  });

  it("renders failed status with error message", async () => {
    mockStatus({
      ...baseStatus,
      status: "failed",
      last_error: "S3 credentials invalid",
    });
    render(<CloudMigrationBanner />);

    await waitFor(() => {
      expect(screen.getByTestId("cloud-migration-banner")).toBeInTheDocument();
    });
    expect(screen.getByTestId("cloud-migration-banner")).toHaveAttribute("data-status", "failed");
    expect(screen.getByTestId("cloud-migration-banner-error")).toHaveTextContent(/S3 credentials invalid/);
  });

  it("cancel button calls POST /setup/migration-cancel", async () => {
    mockStatus({ ...baseStatus, status: "running", total: 10, migrated: 3 });
    render(<CloudMigrationBanner />);

    await waitFor(() => {
      expect(screen.getByTestId("cloud-migration-banner-cancel")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("cloud-migration-banner-cancel"));

    await waitFor(() => {
      expect(client.api).toHaveBeenCalledWith(
        "/setup/migration-cancel",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("dismiss button hides failed banner", async () => {
    mockStatus({ ...baseStatus, status: "failed", last_error: "boom" });
    render(<CloudMigrationBanner />);

    await waitFor(() => {
      expect(screen.getByTestId("cloud-migration-banner")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("cloud-migration-banner-dismiss"));
    await waitFor(() => {
      expect(screen.queryByTestId("cloud-migration-banner")).not.toBeInTheDocument();
    });
  });
});
