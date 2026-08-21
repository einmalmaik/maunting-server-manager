/**
 * Boot-Sequenz wie beim Start eines Spiels: erst wovon es geschützt wird
 * (DIS), dann von wem es ist (MauntingStudios), dann das Produkt (MSM).
 *
 * Jede Stufe ist ein Bild mit Ein-/Ausblendung; ein Klick oder eine Taste
 * überspringt die ganze Sequenz. Fehlt eine Bilddatei (das Firmenlogo liegt
 * noch nicht im Repo), wird die Stufe still übersprungen statt ein kaputtes
 * Bild zu zeigen. Die App lädt währenddessen im Hintergrund weiter.
 */
import { useEffect, useState } from "react";

interface Stufe {
  bild: string;
  alt: string;
  untertitel: string;
}

const STUFEN: Stufe[] = [
  { bild: "/dis-logo.png", alt: "DIS", untertitel: "Geschützt durch DIS" },
  { bild: "/firmen-logo.png", alt: "MauntingStudios", untertitel: "Ein Produkt von MauntingStudios" },
  { bild: "/msm-logo.png", alt: "Maunting Server Manager", untertitel: "Maunting Smart System" },
];

const STUFEN_DAUER_MS = 2200;

export default function Splash({ onFertig }: { onFertig: () => void }) {
  const [stufe, setStufe] = useState(0);

  // Klick oder Taste überspringt alles — niemand muss die Sequenz absitzen.
  useEffect(() => {
    window.addEventListener("keydown", onFertig);
    return () => window.removeEventListener("keydown", onFertig);
  }, [onFertig]);

  useEffect(() => {
    if (stufe >= STUFEN.length) {
      onFertig();
      return;
    }
    const timer = setTimeout(() => setStufe((s) => s + 1), STUFEN_DAUER_MS);
    return () => clearTimeout(timer);
  }, [stufe, onFertig]);

  if (stufe >= STUFEN.length) {
    return null;
  }
  const aktuelle = STUFEN[stufe];

  return (
    <div
      className="fixed inset-0 z-50 flex cursor-pointer items-center justify-center bg-background"
      onClick={onFertig}
      role="presentation"
      data-testid="splash"
    >
      {/* key erzwingt einen frischen Animationslauf je Stufe. */}
      <div key={stufe} className="mss-splash-stufe flex flex-col items-center gap-4">
        <img
          src={aktuelle.bild}
          alt={aktuelle.alt}
          className="max-h-40 max-w-[60%] object-contain"
          draggable={false}
          onError={() => setStufe((s) => s + 1)}
        />
        <p className="text-xs tracking-widest text-muted-foreground uppercase">
          {aktuelle.untertitel}
        </p>
      </div>
    </div>
  );
}
