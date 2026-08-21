/**
 * Der Chat mit dem Assistenten — derselbe Dauerchat wie im Panel.
 *
 * Live-Antworten kommen als geordnete Abschnitte (Text, Denken, Werkzeuge);
 * nach `done` wird der gespeicherte Verlauf nachgeladen, damit die Anzeige
 * die Wahrheit der Datenbank zeigt und nicht einen lokal zusammengesetzten
 * Zwischenstand. Ein laufender Zug von vorhin wird beim Öffnen über /run
 * wieder aufgenommen (der Lauf überlebt die App).
 */
import { useEffect, useRef, useState } from "react";

import {
  aktiverLauf,
  laufVerfolgen,
  nachrichtSenden,
  providerListe,
  verlaufLaden,
  type ChatAbschnitt,
  type ChatNachricht,
  type FragePayload,
  type Provider,
} from "../api/ai";
import { setzeStatus } from "../lib/tauri";
import { Fehlertext, Knopf } from "../ui";

interface LaufenderAbschnitt {
  art: "text" | "denken" | "tool";
  inhalt: string;
  werkzeug?: Record<string, unknown>;
}

export default function Chat({ agentName }: { agentName: string }) {
  const [nachrichten, setNachrichten] = useState<ChatNachricht[]>([]);
  const [provider, setProvider] = useState<Provider[]>([]);
  const [providerId, setProviderId] = useState<number | null>(null);
  const [eingabe, setEingabe] = useState("");
  const [laufend, setLaufend] = useState<LaufenderAbschnitt[] | null>(null);
  const [frage, setFrage] = useState<FragePayload | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const endeRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void (async () => {
      try {
        const [verlauf, alleProvider] = await Promise.all([verlaufLaden(), providerListe()]);
        setNachrichten(verlauf.messages);
        setProvider(alleProvider);
        const erster = alleProvider.find((p) => p.available);
        if (erster) {
          setProviderId(erster.id);
        }
        // Läuft von vorhin noch ein Zug? Dann wieder anhängen statt eine
        // abgerissene Antwort zu zeigen.
        const lauf = await aktiverLauf();
        if (lauf && lauf.status === "running") {
          setLaufend([]);
          void laufVerfolgen(lauf.id, ereignisVerarbeiten).finally(() => void fertigstellen());
        }
      } catch (e) {
        setFehler(e instanceof Error ? e.message : String(e));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // Optionaler Aufruf: jsdom (Tests) kennt scrollIntoView nicht.
    endeRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [nachrichten, laufend]);

  function ereignisVerarbeiten(ereignis: string, daten: unknown) {
    const d = daten as Record<string, unknown>;
    switch (ereignis) {
      case "snapshot": {
        const abschnitte = (d.sections as ChatAbschnitt[] | undefined) ?? [];
        setLaufend(
          abschnitte.map((a) => ({
            art: a.art,
            inhalt: a.inhalt ?? "",
            werkzeug: a.werkzeug ?? undefined,
          })),
        );
        break;
      }
      case "delta":
        anhaengen("text", String(d.content ?? ""));
        break;
      case "reasoning":
        anhaengen("denken", String(d.content ?? ""));
        break;
      case "tool":
        setLaufend((alt) => [...(alt ?? []), { art: "tool", inhalt: "", werkzeug: d }]);
        break;
      case "question":
        setFrage(d as unknown as FragePayload);
        break;
      case "error":
        setFehler(String(d.message_key ?? d.code ?? "Unbekannter Fehler"));
        break;
      default:
        break;
    }
  }

  function anhaengen(art: "text" | "denken", stueck: string) {
    if (!stueck) return;
    setLaufend((alt) => {
      const liste = [...(alt ?? [])];
      const letzter = liste[liste.length - 1];
      if (letzter && letzter.art === art) {
        liste[liste.length - 1] = { ...letzter, inhalt: letzter.inhalt + stueck };
      } else {
        liste.push({ art, inhalt: stueck });
      }
      return liste;
    });
  }

  /** Nach dem Strom: gespeicherte Wahrheit nachladen, Status zurücksetzen. */
  async function fertigstellen() {
    try {
      const verlauf = await verlaufLaden();
      setNachrichten(verlauf.messages);
    } catch {
      // Der Live-Stand bleibt sichtbar, wenn das Nachladen scheitert.
    }
    setLaufend(null);
    await setzeStatus("bereit").catch(() => {});
  }

  async function senden(text: string) {
    const inhalt = text.trim();
    if (!inhalt || providerId === null || laufend !== null) {
      return;
    }
    setFehler(null);
    setFrage(null);
    setEingabe("");
    // Die eigene Nachricht sofort zeigen; nach `done` ersetzt der geladene
    // Verlauf diese lokale Fassung durch die gespeicherte.
    setNachrichten((alt) => [
      ...alt,
      {
        id: `lokal-${Date.now()}`,
        role: "user",
        content: inhalt,
        reasoning: null,
        question: null,
        sections: null,
        status: "complete",
        provider_id: providerId,
        model: null,
        created_at: new Date().toISOString(),
      },
    ]);
    setLaufend([]);
    await setzeStatus("denkt").catch(() => {});
    try {
      await nachrichtSenden(inhalt, providerId, ereignisVerarbeiten);
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
    } finally {
      await fertigstellen();
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto pr-1" aria-label="Chatverlauf">
        {nachrichten.length === 0 && laufend === null && (
          <p className="mt-8 text-center text-sm text-muted-foreground">
            Sag {agentName} einfach, was du brauchst.
          </p>
        )}
        {nachrichten.map((n) => (
          <Nachricht key={n.id} nachricht={n} />
        ))}
        {laufend !== null && <LaufendeAntwort abschnitte={laufend} />}
        {frage && <Frage frage={frage} onAntwort={(antwort) => void senden(antwort)} />}
        <div ref={endeRef} />
      </div>

      <Fehlertext text={fehler} />

      <form
        className="flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void senden(eingabe);
        }}
      >
        <textarea
          value={eingabe}
          onChange={(e) => setEingabe(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void senden(eingabe);
            }
          }}
          placeholder={`Nachricht an ${agentName} …`}
          rows={2}
          className="flex-1 resize-none rounded-[var(--radius-control)] border border-input bg-secondary px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <div className="flex flex-col gap-1.5">
          <select
            value={providerId ?? ""}
            onChange={(e) => setProviderId(Number(e.target.value))}
            aria-label="KI-Anbieter"
            className="rounded-[var(--radius-control)] border border-input bg-secondary px-2 py-1 text-xs text-muted-foreground focus-visible:outline-none"
          >
            {provider.map((p) => (
              <option key={p.id} value={p.id} disabled={!p.available}>
                {p.name}
              </option>
            ))}
          </select>
          <Knopf type="submit" disabled={laufend !== null || eingabe.trim() === "" || providerId === null}>
            Senden
          </Knopf>
        </div>
      </form>
    </div>
  );
}

