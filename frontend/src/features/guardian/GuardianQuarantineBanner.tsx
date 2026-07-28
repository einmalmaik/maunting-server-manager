import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Server, GuardianIncident } from "../../types";
import { api } from "@/api/client";
import { toast } from "@/stores/toastStore";

interface GuardianQuarantineBannerProps {
  server: Server;
  incidents?: GuardianIncident[];
  onRefresh?: () => void;
}

export const GuardianQuarantineBanner: React.FC<GuardianQuarantineBannerProps> = ({
  server,
  incidents,
  onRefresh,
}) => {
  const { t } = useTranslation();
  const [resolving, setResolving] = useState(false);
  const [clearRequested, setClearRequested] = useState(false);
  const [fetchedIncidents, setFetchedIncidents] = useState<GuardianIncident[]>([]);
  const clearPending = Boolean(server.guardian_quarantine_clear_pending || clearRequested);

  useEffect(() => {
    setClearRequested(false);
  }, [server]);

  useEffect(() => {
    if (
      incidents !== undefined ||
      (server.guardian_observed_state !== "quarantined" &&
        !server.guardian_quarantine_clear_pending)
    ) return;
    let active = true;
    api<GuardianIncident[]>(`/servers/${server.id}/incidents`)
      .then(value => {
        if (active && Array.isArray(value)) setFetchedIncidents(value);
      })
      .catch(() => undefined);
    return () => { active = false; };
  }, [incidents, server.guardian_observed_state, server.id]);

  const safeIncidents = Array.isArray(incidents) ? incidents : fetchedIncidents;

  const isQuarantined =
    server?.guardian_observed_state === "quarantined" ||
    Boolean(server?.guardian_quarantine_clear_pending) ||
    safeIncidents.some((inc) => inc?.status === "quarantined");

  if (!server?.guardian_enabled || !isQuarantined) {
    return null;
  }

  const openQuarantineIncident = safeIncidents.find((inc) => inc?.status === "quarantined");

  const handleResolve = async () => {
    setResolving(true);
    try {
      if (!openQuarantineIncident) return;
      await api(
        `/servers/${server.id}/incidents/${openQuarantineIncident.id}/resolve`,
        { method: "POST" }
      );
      setClearRequested(true);
      toast.success(t("servers.guardian.quarantine.requestedSuccess"));
      if (onRefresh) {
        onRefresh();
      }
    } catch {
      toast.error(t("servers.guardian.quarantine.requestFailed"));
    } finally {
      setResolving(false);
    }
  };

  return (
    <div className="msm-card p-4 border-status-error/40 bg-status-error/5 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
      <div className="flex items-start gap-3">
        <AlertTriangle className="w-5 h-5 text-status-error flex-shrink-0 mt-0.5" />
        <div>
          <p className="font-headline text-body-md text-on-surface font-semibold mb-1">
            {t("servers.guardian.quarantine.title")}
          </p>
          <p className="font-body-md text-sm text-on-surface-variant">
            {t(clearPending ? "servers.guardian.quarantine.pendingDescription" : "servers.guardian.quarantine.description")}
          </p>
          {!clearPending && !openQuarantineIncident && <p className="mt-2 text-xs text-status-warning">{t("servers.guardian.quarantine.noIncident")}</p>}
        </div>
      </div>

      <button
        onClick={() => void handleResolve()}
        disabled={resolving || clearPending || !openQuarantineIncident}
        className="msm-btn-primary px-4 py-2 text-sm flex-shrink-0 flex items-center gap-2"
      >
        {resolving ? (
          <RefreshCw className="w-4 h-4 animate-spin" />
        ) : (
          <RefreshCw className="w-4 h-4" />
        )}
        {t(clearPending ? "servers.guardian.quarantine.pendingAction" : "servers.guardian.quarantine.clearAction")}
      </button>
    </div>
  );
};
