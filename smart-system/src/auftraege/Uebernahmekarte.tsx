/**
 * Die Bestätigungskarte für die Übernahme von Maus und Tastatur.
 *
 * Der einzige Weg zur Freigabe. Die KI kann sie anfragen, aber nicht
 * erteilen: erst ein Klick hier ruft `uebernahme_freigeben`, und die Frist
 * selbst liegt in Rust — nicht im Panel, nicht in dieser Datei.
 *
 * Die Karte meldet auch das Ergebnis des Auftrags zurück, denn genau dieser
 * eine Auftrag hat keins, wenn er ausgeführt wird: er wartet auf einen
 * Menschen. Antwortet niemand, verfällt er panelseitig nach zehn Minuten, und
 * das Modell erfährt das als Verfall statt als Stille.
 */
import { useCallback, useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";

import { ergebnisMelden } from "../api/desktop";
import { uebernahmeFreigeben, uebernahmeWiderrufen, uebernahmeRest } from "../lib/tauri";
import { Knopf } from "../ui";

interface Anfrage {
  anliegen: string;
  minuten: number;
  auftragId: string;
}

export const EREIGNIS_UEBERNAHME = "singra:uebernahme-anfrage";

export default function Uebernahmekarte({
  offenerAuftragId,
}: {
  offenerAuftragId: string | null;
}) {
  const [anfrage, setAnfrage] = useState<Anfrage | null>(null);
  const [rest, setRest] = useState(0);

  useEffect(() => {
    const abmelden = listen<{ anliegen: string; minuten: number }>(
      EREIGNIS_UEBERNAHME,
      (ereignis) => {
        if (!offenerAuftragId) {
          return;
        }
        setAnfrage({
          anliegen: ereignis.payload.anliegen,
          minuten: ereignis.payload.minuten,
          auftragId: offenerAuftragId,
        });
      },
    );
    return () => {
      void abmelden.then((weg) => weg());
    };
  }, [offenerAuftragId]);

  // Eine laufende Übernahme muss man sehen. Eine, die man nicht sieht, wäre
  // die schlechteste Fassung dieser Funktion.
  useEffect(() => {
    const takt = setInterval(() => {
      void uebernahmeRest().then(setRest).catch(() => setRest(0));
    }, 1000);
    return () => clearInterval(takt);
  }, []);

  const entscheiden = useCallback(
    async (erteilt: boolean) => {
      if (!anfrage) return;
      if (erteilt) {
        await uebernahmeFreigeben(anfrage.minuten);
      }
      await ergebnisMelden(anfrage.auftragId, true, {
        freigegeben: erteilt,
        minuten: erteilt ? anfrage.minuten : 0,
        hinweis: erteilt
          ? "Der Benutzer hat die Übernahme freigegeben. Sie endet nach der genannten Zeit von selbst."
          : "Der Benutzer hat die Übernahme abgelehnt. Frag nicht sofort erneut — such einen Weg ohne Maus und Tastatur.",
      });
      setAnfrage(null);
    },
    [anfrage],
  );

  if (anfrage) {
    return (
      <div className="fixed inset-0 z-40 flex items-center justify-center bg-background/80 p-6">
        <div className="max-w-md rounded-[var(--radius-card)] border border-accent/50 bg-card p-6 shadow-panel">
          <h2 className="text-lg font-semibold">Darf ich deinen Rechner bedienen?</h2>
          <p className="mt-3 text-sm text-muted-foreground">{anfrage.anliegen}</p>
          <p className="mt-3 text-xs text-muted-foreground">
            Freigabe für {anfrage.minuten} Minuten. Du kannst sie jederzeit beenden, und sie
            läuft danach von selbst ab. Maus und Tastatur bleiben deine — bewegst du die Maus,
            merkst du es sofort.
          </p>
          <div className="mt-5 flex gap-2">
            <Knopf onClick={() => void entscheiden(true)}>Freigeben</Knopf>
            <Knopf stimme="leise" onClick={() => void entscheiden(false)}>
              Ablehnen
            </Knopf>
          </div>
        </div>
      </div>
    );
  }

  if (rest > 0) {
    return (
      <div className="fixed bottom-4 right-4 z-30 flex items-center gap-3 rounded-[var(--radius-card)] border border-accent/50 bg-card px-4 py-2 text-xs shadow-panel">
        <span className="singra-blase inline-block h-2 w-2 rounded-full bg-accent" />
        <span>
          Übernahme aktiv — noch {Math.floor(rest / 60)}:
          {String(rest % 60).padStart(2, "0")}
        </span>
        <Knopf stimme="leise" onClick={() => void uebernahmeWiderrufen()}>
          Beenden
        </Knopf>
      </div>
    );
  }

  return null;
}
