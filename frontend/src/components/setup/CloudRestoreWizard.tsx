import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { X, Check, Loader2, CheckCircle2, AlertTriangle, Download, Archive, Database, ExternalLink } from "lucide-react";
import { api } from "@/api/client";
import { toast } from "@/stores/toastStore";

export interface PendingRestoreItem {
  remote_key: string;
  server_id: number;
  server_name: string;
  game_type: string;
  created_at: string;
  panel_version: string;
  cpu_limit_percent: number | null;
  ram_limit_mb: number | null;
  disk_limit_gb: number | null;
  size_mb: number | null;
  ports: Array<{ role: string; port?: number; protocol?: string }>;
}

interface RestoreOrphanAccepted {
  server_id: number;
  backup_id: number;
  server_name: string;
  status: string;
  message: string;
}

interface BackupStatus {
  active: boolean;
  operation: string | null;
  phase: "create" | "upload" | "download" | "extract" | "decrypt" | null;
  bytes_done: number | null;
  bytes_total: number | null;
  percent: number | null;
  started_at: string | null;
  estimated_size_mb: number | null;
}

interface ServerSummary {
  id: number;
  status: "stopped" | "running" | "creating" | "error" | string;
  status_message: string | null;
}

type Phase = "pending" | "creating" | "downloading" | "decrypting" | "extracting" | "done" | "error";

interface ItemState {
  remote_key: string;
  idx: number;
  server_id: number | null;
  server_name: string;
  game_type: string;
  created_at: string;
  size_mb: number | null;
  phase: Phase;
  bytes_done: number | null;
  bytes_total: number | null;
  percent: number | null;
  error_message: string | null;
}

const POLL_INTERVAL_MS = 1_000;

function mapPhaseFromStatus(status: BackupStatus): { phase: Phase; percent: number | null; bytes_done: number | null; bytes_total: number | null } {
  // Wenn nicht mehr aktiv: phase-Info ist weg, der Caller muss /servers pruefen
  if (!status.active) {
    return { phase: "creating", percent: null, bytes_done: null, bytes_total: null };
  }
  const percent = status.percent != null ? Math.max(0, Math.min(100, status.percent)) : null;
  switch (status.phase) {
    case "download":
      return { phase: "downloading", percent, bytes_done: status.bytes_done, bytes_total: status.bytes_total };
    case "decrypt":
      return { phase: "decrypting", percent, bytes_done: status.bytes_done, bytes_total: status.bytes_total };
    case "extract":
      return { phase: "extracting", percent, bytes_done: status.bytes_done, bytes_total: status.bytes_total };
    case "create":
    case "upload":
    default:
      return { phase: "creating", percent, bytes_done: status.bytes_done, bytes_total: status.bytes_total };
  }
}

function formatBytes(bytes: number | null): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

interface CloudRestoreWizardProps {
  items: PendingRestoreItem[];
  provider: string;
  onClose: () => void;
}

/**
 * CloudRestoreWizard — Modal-Wizard fuer die Wiederherstellung von
 * orphan Cloud-Backups (Plan 3.7 Punkt 4).
 *
 * UX: 3-Spalten-Layout (responsive: mobile = 1 Spalte gestapelt)
 *   - Spalte 1: Pending-Liste mit Checkbox + Server-Info
 *   - Spalte 2: In-Progress-Liste mit Live-Phase + Progress
 *   - Spalte 3: Done-Liste mit "Zum Server" oder Fehlertext
 *
 * Ablauf:
 *   1. User waehlt N Eintraege aus Spalte 1
 *   2. Klick "Auswahl wiederherstellen": fuer jeden Eintrag
 *      POST /api/setup/restore-orphan/{idx} -> server_id zurueck
 *   3. Spalte 2 pollt /api/backups/{id}/status pro Server alle 1s
 *   4. Sobald active=false: GET /api/servers/{id} fuer final-Status
 *      (stopped = done, error = fehler)
 *   5. Eintrag wandert in Spalte 3
 *
 * Buttons:
 *   - "Auswahl wiederherstellen"  (postet N Requests parallel)
 *   - "Alle auswaehlen"           (selektiert alle pending)
 *   - "Spaeter"                   (schliesst Wizard, Banner bleibt)
 *   - "Liste verwerfen"           (POST /pending-restores/discard, dann onClose)
 */
