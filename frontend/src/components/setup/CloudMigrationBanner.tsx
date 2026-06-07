import { useEffect, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { CloudUpload, X, Loader2, CheckCircle2, AlertTriangle } from "lucide-react";
import { api } from "@/api/client";
import { toast } from "@/stores/toastStore";

interface MigrationProgress {
  status: "idle" | "running" | "completed" | "failed" | "cancelled";
  total: number;
  migrated: number;
  failed: number;
  current_server_id: number | null;
  current_filename: string | null;
  started_at: string | null;
  finished_at: string | null;
  last_error: string | null;
  target_provider: string;
}

interface SimpleOkResponse {
  ok: boolean;
  message: string;
}

const POLL_INTERVAL_MS = 2_000;

/**
 * CloudMigrationBanner — Dashboard-Banner fuer laufende Auto-Migration.
 *
 * Plan 3.10: Erscheint parallel zum CloudRestoreBanner, solange eine
 * Auto-Migration laeuft (status=running) oder fehlgeschlagen ist (failed).
 *
 * Verhalten:
 * - Pollt `/api/setup/migration-status` alle 2s
 * - running: zeigt Progress (migrated/total) + aktueller Filename + Cancel
 * - completed: gruener Status, 5s sichtbar, dann ausgeblendet
 * - failed: roter Status + letzter Fehler, bleibt sichtbar (User soll .env fixen)
 * - idle/cancelled: nicht sichtbar
 *
 * Cancel ist idempotent. Klick ruft POST /api/setup/migration-cancel,
 * der Server setzt ein internes Flag, der aktuelle Upload laeuft noch zu Ende.
 */
export function CloudMigrationBanner() {
  const { t } = useTranslation();
  const [progress, setProgress] = useState<MigrationProgress | null>(null);
  const [loading, setLoading] = useState(true);
  const [cancelling, setCancelling] = useState(false);
  const [completedDismissed, setCompletedDismissed] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await api<MigrationProgress>("/setup/migration-status");
      setProgress(res);
    } catch {
      setProgress((prev) =>
        prev ?? {
          status: "idle",
          total: 0,
          migrated: 0,
          failed: 0,
          current_server_id: null,
          current_filename: null,
          started_at: null,
          finished_at: null,
          last_error: null,
          target_provider: "unknown",
        },
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const id = setInterval(fetchStatus, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [fetchStatus]);

  // Nach completed 5s sichtbar lassen, dann auto-dismiss
  useEffect(() => {
    if (progress?.status === "completed" && !completedDismissed) {
      const id = setTimeout(() => setCompletedDismissed(true), 5_000);
      return () => clearTimeout(id);
    }
  }, [progress?.status, completedDismissed]);

  // Reset dismiss-Flag wenn neue Migration startet
  useEffect(() => {
    if (progress?.status === "running") {
      setCompletedDismissed(false);
    }
  }, [progress?.status]);

  const handleCancel = useCallback(async () => {
    if (!window.confirm(t("setup.cloudMigration.cancelConfirm", "Migration wirklich abbrechen?"))) return;
    setCancelling(true);
    try {
      await api<SimpleOkResponse>("/setup/migration-cancel", { method: "POST" });
      toast.success(t("setup.cloudMigration.cancelSent", "Cancel-Signal gesendet."));
      // Status wird im naechsten Polling-Tick aktualisiert
    } catch {
      // Toast kommt vom api.ts
    } finally {
      setCancelling(false);
    }
  }, [t]);

  if (loading || !progress) return null;

  const status = progress.status;
  if (status === "idle" || status === "cancelled") return null;
  if (status === "completed" && completedDismissed) return null;

  const percent =
    progress.total > 0
      ? Math.max(0, Math.min(100, Math.round((progress.migrated / progress.total) * 100)))
      : 0;

  // Farb- + Icon-Auswahl je nach Status
  const visual =
    status === "running"
      ? {
          borderClass: "border-primary/30",
          bgClass: "bg-primary/5",
          Icon: CloudUpload,
          iconClass: "text-primary",
          titleKey: "setup.cloudMigration.bannerTitle",
          titleDefault: "Auto-Migration laeuft",
        }
      : status === "completed"
        ? {
            borderClass: "border-status-success/30",
            bgClass: "bg-status-success/5",
            Icon: CheckCircle2,
            iconClass: "text-status-success",
            titleKey: "setup.cloudMigration.completed",
            titleDefault: "Migration abgeschlossen",
          }
        : {
            // failed
            borderClass: "border-status-destructive/30",
            bgClass: "bg-status-destructive/5",
            Icon: AlertTriangle,
            iconClass: "text-status-destructive",
            titleKey: "setup.cloudMigration.failed",
            titleDefault: "Migration fehlgeschlagen",
          };

  const showProgress = status === "running" && progress.total > 0;

  return (
    <div
      data-testid="cloud-migration-banner"
      data-status={status}
      className={`msm-card border ${visual.borderClass} ${visual.bgClass} p-4 mb-6`}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3 flex-1 min-w-0">
          {status === "running" ? (
            <Loader2 className={`w-5 h-5 ${visual.iconClass} flex-shrink-0 mt-0.5 animate-spin`} />
          ) : (
            <visual.Icon className={`w-5 h-5 ${visual.iconClass} flex-shrink-0 mt-0.5`} />
          )}
          <div className="flex-1 min-w-0">
            <h3 className="font-headline text-body-md text-on-surface">
              {t(visual.titleKey, visual.titleDefault)}
            </h3>

            {status === "running" && (
              <p className="font-body-md text-sm text-on-surface-variant mt-1">
                {t("setup.cloudMigration.bannerText", {
                  migrated: progress.migrated,
                  total: progress.total,
                  defaultValue: "{{migrated}} von {{total}} lokalen Backups werden in die Cloud migriert...",
                })}
              </p>
            )}

            {status === "completed" && (
              <p className="font-body-md text-sm text-on-surface-variant mt-1">
                {t("setup.cloudMigration.completedText", {
                  migrated: progress.migrated,
                  total: progress.total,
                  defaultValue: "{{migrated}} von {{total}} Backups erfolgreich in die Cloud migriert.",
                })}
              </p>
            )}

            {status === "failed" && progress.last_error && (
              <p
                data-testid="cloud-migration-banner-error"
                className="font-body-md text-sm text-status-destructive/90 mt-1 break-words"
              >
                {t("setup.cloudMigration.errorPrefix", "Fehler:")} {progress.last_error}
              </p>
            )}

            {status === "failed" && (
              <p className="font-body-md text-xs text-on-surface-variant mt-2">
                {t("setup.cloudMigration.retryHint", "Cloud-Credentials in .env pruefen, dann erscheint der Banner beim naechsten Start erneut.")}
              </p>
            )}

            {/* Live-Progress-Bar (running) */}
            {showProgress && (
              <div className="mt-3" data-testid="cloud-migration-progress">
                <div
                  className="h-1.5 bg-surface-container-highest rounded-full overflow-hidden"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={percent}
                >
                  <div
                    className="h-full bg-primary transition-all duration-300"
                    style={{ width: `${percent}%` }}
                    data-testid="cloud-migration-progress-bar"
                  />
                </div>
                <div className="flex items-center justify-between mt-1.5">
                  <p className="font-mono-sm text-xs text-on-surface-variant">
                    {progress.migrated} / {progress.total} ({percent}%)
                  </p>
                  {progress.current_filename && (
                    <p
                      className="font-mono-sm text-xs text-on-surface-variant truncate ml-2 max-w-[60%]"
                      title={progress.current_filename}
                    >
                      {t("setup.cloudMigration.currentFile", "Aktuell:")} {progress.current_filename}
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Cancel nur bei running */}
        {status === "running" && (
          <div className="flex items-center gap-2 flex-shrink-0">
            <button
              onClick={handleCancel}
              disabled={cancelling}
              className="msm-btn-tertiary inline-flex items-center gap-1 px-3 py-1.5 text-sm disabled:opacity-50"
              data-testid="cloud-migration-banner-cancel"
            >
              {cancelling
                ? t("setup.cloudMigration.cancelling", "Abbruch wird vorbereitet...")
                : t("setup.cloudMigration.cancel", "Abbrechen")}
            </button>
          </div>
        )}

        {status === "completed" && (
          <button
            onClick={() => setCompletedDismissed(true)}
            className="text-on-surface-variant hover:text-on-surface transition-colors p-1 flex-shrink-0"
            aria-label={t("common.close", "Schliessen")}
            data-testid="cloud-migration-banner-dismiss"
          >
            <X className="w-4 h-4" />
          </button>
        )}

        {status === "failed" && (
          <button
            onClick={() => setCompletedDismissed(true)}
            className="text-on-surface-variant hover:text-on-surface transition-colors p-1 flex-shrink-0"
            aria-label={t("common.close", "Schliessen")}
            data-testid="cloud-migration-banner-dismiss"
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>
    </div>
  );
}
