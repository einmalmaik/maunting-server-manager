import { useEffect, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { Cloud, X, ChevronRight } from "lucide-react";
import { api } from "@/api/client";
import { CloudRestoreWizard, type PendingRestoreItem } from "./CloudRestoreWizard";

interface PendingRestoresResponse {
  pending: boolean;
  items: PendingRestoreItem[];
  error: string | null;
  provider: string;
}

const POLL_INTERVAL_MS = 30_000;

/**
 * CloudRestoreBanner — Dashboard-Banner fuer orphan Cloud-Backups.
 *
 * Plan 3.7 Punkt 4: Erscheint auf dem Dashboard, solange
 * `pending-restores` nicht leer ist. Klick oeffnet den CloudRestoreWizard.
 *
 * Verhalten:
 * - Pollt `/api/setup/pending-restores` alle 30s
 * - Verschwindet, wenn `pending === false` ODER nach erfolgreichem Discard
 * - Fehlertext vom Backend (sanitized) wird dezent angezeigt, blockiert nicht
 *
 * Security:
 * - Liest NUR Metadaten, kein Download
 * - Discard setzt `MSM_PENDING_CLOUD_RESTORE=0` in .env (kein Cloud-Delete)
 */
export function CloudRestoreBanner() {
  const { t } = useTranslation();
  const [data, setData] = useState<PendingRestoresResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [dismissed, setDismissed] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);

  const fetchPending = useCallback(async () => {
    try {
      const res = await api<PendingRestoresResponse>("/setup/pending-restores");
      setData(res);
    } catch {
      // Stilles Failen — Banner ist non-blocking, User bekommt spaeter Retry
      setData((prev) => prev ?? { pending: false, items: [], error: null, provider: "unknown" });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPending();
    const id = setInterval(fetchPending, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [fetchPending]);

  // Wenn pending-Flag wegfaellt, sicherheitshalber dismiss zuruecksetzen
  // (z.B. wenn discard von woanders aufgerufen wurde)
  useEffect(() => {
    if (data && !data.pending) {
      setDismissed(false);
    }
  }, [data?.pending]);

  const handleDiscard = useCallback(async () => {
    if (!window.confirm(t("setup.cloudRestore.discardText"))) return;
    try {
      await api("/setup/pending-restores/discard", { method: "POST" });
      setDismissed(true);
      // Banner verschwindet, weil Server jetzt pending=false liefert
      setData((prev) => (prev ? { ...prev, pending: false, items: [] } : prev));
    } catch {
      // Fehler werden im globalen Toast gezeigt (api.ts), Banner bleibt
    }
  }, [t]);

  if (loading || !data) return null;
  if (!data.pending || data.items.length === 0) return null;
  if (dismissed) return null;

  const count = data.items.length;
  const bannerText =
    count === 1
      ? t("setup.cloudRestore.bannerTextSingular", { defaultValue: "1 Backup in der Cloud gefunden. Klicken zum Wiederherstellen." })
      : t("setup.cloudRestore.bannerText", { count, defaultValue: "{{count}} Backups in der Cloud gefunden. Klicken zum Wiederherstellen." });

  return (
    <>
      <div
        data-testid="cloud-restore-banner"
        className="msm-card border border-primary/30 bg-primary/5 p-4 mb-6"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3 flex-1">
            <Cloud className="w-5 h-5 text-primary flex-shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <h3 className="font-headline text-body-md text-on-surface">
                {t("setup.cloudRestore.bannerTitle", "Cloud-Backups gefunden")}
              </h3>
              <p className="font-body-md text-sm text-on-surface-variant mt-1">
                {bannerText}
              </p>
              {data.error && (
                <p
                  data-testid="cloud-restore-banner-error"
                  className="font-body-md text-xs text-status-warning/90 mt-2"
                  title={data.error}
                >
                  {t("setup.cloudRestore.errorPrefix", "Cloud-Provider meldet:")} {data.error}
                </p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <button
              onClick={handleDiscard}
              className="msm-btn-tertiary inline-flex items-center gap-1 px-3 py-1.5 text-sm"
              data-testid="cloud-restore-banner-discard"
            >
              {t("setup.cloudRestore.verwerfen", "Verwerfen")}
            </button>
            <button
              onClick={() => setWizardOpen(true)}
              className="msm-btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-sm"
              data-testid="cloud-restore-banner-open"
            >
              {t("setup.cloudRestore.bannerOpen", "Oeffnen")}
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => setDismissed(true)}
              className="text-on-surface-variant hover:text-on-surface transition-colors p-1"
              aria-label={t("common.close", "Schliessen")}
              data-testid="cloud-restore-banner-dismiss"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      {wizardOpen && (
        <CloudRestoreWizard
          items={data.items}
          provider={data.provider}
          onClose={() => {
            setWizardOpen(false);
            // Liste nach Wizard-Close frisch holen (ggf. reduziert durch Discards)
            fetchPending();
          }}
        />
      )}
    </>
  );
}