export function CloudRestoreWizard({ items, provider, onClose }: CloudRestoreWizardProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  // Item-State (initial aus props)
  const [itemStates, setItemStates] = useState<ItemState[]>(() =>
    items.map((it, idx) => ({
      remote_key: it.remote_key,
      idx,
      server_id: null,
      server_name: it.server_name || t("setup.cloudRestore.table.name"),
      game_type: it.game_type,
      created_at: it.created_at,
      size_mb: it.size_mb,
      phase: "pending" as Phase,
      bytes_done: null,
      bytes_total: null,
      percent: null,
      error_message: null,
    })),
  );

  const [selected, setSelected] = useState<Set<string>>(() => new Set(items.map((it) => it.remote_key)));
  const [starting, setStarting] = useState(false);

  // Refs, um in Polling-Closures frisch zu bleiben
  const itemStatesRef = useRef(itemStates);
  itemStatesRef.current = itemStates;

  // ── Start-Restore fuer alle selected Items ────────────────────────────
  const startSelected = useCallback(async () => {
    if (selected.size === 0) return;
    setStarting(true);
    const toStart = Array.from(selected);
    setSelected(new Set());

    // Optimistisch: phase auf "creating", server_id noch null
    setItemStates((prev) =>
      prev.map((s) =>
        toStart.includes(s.remote_key)
          ? { ...s, phase: "creating" as Phase }
          : s,
      ),
    );

    // Parallele POSTs (Backend startet jeweils einen Background-Task)
    const results = await Promise.allSettled(
      toStart.map(async (remote_key) => {
        const item = itemStatesRef.current.find((s) => s.remote_key === remote_key);
        if (!item) throw new Error("Item nicht in State-Liste");
        const res = await api<RestoreOrphanAccepted>(
          `/setup/restore-orphan/${item.idx}`,
          { method: "POST" },
        );
        return { remote_key, server_id: res.server_id, server_name: res.server_name };
      }),
    );

    // Pro remote_key: success (server_id setzen) oder error (Phase markieren)
    const successByKey = new Map<string, { server_id: number; server_name: string }>();
    const errorByKey = new Map<string, string>();
    results.forEach((r, i) => {
      const rk = toStart[i];
      if (r.status === "fulfilled") {
        successByKey.set(rk, { server_id: r.value.server_id, server_name: r.value.server_name });
      } else {
        errorByKey.set(rk, (r.reason as Error)?.message || "Start fehlgeschlagen");
      }
    });

    setItemStates((prev) =>
      prev.map((s) => {
        const ok = successByKey.get(s.remote_key);
        if (ok) {
          return { ...s, server_id: ok.server_id, server_name: ok.server_name };
        }
        const err = errorByKey.get(s.remote_key);
        if (err) {
          return { ...s, phase: "error" as Phase, error_message: err };
        }
        return s;
      }),
    );

    toast.success(t("setup.cloudRestore.restoreStarted", "Wiederherstellung gestartet"));
    setStarting(false);
  }, [selected, t]);

  // ── Polling fuer alle in-progress Items ───────────────────────────────
  useEffect(() => {
    const inProgress = itemStates.filter(
      (s) =>
        s.server_id != null &&
        (s.phase === "creating" || s.phase === "downloading" || s.phase === "decrypting" || s.phase === "extracting"),
    );
    if (inProgress.length === 0) return;

    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      const updates: Array<{ remote_key: string; patch: Partial<ItemState> }> = [];

      await Promise.all(
        inProgress.map(async (item) => {
          if (item.server_id == null) return;
          try {
            const status = await api<BackupStatus>(`/backups/${item.server_id}/status`);
            if (cancelled) return;

            if (status.active) {
              const mapped = mapPhaseFromStatus(status);
              updates.push({
                remote_key: item.remote_key,
                patch: {
                  phase: mapped.phase,
                  percent: mapped.percent,
                  bytes_done: mapped.bytes_done,
                  bytes_total: mapped.bytes_total,
                },
              });
            } else {
              // active=false: Server-Status pruefen
              try {
                const server = await api<ServerSummary>(`/servers/${item.server_id}`);
                if (cancelled) return;
                updates.push({
                  remote_key: item.remote_key,
                  patch: {
                    phase: server.status === "error" ? "error" : "done",
                    error_message:
                      server.status === "error"
                        ? server.status_message || t("setup.cloudRestore.statusExtractFailed", "Entpacken fehlgeschlagen")
                        : null,
                  },
                });
              } catch {
                // Server-Endpoint-Fehler: conservative "error" annehmen
                updates.push({
                  remote_key: item.remote_key,
                  patch: {
                    phase: "error",
                    error_message: t("setup.cloudRestore.statusDownloadFailed", "Download fehlgeschlagen"),
                  },
                });
              }
            }
          } catch {
            // Polling-Fehler: Phase bleibt, beim naechsten Tick wieder versuchen
          }
        }),
      );

      if (cancelled || updates.length === 0) return;
      setItemStates((prev) =>
        prev.map((s) => {
          const u = updates.find((x) => x.remote_key === s.remote_key);
          return u ? { ...s, ...u.patch } : s;
        }),
      );
    };

    // Sofort einmal + dann Intervall
    void tick();
    const id = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [itemStates, t]);

  // ── Discard (Liste verwerfen) ─────────────────────────────────────────
  const handleDiscard = useCallback(async () => {
    if (!window.confirm(t("setup.cloudRestore.discardText", "Cloud-Backups verwerfen?"))) return;
    try {
      await api("/setup/pending-restores/discard", { method: "POST" });
      toast.success(t("setup.cloudRestore.verwerfen", "Verworfen"));
      onClose();
    } catch {
      // Toast vom api.ts
    }
  }, [t, onClose]);

  // ── Helpers ───────────────────────────────────────────────────────────
  const pendingItems = useMemo(
    () => itemStates.filter((s) => s.phase === "pending"),
    [itemStates],
  );
  const inProgressItems = useMemo(
    () =>
      itemStates.filter(
        (s) =>
          s.phase === "creating" || s.phase === "downloading" || s.phase === "decrypting" || s.phase === "extracting",
      ),
    [itemStates],
  );
  const doneItems = useMemo(
    () => itemStates.filter((s) => s.phase === "done" || s.phase === "error"),
    [itemStates],
  );

  const allSelected = pendingItems.length > 0 && pendingItems.every((s) => selected.has(s.remote_key));
  const selectedCount = selected.size;

  const toggleAll = () => {
    if (allSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(pendingItems.map((s) => s.remote_key)));
    }
  };

  const toggleOne = (remote_key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(remote_key)) {
        next.delete(remote_key);
      } else {
        next.add(remote_key);
      }
      return next;
    });
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
      onClick={onClose}
      data-testid="cloud-restore-wizard"
    >
      <div
        className="msm-card border border-primary/30 max-w-6xl w-full max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-outline">
          <div>
            <h2 className="font-headline text-headline-sm text-on-surface">
              {t("setup.cloudRestore.wizardTitle", "Cloud-Backups wiederherstellen")}
            </h2>
            <p className="font-body-md text-sm text-on-surface-variant mt-1">
              {t("setup.cloudRestore.wizardSubtitle", "Waehle die Backups aus, die du wiederherstellen moechtest.")}
              {provider && provider !== "unknown" && (
                <span className="ml-2 font-mono-sm text-xs text-on-surface-variant/60">({provider})</span>
              )}
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-on-surface-variant hover:text-on-surface transition-colors p-1"
            aria-label={t("common.close", "Schliessen")}
            data-testid="cloud-restore-wizard-close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Action Bar */}
        <div className="flex flex-wrap items-center gap-2 p-4 border-b border-outline bg-surface-container-high/30">
          <button
            onClick={toggleAll}
            disabled={pendingItems.length === 0}
            className="msm-btn-tertiary inline-flex items-center gap-1.5 px-3 py-1.5 text-sm disabled:opacity-50"
            data-testid="cloud-restore-wizard-toggle-all"
          >
            <Check className="w-3.5 h-3.5" />
            {allSelected
              ? t("setup.cloudRestore.deselectAll", "Auswahl aufheben")
              : t("setup.cloudRestore.selectAll", "Alle auswaehlen")}
          </button>
          <span className="font-mono-sm text-xs text-on-surface-variant ml-2">
            {t("setup.cloudRestore.selected", { selected: selectedCount, total: pendingItems.length, defaultValue: "{{selected}} von {{total}} ausgewaehlt" })}
          </span>

          <div className="flex-1" />

          <button
            onClick={startSelected}
            disabled={selectedCount === 0 || starting}
            className="msm-btn-primary inline-flex items-center gap-1.5 px-4 py-1.5 text-sm disabled:opacity-50"
            data-testid="cloud-restore-wizard-restore"
          >
            {starting ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Download className="w-3.5 h-3.5" />
            )}
            {t("setup.cloudRestore.restoreSelected", "Auswahl wiederherstellen")}
          </button>
          <button
            onClick={onClose}
            className="msm-btn-tertiary inline-flex items-center gap-1.5 px-3 py-1.5 text-sm"
            data-testid="cloud-restore-wizard-spaeter"
          >
            {t("setup.cloudRestore.spaeter", "Spaeter")}
          </button>
          <button
            onClick={handleDiscard}
            className="msm-btn-tertiary inline-flex items-center gap-1.5 px-3 py-1.5 text-sm text-status-destructive/90"
            data-testid="cloud-restore-wizard-discard"
          >
            {t("setup.cloudRestore.verwerfen", "Verwerfen")}
          </button>
        </div>

        {/* 3-Column Body */}
        <div className="flex-1 overflow-y-auto p-4">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* Col 1: Pending */}
            <div data-testid="cloud-restore-wizard-col-pending">
              <ColumnHeader
                icon={<Database className="w-4 h-4" />}
                title={t("setup.cloudRestore.colPending", "Verfuegbar")}
                count={pendingItems.length}
              />
              {pendingItems.length === 0 ? (
                <EmptyColumn text={t("setup.cloudRestore.colPendingEmpty", "Keine ausstehenden Backups")} />
              ) : (
                <div className="space-y-2">
                  {pendingItems.map((item) => (
                    <PendingCard
                      key={item.remote_key}
                      item={item}
                      checked={selected.has(item.remote_key)}
                      onToggle={() => toggleOne(item.remote_key)}
                    />
                  ))}
                </div>
              )}
            </div>

            {/* Col 2: In Progress */}
            <div data-testid="cloud-restore-wizard-col-progress">
              <ColumnHeader
                icon={<Loader2 className="w-4 h-4 animate-spin" />}
                title={t("setup.cloudRestore.colProgress", "Wiederherstellung")}
                count={inProgressItems.length}
              />
              {inProgressItems.length === 0 ? (
                <EmptyColumn text={t("setup.cloudRestore.colProgressEmpty", "Keine laufenden Wiederherstellungen")} />
              ) : (
                <div className="space-y-2">
                  {inProgressItems.map((item) => (
                    <ProgressCard key={item.remote_key} item={item} />
                  ))}
                </div>
              )}
            </div>

            {/* Col 3: Done */}
            <div data-testid="cloud-restore-wizard-col-done">
              <ColumnHeader
                icon={<CheckCircle2 className="w-4 h-4" />}
                title={t("setup.cloudRestore.colDone", "Abgeschlossen")}
                count={doneItems.length}
              />
              {doneItems.length === 0 ? (
                <EmptyColumn text={t("setup.cloudRestore.colDoneEmpty", "Noch nichts abgeschlossen")} />
              ) : (
                <div className="space-y-2">
                  {doneItems.map((item) => (
                    <DoneCard
                      key={item.remote_key}
                      item={item}
                      onNavigate={() => item.server_id != null && navigate(`/servers/${item.server_id}`)}
                    />
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* All-done Message */}
          {pendingItems.length === 0 && inProgressItems.length === 0 && doneItems.length > 0 && (
            <div className="mt-6 text-center">
              <CheckCircle2 className="w-8 h-8 text-status-success mx-auto mb-2" />
              <p className="font-body-md text-sm text-on-surface-variant">
                {t("setup.cloudRestore.allDone", "Alle ausgewaehlten Backups verarbeitet.")}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Sub-Components ─────────────────────────────────────────────────────

function ColumnHeader({ icon, title, count }: { icon: React.ReactNode; title: string; count: number }) {
  return (
    <div className="flex items-center gap-2 mb-3 px-1">
      <span className="text-primary">{icon}</span>
      <h3 className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider">{title}</h3>
      <span className="font-mono-sm text-xs text-on-surface-variant/60">({count})</span>
    </div>
  );
}

function EmptyColumn({ text }: { text: string }) {
  return (
    <div className="msm-card border border-dashed border-outline p-4 text-center">
      <p className="font-body-md text-xs text-on-surface-variant/60">{text}</p>
    </div>
  );
}

function PendingCard({ item, checked, onToggle }: { item: ItemState; checked: boolean; onToggle: () => void }) {
  const { t } = useTranslation();
  return (
    <label
      className="msm-card border border-outline p-3 flex items-start gap-3 cursor-pointer hover:border-primary/30 transition-colors"
      data-testid="cloud-restore-wizard-pending-item"
      data-remote-key={item.remote_key}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={onToggle}
        className="mt-1 w-4 h-4 accent-primary flex-shrink-0"
        data-testid="cloud-restore-wizard-checkbox"
      />
      <div className="flex-1 min-w-0">
        <p className="font-headline text-body-md text-on-surface truncate" title={item.server_name}>
          {item.server_name}
        </p>
        <p className="font-mono-sm text-xs text-on-surface-variant truncate">{item.game_type}</p>
        <p className="font-mono-sm text-xs text-on-surface-variant/70 mt-1">
          {formatDate(item.created_at)} · {formatBytes(item.size_mb != null ? item.size_mb * 1024 * 1024 : null)}
        </p>
      </div>
    </label>
  );
}

function ProgressCard({ item }: { item: ItemState }) {
  const { t } = useTranslation();
  const phaseLabel = t(`setup.cloudRestore.phase.${item.phase}`, item.phase);
  const percent = item.percent != null ? Math.max(0, Math.min(100, item.percent)) : null;
  const isIndeterminate = percent == null;
  return (
    <div
      className="msm-card border border-primary/30 bg-primary/5 p-3"
      data-testid="cloud-restore-wizard-progress-item"
      data-remote-key={item.remote_key}
    >
      <div className="flex items-start gap-3">
        <Loader2 className="w-4 h-4 text-primary flex-shrink-0 mt-1 animate-spin" />
        <div className="flex-1 min-w-0">
          <p className="font-headline text-body-md text-on-surface truncate" title={item.server_name}>
            {item.server_name}
          </p>
          <p className="font-mono-sm text-xs text-primary mt-0.5">{phaseLabel}</p>

          {/* Progress bar (indeterminate wenn keine percent-Daten) */}
          <div
            className="mt-2 h-1.5 bg-surface-container-highest rounded-full overflow-hidden"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent ?? undefined}
          >
            {isIndeterminate ? (
              <div className="h-full bg-primary animate-pulse" style={{ width: "40%" }} />
            ) : (
              <div
                className="h-full bg-primary transition-all duration-300"
                style={{ width: `${percent}%` }}
                data-testid="cloud-restore-wizard-progress-bar"
              />
            )}
          </div>

          {item.bytes_done != null && item.bytes_total != null && (
            <p className="font-mono-sm text-xs text-on-surface-variant mt-1" data-testid="cloud-restore-wizard-progress-counter">
              {formatBytes(item.bytes_done)} / {formatBytes(item.bytes_total)}
              {percent != null && ` (${percent}%)`}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function DoneCard({ item, onNavigate }: { item: ItemState; onNavigate: () => void }) {
  const { t } = useTranslation();
  const isError = item.phase === "error";
  return (
    <div
      className={`msm-card border p-3 ${isError ? "border-status-destructive/30 bg-status-destructive/5" : "border-status-success/30 bg-status-success/5"}`}
      data-testid="cloud-restore-wizard-done-item"
      data-remote-key={item.remote_key}
      data-phase={item.phase}
    >
      <div className="flex items-start gap-3">
        {isError ? (
          <AlertTriangle className="w-4 h-4 text-status-destructive flex-shrink-0 mt-1" />
        ) : (
          <Archive className="w-4 h-4 text-status-success flex-shrink-0 mt-1" />
        )}
        <div className="flex-1 min-w-0">
          <p className="font-headline text-body-md text-on-surface truncate" title={item.server_name}>
            {item.server_name}
          </p>
          {isError ? (
            <p className="font-mono-sm text-xs text-status-destructive/90 mt-1 break-words" data-testid="cloud-restore-wizard-error">
              {item.error_message || t("setup.cloudRestore.statusExtractFailed", "Entpacken fehlgeschlagen")}
            </p>
          ) : (
            <>
              <p className="font-mono-sm text-xs text-status-success mt-0.5">
                {t("setup.cloudRestore.phase.done", "Fertig")}
              </p>
              {item.server_id != null && (
                <button
                  onClick={onNavigate}
                  className="inline-flex items-center gap-1 mt-2 text-xs text-primary hover:underline"
                  data-testid="cloud-restore-wizard-view-server"
                >
                  <ExternalLink className="w-3 h-3" />
                  {t("setup.cloudRestore.viewServer", "Zum Server")}
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
