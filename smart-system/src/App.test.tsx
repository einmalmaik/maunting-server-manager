import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect, beforeEach } from "vitest";

// invoke wird gemockt: die Tests prüfen, dass die UI die richtigen Commands
// mit den richtigen Argumenten ruft — nicht Tauri selbst.
const invokeMock = vi.fn().mockResolvedValue(undefined);
vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}));

import App from "./App";
import Overlay from "./Overlay";

describe("App (Hauptfenster)", () => {
  beforeEach(() => invokeMock.mockClear());

  it("zeigt den Produktnamen", () => {
    render(<App />);
    expect(screen.getByText("Singra Smart System")).toBeInTheDocument();
  });

  it("setzt den Tray-Status ueber das Rust-Command", async () => {
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "Denkt" }));
    expect(invokeMock).toHaveBeenCalledWith("setze_status", { status: "denkt" });
  });

  it("schaltet das Overlay ueber das Rust-Command um", async () => {
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "Overlay einblenden" }));
    expect(invokeMock).toHaveBeenCalledWith("overlay_sichtbar", { sichtbar: true });
    await userEvent.click(screen.getByRole("button", { name: "Overlay ausblenden" }));
    expect(invokeMock).toHaveBeenCalledWith("overlay_sichtbar", { sichtbar: false });
  });
});

describe("Overlay (Sprachblase)", () => {
  it("rendert die pulsierende Blase", () => {
    render(<Overlay />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});
