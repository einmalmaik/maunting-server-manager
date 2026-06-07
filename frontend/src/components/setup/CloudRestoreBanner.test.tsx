/**
 * Tests fuer Schritt 12.1: CloudRestoreBanner.
 *
 * Deckt die Dashboard-Banner-Komponente fuer orphan Cloud-Backups
 * ab (Plan 3.7 Punkt 4):
 * - Renders nothing wenn keine pending restores
 * - Singular/Plural Banner-Text
 * - Cloud-Provider-Fehler (sanitized) wird dezent angezeigt
 * - Verwerfen ruft POST /pending-restores/discard
 * - X-Button dismisst lokal
 * - data-testid Hooks fuer zukuenftige E2E-Tests
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { CloudRestoreBanner } from "./CloudRestoreBanner";
import * as client from "@/api/client";
import i18n from "@/i18n";

vi.mock("@/api/client", () => ({
  api: vi.fn(),
}));

vi.mock("@/stores/toastStore", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

function mockPending(pending: { pending: boolean; items?: any[]; error?: string | null; provider?: string }) {
  vi.mocked(client.api).mockImplementation(async (path: string) => {
    if (path === "/setup/pending-restores") {
      return {
        pending: pending.pending,
        items: pending.items ?? [],
        error: pending.error ?? null,
        provider: pending.provider ?? "s3",
      } as any;
    }
    if (path === "/setup/pending-restores/discard") {
      return { ok: true, message: "ok" } as any;
    }
    return undefined as any;
  });
}

const sampleItem = (overrides: Partial<any> = {}) => ({
  remote_key: "1/abc.tar.gz",
  server_id: 1,
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

beforeEach(async () => {
  vi.mocked(client.api).mockReset();
  await i18n.changeLanguage("en");
  // window.confirm default: yes
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

describe("CloudRestoreBanner (Schritt 12.1)", () => {
  it("renders nothing when loading", () => {
    mockPending({ pending: true, items: [sampleItem()] });
    // Force pending to be unresolved
    vi.mocked(client.api).mockImplementation(() => new Promise(() => {}) as any);
    const { container } = render(<CloudRestoreBanner />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when pending=false", async () => {
    mockPending({ pending: false, items: [] });
    const { container } = render(<CloudRestoreBanner />);
    await waitFor(() => {
      expect(client.api).toHaveBeenCalledWith("/setup/pending-restores");
    });
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when items=[] even if pending=true", async () => {
    mockPending({ pending: true, items: [] });
    const { container } = render(<CloudRestoreBanner />);
    await waitFor(() => {
      expect(client.api).toHaveBeenCalled();
    });
    expect(container.firstChild).toBeNull();
  });

  it("renders banner with singular text for 1 item", async () => {
    mockPending({ pending: true, items: [sampleItem()] });
    render(<CloudRestoreBanner />);

    await waitFor(() => {
      expect(screen.getByTestId("cloud-restore-banner")).toBeInTheDocument();
    });
    // Singular: "1 cloud backup found..."
    expect(screen.getByText(/1 cloud backup found/i)).toBeInTheDocument();
  });

  it("renders banner with plural text for N items", async () => {
    mockPending({
      pending: true,
      items: [sampleItem({ remote_key: "1/a.tar.gz" }), sampleItem({ remote_key: "2/b.tar.gz" }), sampleItem({ remote_key: "3/c.tar.gz" })],
    });
    render(<CloudRestoreBanner />);

    await waitFor(() => {
      expect(screen.getByTestId("cloud-restore-banner")).toBeInTheDocument();
    });
    // Plural: "{{count}} cloud backups found..."
    expect(screen.getByText(/3 cloud backups found/i)).toBeInTheDocument();
  });

  it("renders cloud provider error when error is set", async () => {
    mockPending({ pending: true, items: [sampleItem()], error: "Auth failed" });
    render(<CloudRestoreBanner />);

    await waitFor(() => {
      expect(screen.getByTestId("cloud-restore-banner-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("cloud-restore-banner-error")).toHaveTextContent(/Auth failed/);
  });

  it("does NOT render error element when error is null", async () => {
    mockPending({ pending: true, items: [sampleItem()], error: null });
    render(<CloudRestoreBanner />);

    await waitFor(() => {
      expect(screen.getByTestId("cloud-restore-banner")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("cloud-restore-banner-error")).not.toBeInTheDocument();
  });

  it("verwerfen button calls POST /setup/pending-restores/discard", async () => {
    mockPending({ pending: true, items: [sampleItem()] });
    render(<CloudRestoreBanner />);

    await waitFor(() => {
      expect(screen.getByTestId("cloud-restore-banner-discard")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("cloud-restore-banner-discard"));

    await waitFor(() => {
      expect(client.api).toHaveBeenCalledWith(
        "/setup/pending-restores/discard",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("dismiss button (X) hides banner locally", async () => {
    mockPending({ pending: true, items: [sampleItem()] });
    const { rerender } = render(<CloudRestoreBanner />);

    await waitFor(() => {
      expect(screen.getByTestId("cloud-restore-banner")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("cloud-restore-banner-dismiss"));
    // Banner muss weg sein nach Re-Render
    await waitFor(() => {
      expect(screen.queryByTestId("cloud-restore-banner")).not.toBeInTheDocument();
    });
    // ...und bleibt auch nach Re-Render weg
    rerender(<CloudRestoreBanner />);
    expect(screen.queryByTestId("cloud-restore-banner")).not.toBeInTheDocument();
  });

  it("renders open button (data-testid hook)", async () => {
    mockPending({ pending: true, items: [sampleItem()] });
    render(<CloudRestoreBanner />);

    await waitFor(() => {
      expect(screen.getByTestId("cloud-restore-banner-open")).toBeInTheDocument();
    });
  });
});
