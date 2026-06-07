/**
 * Tests fuer Schritt 12.2: CloudRestoreWizard.
 *
 * Deckt den Modal-Wizard fuer die Wiederherstellung von orphan
 * Cloud-Backups ab (Plan 3.7 Punkt 4):
 * - 3-Spalten-Layout (pending, in-progress, done)
 * - Checkbox-Auswahl + "Alle auswaehlen"
 * - "Auswahl wiederherstellen" ruft POST /restore-orphan/{idx}
 * - Live-Progress pro Server via Polling
 * - Done-Status mit "Zum Server"-Link
 * - Error-Status mit Fehlermeldung
 * - "Verwerfen" ruft POST /pending-restores/discard
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { CloudRestoreWizard, type PendingRestoreItem } from "./CloudRestoreWizard";
import * as client from "@/api/client";
import i18n from "@/i18n";

vi.mock("@/api/client", () => ({
  api: vi.fn(),
}));

vi.mock("@/stores/toastStore", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const sampleItem = (overrides: Partial<PendingRestoreItem> = {}): PendingRestoreItem => ({
  remote_key: "1/abc.tar.gz",
  server_id: 0,
  server_name: "Test Server",
  game_type: "dayz",
  created_at: "2025-12-01T12:00:00Z",
  panel_version: "1.0",
  cpu_limit_percent: 80,
  ram_limit_mb: 4096,
  disk_limit_gb: 20,
  size_mb: 512,
  ports: [],
  ...overrides,
});

function mockApi(handlers: Record<string, (params?: any) => any>) {
  vi.mocked(client.api).mockImplementation(async (path: string, opts?: any) => {
    for (const [pattern, handler] of Object.entries(handlers)) {
      if (path === pattern || (pattern.includes(":") && matchPath(pattern, path))) {
        return handler(opts);
      }
    }
    return undefined as any;
  });
}

function matchPath(pattern: string, actual: string): boolean {
  // Simple pattern like /backups/:id/status -> /backups/42/status
  const pParts = pattern.split("/");
  const aParts = actual.split("/");
  if (pParts.length !== aParts.length) return false;
  return pParts.every((p, i) => p.startsWith(":") || p === aParts[i]);
}

function renderWizard(items: PendingRestoreItem[] = [sampleItem()]) {
  const onClose = vi.fn();
  const result = render(
    <MemoryRouter>
      <CloudRestoreWizard items={items} provider="s3" onClose={onClose} />
    </MemoryRouter>,
  );
  return { ...result, onClose };
}

beforeEach(async () => {
  vi.mocked(client.api).mockReset();
  await i18n.changeLanguage("en");
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CloudRestoreWizard (Schritt 12.2)", () => {
  it("renders 3 columns (pending, in-progress, done)", () => {
    renderWizard();
    expect(screen.getByTestId("cloud-restore-wizard-col-pending")).toBeInTheDocument();
    expect(screen.getByTestId("cloud-restore-wizard-col-progress")).toBeInTheDocument();
    expect(screen.getByTestId("cloud-restore-wizard-col-done")).toBeInTheDocument();
  });

  it("renders pending items with checkboxes (pre-selected by default)", () => {
    renderWizard([sampleItem({ remote_key: "1/a.tar.gz" }), sampleItem({ remote_key: "2/b.tar.gz" })]);
    const items = screen.getAllByTestId("cloud-restore-wizard-pending-item");
    expect(items).toHaveLength(2);
    // Beide sollten per Default checked sein
    const checkboxes = screen.getAllByTestId("cloud-restore-wizard-checkbox") as HTMLInputElement[];
    expect(checkboxes.every((c) => c.checked)).toBe(true);
  });

  it("clicking toggle-all deselects all when all selected", () => {
    renderWizard([sampleItem({ remote_key: "1/a.tar.gz" }), sampleItem({ remote_key: "2/b.tar.gz" })]);
    const toggleBtn = screen.getByTestId("cloud-restore-wizard-toggle-all");
    expect(toggleBtn).toHaveTextContent(/deselect all/i);
    fireEvent.click(toggleBtn);
    const checkboxes = screen.getAllByTestId("cloud-restore-wizard-checkbox") as HTMLInputElement[];
    expect(checkboxes.every((c) => !c.checked)).toBe(true);
  });

  it("clicking toggle-all selects all when none selected", () => {
    renderWizard([sampleItem({ remote_key: "1/a.tar.gz" }), sampleItem({ remote_key: "2/b.tar.gz" })]);
    const toggleBtn = screen.getByTestId("cloud-restore-wizard-toggle-all");
    // Erst alle deselektieren
    fireEvent.click(toggleBtn);
    // Dann wieder alle selektieren
    fireEvent.click(toggleBtn);
    const checkboxes = screen.getAllByTestId("cloud-restore-wizard-checkbox") as HTMLInputElement[];
    expect(checkboxes.every((c) => c.checked)).toBe(true);
  });

  it("restore-selected button calls POST /setup/restore-orphan/{idx} for each selected", async () => {
    mockApi({
      "/setup/restore-orphan/0": () => ({ server_id: 100, backup_id: 1, server_name: "Test Server", status: "creating", message: "ok" }),
    });
    renderWizard([sampleItem({ remote_key: "1/a.tar.gz" })]);

    const restoreBtn = screen.getByTestId("cloud-restore-wizard-restore");
    expect(restoreBtn).not.toBeDisabled();
    fireEvent.click(restoreBtn);

    await waitFor(() => {
      expect(client.api).toHaveBeenCalledWith(
        "/setup/restore-orphan/0",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("shows live progress for in-progress server (downloading + percent)", async () => {
    let pollCount = 0;
    mockApi({
      "/setup/restore-orphan/0": () => ({ server_id: 100, backup_id: 1, server_name: "Test Server", status: "creating", message: "ok" }),
      "/backups/100/status": () => {
        pollCount++;
        return {
          active: true,
          operation: "downloading",
          phase: "download",
          bytes_done: 25 * 1024 * 1024,
          bytes_total: 100 * 1024 * 1024,
          percent: 25,
          started_at: new Date().toISOString(),
          estimated_size_mb: 100,
        };
      },
    });
    renderWizard([sampleItem({ remote_key: "1/a.tar.gz" })]);

    fireEvent.click(screen.getByTestId("cloud-restore-wizard-restore"));

    await waitFor(() => {
      expect(screen.getByTestId("cloud-restore-wizard-progress-item")).toBeInTheDocument();
    });
    const progressItem = screen.getByTestId("cloud-restore-wizard-progress-item");
    expect(progressItem).toHaveTextContent(/downloading/i);
    // Progress-Bar mit korrektem percent
    const bar = screen.getByTestId("cloud-restore-wizard-progress-bar");
    expect(bar).toHaveStyle({ width: "25%" });
    // MB-Counter
    expect(screen.getByTestId("cloud-restore-wizard-progress-counter")).toHaveTextContent(/25\.0 MB/);
    // Mindestens ein Poll-Call ist erfolgt
    expect(pollCount).toBeGreaterThanOrEqual(1);
  });

  it("moves item to done column with 'view server' link when restore completes successfully", async () => {
    mockApi({
      "/setup/restore-orphan/0": () => ({ server_id: 100, backup_id: 1, server_name: "Test Server", status: "creating", message: "ok" }),
      "/backups/100/status": () => ({
        active: false,
        operation: null,
        phase: null,
        bytes_done: null,
        bytes_total: null,
        percent: null,
        started_at: null,
        estimated_size_mb: null,
      }),
      "/servers/100": () => ({ id: 100, status: "stopped", status_message: null }),
    });
    renderWizard([sampleItem({ remote_key: "1/a.tar.gz" })]);

    fireEvent.click(screen.getByTestId("cloud-restore-wizard-restore"));

    await waitFor(() => {
      expect(screen.getByTestId("cloud-restore-wizard-done-item")).toBeInTheDocument();
    });
    const doneItem = screen.getByTestId("cloud-restore-wizard-done-item");
    expect(doneItem).toHaveAttribute("data-phase", "done");
    // "Zum Server" Button
    expect(screen.getByTestId("cloud-restore-wizard-view-server")).toBeInTheDocument();
  });

  it("moves item to done column with error message when server status=error", async () => {
    mockApi({
      "/setup/restore-orphan/0": () => ({ server_id: 100, backup_id: 1, server_name: "Test Server", status: "creating", message: "ok" }),
      "/backups/100/status": () => ({
        active: false,
        operation: null,
        phase: null,
        bytes_done: null,
        bytes_total: null,
        percent: null,
        started_at: null,
        estimated_size_mb: null,
      }),
      "/servers/100": () => ({ id: 100, status: "error", status_message: "Quelldatei fehlt" }),
    });
    renderWizard([sampleItem({ remote_key: "1/a.tar.gz" })]);

    fireEvent.click(screen.getByTestId("cloud-restore-wizard-restore"));

    await waitFor(() => {
      expect(screen.getByTestId("cloud-restore-wizard-done-item")).toBeInTheDocument();
    });
    const doneItem = screen.getByTestId("cloud-restore-wizard-done-item");
    expect(doneItem).toHaveAttribute("data-phase", "error");
    expect(screen.getByTestId("cloud-restore-wizard-error")).toHaveTextContent(/Quelldatei fehlt/);
    // KEIN "Zum Server" Link bei Fehler
    expect(screen.queryByTestId("cloud-restore-wizard-view-server")).not.toBeInTheDocument();
  });

  it("verwerfen button calls POST /setup/pending-restores/discard and onClose", async () => {
    mockApi({
      "/setup/pending-restores/discard": () => ({ ok: true, message: "ok" }),
    });
    const { onClose } = renderWizard([sampleItem()]);

    fireEvent.click(screen.getByTestId("cloud-restore-wizard-discard"));

    await waitFor(() => {
      expect(client.api).toHaveBeenCalledWith(
        "/setup/pending-restores/discard",
        expect.objectContaining({ method: "POST" }),
      );
      expect(onClose).toHaveBeenCalled();
    });
  });

  it("spaeter button calls onClose WITHOUT discarding", () => {
    mockApi({});
    const { onClose } = renderWizard([sampleItem()]);

    fireEvent.click(screen.getByTestId("cloud-restore-wizard-spaeter"));
    expect(onClose).toHaveBeenCalled();
    // KEIN POST /discard
    expect(client.api).not.toHaveBeenCalledWith(
      "/setup/pending-restores/discard",
      expect.anything(),
    );
  });

  it("clicking backdrop calls onClose", () => {
    mockApi({});
    const { onClose } = renderWizard([sampleItem()]);

    // Klick auf den Backdrop-Container (nicht den inneren Modal-Container)
    const backdrop = screen.getByTestId("cloud-restore-wizard");
    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalled();
  });

  it("clicking modal content does NOT call onClose (stopPropagation)", () => {
    mockApi({});
    const { onClose } = renderWizard([sampleItem()]);

    // Klick auf das Modal-Panel (das innere div) — sollte NICHT onClose ausloesen
    const modal = screen.getByTestId("cloud-restore-wizard").firstChild as HTMLElement;
    fireEvent.click(modal);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("clamps percent to 0-100 in progress bar (defensive)", async () => {
    mockApi({
      "/setup/restore-orphan/0": () => ({ server_id: 100, backup_id: 1, server_name: "Test Server", status: "creating", message: "ok" }),
      "/backups/100/status": () => ({
        active: true,
        operation: "downloading",
        phase: "download",
        bytes_done: 100,
        bytes_total: 100,
        percent: 150, // Buggy Provider
        started_at: null,
        estimated_size_mb: null,
      }),
    });
    renderWizard([sampleItem({ remote_key: "1/a.tar.gz" })]);

    fireEvent.click(screen.getByTestId("cloud-restore-wizard-restore"));

    await waitFor(() => {
      expect(screen.getByTestId("cloud-restore-wizard-progress-bar")).toBeInTheDocument();
    });
    const bar = screen.getByTestId("cloud-restore-wizard-progress-bar");
    expect(bar).toHaveStyle({ width: "100%" }); // clamped
  });
});
