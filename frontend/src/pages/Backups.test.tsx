"""Tests fuer Schritt 11: Backups.tsx Live-Progress (Progress-Bar + MB-Zaehler).

Deckt die UI-Verbesserung aus Plan 3.4 ab:
- Echte Progress-Bar mit style={{ width: percent% }} statt animate-pulse
- MB-Zaehler (bytes_done/bytes_total -> /1024/1024 -> "523.0 MB / 2048.0 MB")
- Prozent-Anzeige in monospace unter der Bar
- Operation-Label-Mapping: uploading/downloading/decrypting
- Accessibility: role="progressbar" + aria-valuenow/min/max
- data-testid Hooks fuer zukuenftige E2E-Tests
"""
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Backups } from "./Backups";
import * as client from "@/api/client";
import i18n from "@/i18n";

vi.mock("@/api/client", () => ({
  api: vi.fn(),
}));

vi.mock("@/hooks/useHasPermission", () => ({
  useHasPermission: () => true,
}));

vi.mock("@/stores/toastStore", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/stores/confirmStore", () => ({
  confirm: vi.fn(() => Promise.resolve(true)),
}));

function mockStatus(
  status: {
    active: boolean;
    operation?: string | null;
    phase?: string | null;
    bytes_done?: number | null;
    bytes_total?: number | null;
    percent?: number | null;
    started_at?: string | null;
    estimated_size_mb?: number | null;
  },
) {
  vi.mocked(client.api).mockImplementation(async (path: string) => {
    if (path.includes("/status")) return status as any;
    if (path.match(/\/backups\/\d+$/)) return [] as any;
    if (path.includes("/settings"))
      return {
        backup_on_start: false,
        backup_interval_hours: null,
        backup_retention_count: 5,
      } as any;
    return undefined as any;
  });
}

function renderBackups() {
  return render(
    <MemoryRouter>
      <Backups serverId={42} />
    </MemoryRouter>,
  );
}

beforeEach(async () => {
  vi.mocked(client.api).mockReset();
  await i18n.changeLanguage("en");
});

