/**
 * KI-Chat gegen die Panel-Endpunkte (backend/routers/ai_chat.py).
 *
 * Der Desktop-Chat schreibt in denselben Dauerchat (kind=primary) wie das
 * Panel — gleiche Unterhaltung, gleiches Gedächtnis, gleiche Rechte
 * (ai.chat.use). Streaming ist POST-SSE; ein laufender Zug überlebt die App
 * (ai_runs) und wird beim nächsten Start über /run wieder aufgenommen.
 */
import { api, apiStrom } from "./client";

export interface Provider {
  id: number;
  name: string;
  default_model: string;
  available: boolean;
  reasoning: boolean;
  efforts: string[];
  can_disable: boolean;
  default_effort: string | null;
}

export interface ChatAbschnitt {
  art: "text" | "tool" | "denken";
  inhalt: string | null;
  werkzeug: Record<string, unknown> | null;
}

export interface FrageOption {
  label?: string;
  [key: string]: unknown;
}

export interface FragePayload {
  question: string;
  options: (string | FrageOption)[];
}

export interface ChatNachricht {
  id: string;
  role: "user" | "assistant";
  content: string;
  reasoning: string | null;
  question: FragePayload | null;
  sections: ChatAbschnitt[] | null;
  status: string;
  provider_id: number | null;
  model: string | null;
  created_at: string;
}

export interface Verlauf {
  id: string;
  messages: ChatNachricht[];
  has_more: boolean;
}

export interface AktiverLauf {
  id: string;
  status: string;
  stop_reason: string | null;
  message_id: string | null;
}

export type StromEreignis = (ereignis: string, daten: unknown) => void;

export async function providerListe(): Promise<Provider[]> {
  return await api<Provider[]>("/api/ai/providers");
}

export async function verlaufLaden(before?: string): Promise<Verlauf> {
  const anhang = before ? `&before=${encodeURIComponent(before)}` : "";
  return await api<Verlauf>(`/api/ai/conversation?kind=primary${anhang}`);
}

export async function nachrichtSenden(
  inhalt: string,
  providerId: number,
  onEreignis: StromEreignis,
): Promise<void> {
  await apiStrom(
    "/api/ai/conversation/messages/stream",
    {
      body: {
        content: inhalt,
        provider_id: providerId,
        request_id: crypto.randomUUID(),
        reasoning: false,
        // Hier stand `herkunft: "desktop"`. Der Client sagt das nicht mehr
        // selbst — es steht im Token, das beim Koppeln entstanden ist. Eine
        // Angabe, die entscheidet, ob die KI Maus und Tastatur anfassen darf,
        // gehört nicht in einen Request-Körper, den jeder schreiben kann.
      },
    },
    onEreignis,
  );
}

/** Läuft für diesen Benutzer im Dauerchat gerade noch etwas? */
export async function aktiverLauf(): Promise<AktiverLauf | null> {
  return await api<AktiverLauf | null>("/api/ai/conversation/run?kind=primary");
}

/** Hängt sich an einen laufenden Zug — erst `snapshot`, dann live. */
export async function laufVerfolgen(runId: string, onEreignis: StromEreignis): Promise<void> {
  await apiStrom(
    `/api/ai/conversation/run/${encodeURIComponent(runId)}/stream`,
    { method: "GET" },
    onEreignis,
  );
}
