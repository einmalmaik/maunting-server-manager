import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect, beforeEach } from "vitest";

// invoke wird gemockt: die Tests prüfen, dass die UI die richtigen Commands
// mit den richtigen Argumenten ruft — nicht Tauri selbst.
const invokeMock = vi.fn(async (...args: unknown[]) => {
  if (args[0] === "wakeword_stand") {
    return { aufnahmen: 0, trainiert: false, lauscht: false };
  }
  return undefined;
});
vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}));
vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(async () => () => {}),
}));

import App from "./App";
import Overlay from "./Overlay";

describe("App (Hauptfenster)", () => {
  beforeEach(() => invokeMock.mockClear());

  it("zeigt den Produktnamen", async () => {
    render(<App />);
    expect(screen.getByText("Singra Smart System")).toBeInTheDocument();
    // Wake-Word-Stand wurde geladen (Mount-Effekt abwarten, sonst act-Warnung)
    expect(await screen.findByText("0/10 Aufnahmen")).toBeInTheDocument();
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

describe("Wake-Word-Einrichtung", () => {
  beforeEach(() => invokeMock.mockClear());

  it("startet die naechste Aufnahme mit fortlaufender Nummer", async () => {
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Aufnahme 1 starten" }));
    expect(invokeMock).toHaveBeenCalledWith("wakeword_aufnehmen", { nummer: 1 });
  });

  it("laesst Training ohne Aufnahmen nicht zu", async () => {
    render(<App />);
    const trainieren = await screen.findByRole("button", { name: "Trainieren" });
    expect(trainieren).toBeDisabled();
  });

  it("laesst Lauschen ohne trainiertes Modell nicht zu", async () => {
    render(<App />);
    const lauschen = await screen.findByRole("button", { name: "Lauschen starten" });
    expect(lauschen).toBeDisabled();
  });
});

describe("Overlay (Sprachblase)", () => {
  it("rendert die pulsierende Blase", () => {
    render(<Overlay />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});