function Nachricht({ nachricht }: { nachricht: ChatNachricht }) {
  const eigene = nachricht.role === "user";
  return (
    <div className={`flex ${eigene ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[80%] rounded-[var(--radius-card)] px-4 py-3 text-sm ${
          eigene ? "bg-secondary text-foreground" : "border border-border bg-card text-card-foreground"
        }`}
      >
        {nachricht.sections && nachricht.sections.length > 0 ? (
          nachricht.sections.map((a, i) => <Abschnitt key={i} abschnitt={a} />)
        ) : (
          <p className="whitespace-pre-wrap">{nachricht.content}</p>
        )}
        {nachricht.question && (
          <p className="mt-2 text-xs text-muted-foreground">{nachricht.question.question}</p>
        )}
      </div>
    </div>
  );
}

function Abschnitt({ abschnitt }: { abschnitt: ChatAbschnitt | LaufenderAbschnitt }) {
  if (abschnitt.art === "text") {
    return <p className="whitespace-pre-wrap">{abschnitt.inhalt}</p>;
  }
  if (abschnitt.art === "denken") {
    return (
      <details className="my-1 text-xs text-muted-foreground">
        <summary className="cursor-pointer select-none">Denkschritte</summary>
        <p className="mt-1 whitespace-pre-wrap">{abschnitt.inhalt}</p>
      </details>
    );
  }
  const werkzeug = (abschnitt as ChatAbschnitt).werkzeug ?? (abschnitt as LaufenderAbschnitt).werkzeug;
  const name = typeof werkzeug?.name === "string" ? werkzeug.name : "Werkzeug";
  return (
    <span className="my-1 mr-1 inline-block rounded-full border border-border bg-muted px-2 py-0.5 text-xs text-muted-foreground">
      {name}
    </span>
  );
}

function LaufendeAntwort({ abschnitte }: { abschnitte: LaufenderAbschnitt[] }) {
  return (
    <div className="flex justify-start">
      <div className="max-w-[80%] rounded-[var(--radius-card)] border border-border bg-card px-4 py-3 text-sm text-card-foreground">
        {abschnitte.length === 0 ? (
          <p className="animate-pulse text-muted-foreground">…</p>
        ) : (
          abschnitte.map((a, i) => <Abschnitt key={i} abschnitt={a} />)
        )}
      </div>
    </div>
  );
}

function Frage({ frage, onAntwort }: { frage: FragePayload; onAntwort: (text: string) => void }) {
  return (
    <div className="flex justify-start">
      <div className="max-w-[80%] rounded-[var(--radius-card)] border border-accent/40 bg-card px-4 py-3 text-sm">
        <p className="mb-2">{frage.question}</p>
        <div className="flex flex-wrap gap-2">
          {frage.options.map((option, i) => {
            const text = typeof option === "string" ? option : String(option.label ?? "");
            if (!text) return null;
            return (
              <Knopf key={i} stimme="leise" onClick={() => onAntwort(text)}>
                {text}
              </Knopf>
            );
          })}
        </div>
      </div>
    </div>
  );
}