describe("Backups live progress (Schritt 11)", () => {
  it("shows indeterminate pulse bar when no bytes data (local-Provider creating)", async () => {
    mockStatus({
      active: true,
      operation: "creating",
      phase: "create",
      bytes_done: null,
      bytes_total: null,
      percent: null,
      started_at: new Date().toISOString(),
    });
    renderBackups();

    await waitFor(() => {
      expect(screen.getByTestId("backup-live-status")).toBeInTheDocument();
    });

    // Progress-Bar (role progressbar) ist da
    const bar = screen.getByRole("progressbar");
    expect(bar).toBeInTheDocument();
    // KEIN data-testid="backup-progress-bar" weil deterministisch
    expect(screen.queryByTestId("backup-progress-bar")).not.toBeInTheDocument();
    // KEIN MB-Counter weil bytes_done fehlt
    expect(screen.queryByTestId("backup-progress-counter")).not.toBeInTheDocument();
  });

  it("shows real progress bar with percent + MB counter when bytes data provided", async () => {
    // 1 GB done / 4 GB total = 25%
    mockStatus({
      active: true,
      operation: "uploading",
      phase: "upload",
      bytes_done: 1024 * 1024 * 1024,
      bytes_total: 4 * 1024 * 1024 * 1024,
      percent: 25,
      started_at: new Date().toISOString(),
    });
    renderBackups();

    await waitFor(() => {
      expect(screen.getByTestId("backup-progress-bar")).toBeInTheDocument();
    });

    const bar = screen.getByTestId("backup-progress-bar");
    expect(bar).toHaveStyle({ width: "25%" });

    const counter = screen.getByTestId("backup-progress-counter");
    // 1024 MB / 4096 MB (with .toFixed(1) formatting)
    expect(counter).toHaveTextContent(/1024\.0 MB/);
    expect(counter).toHaveTextContent(/4096\.0 MB/);
    expect(counter).toHaveTextContent(/25%/);
  });

  it("shows uploading operation label", async () => {
    mockStatus({
      active: true,
      operation: "uploading",
      phase: "upload",
      bytes_done: 100,
      bytes_total: 1000,
      percent: 10,
    });
    renderBackups();

    await waitFor(() => {
      expect(screen.getByTestId("backup-live-status")).toBeInTheDocument();
    });
    // i18n key backups.uploading -> "Uploading..."
    expect(screen.getByText(/uploading\.\.\./i)).toBeInTheDocument();
  });

  it("shows downloading operation label", async () => {
    mockStatus({
      active: true,
      operation: "downloading",
      phase: "download",
      bytes_done: 500,
      bytes_total: 1000,
      percent: 50,
    });
    renderBackups();

    await waitFor(() => {
      expect(screen.getByText(/downloading\.\.\./i)).toBeInTheDocument();
    });
  });

  it("shows decrypting operation label", async () => {
    mockStatus({
      active: true,
      operation: "decrypting",
      phase: "decrypt",
      bytes_done: null,
      bytes_total: null,
      percent: null,
    });
    renderBackups();

    await waitFor(() => {
      expect(screen.getByText(/decrypting\.\.\./i)).toBeInTheDocument();
    });
  });

  it("hides live status banner when inactive", async () => {
    mockStatus({ active: false });
    renderBackups();

    await waitFor(() => {
      expect(client.api).toHaveBeenCalled();
    });
    expect(screen.queryByTestId("backup-live-status")).not.toBeInTheDocument();
  });

  it("clamps percent to 0-100 range (defensive)", async () => {
    // Provider-Bug: percent=150 wuerde ueber die Bar hinausschiessen
    mockStatus({
      active: true,
      operation: "uploading",
      phase: "upload",
      bytes_done: 1000,
      bytes_total: 1000,
      percent: 150, // Buggy Provider
    });
    renderBackups();

    await waitFor(() => {
      expect(screen.getByTestId("backup-progress-bar")).toBeInTheDocument();
    });
    const bar = screen.getByTestId("backup-progress-bar");
    expect(bar).toHaveStyle({ width: "100%" }); // clamped

    // Negative percent auch testen
    mockStatus({
      active: true,
      operation: "uploading",
      phase: "upload",
      bytes_done: 0,
      bytes_total: 1000,
      percent: -10, // Buggy Provider
    });
    // Re-render mit neuem Mock
    render(
      <MemoryRouter>
        <Backups serverId={43} />
      </MemoryRouter>,
    );
    await waitFor(() => {
      const bars = screen.getAllByTestId("backup-progress-bar");
      const negativeBar = bars[bars.length - 1];
      expect(negativeBar).toHaveStyle({ width: "0%" });
    });
  });

  it("formats bytes with 1 decimal place (1024 MiB -> 1024.0 MB)", async () => {
    // 1024.5 MB done
    const bytesDone = Math.floor(1024.5 * 1024 * 1024);
    mockStatus({
      active: true,
      operation: "uploading",
      phase: "upload",
      bytes_done: bytesDone,
      bytes_total: bytesDone * 2,
      percent: 50,
    });
    renderBackups();

    await waitFor(() => {
      expect(screen.getByTestId("backup-progress-counter")).toBeInTheDocument();
    });
    // toFixed(1) rounding
    expect(screen.getByTestId("backup-progress-counter")).toHaveTextContent(
      /1024\.5 MB/,
    );
  });

  it("sets ARIA progressbar attributes (a11y)", async () => {
    mockStatus({
      active: true,
      operation: "uploading",
      phase: "upload",
      bytes_done: 250,
      bytes_total: 1000,
      percent: 25,
    });
    renderBackups();

    await waitFor(() => {
      expect(screen.getByTestId("backup-progress-bar")).toBeInTheDocument();
    });
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
    expect(bar).toHaveAttribute("aria-valuenow", "25");
  });

  it("shows estimated_size_mb fallback in the header row", async () => {
    mockStatus({
      active: true,
      operation: "creating",
      phase: "create",
      bytes_done: null,
      bytes_total: null,
      percent: null,
      estimated_size_mb: 512,
    });
    renderBackups();

    await waitFor(() => {
      expect(screen.getByTestId("backup-live-status")).toBeInTheDocument();
    });
    expect(screen.getByText(/estimated size/i)).toBeInTheDocument();
    expect(screen.getByText(/512 MB/)).toBeInTheDocument();
  });
});
