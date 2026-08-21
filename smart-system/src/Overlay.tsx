/**
 * Das schwebende Overlay: frameless, transparent, always-on-top.
 *
 * Phase 2 (Grundgerüst): eine pulsierende Blase als Platzhalter. Die echte
 * Sprachblase mit Live-Transkription und Status-Leuchten (Design-DNA) kommt
 * in Phase 4 — Fenstertyp, Transparenz und Positionierung stehen damit schon.
 */
export default function Overlay() {
  return (
    <div className="flex h-full items-center justify-center">
      <div
        className="singra-blase flex h-24 w-24 items-center justify-center rounded-full"
        style={{
          background: "radial-gradient(circle at 35% 30%, #60a5fa, #1d4ed8 70%)",
          boxShadow: "0 0 40px 8px rgba(59, 130, 246, 0.45)",
        }}
        role="status"
        aria-label="Singra hört zu"
      >
        <span className="text-2xl font-semibold text-white">S</span>
      </div>
    </div>
  );
}
