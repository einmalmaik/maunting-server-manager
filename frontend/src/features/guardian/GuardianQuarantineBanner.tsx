import React, { useState } from "react";
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
  incidents = [],
  onRefresh,
}) => {
  const { t } = useTranslation();
  const [resolving, setResolving] = useState(false);

  const isQuarantined =
    server.guardian_observed_state === "quarantined" ||
    incidents.some((inc) => inc.status === "quarantined");

  if (!server.guardian_enabled || !isQuarantined) {
    return null;
  }

  const openQuarantineIncident = incidents.find(
    (inc) => inc.status === "quarantined" || inc.status === "open"
  );

  const handleResolve = async () => {
    setResolving(true);
    try {
      if (openQuarantineIncident) {
        await api(
          `/servers/${server.id}/incidents/${openQuarantineIncident.id}/resolve`,
          { method: "POST" }
        );
      } else {
        await api(`/servers/${server.id}/status`);
      }
      toast.success(t("servers.guardian.quarantine.clearedSuccess"));
      if (onRefresh) {
        onRefresh();
      }
    } catch {
      toast.error("Quarantäne konnte nicht aufgehoben werden.");
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
            {t("servers.guardian.quarantine.description")}
          </p>
        </div>
      </div>

      <button
        onClick={() => void handleResolve()}
        disabled={resolving}
        className="msm-btn-primary px-4 py-2 text-sm flex-shrink-0 flex items-center gap-2"
      >
        {resolving ? (
          <RefreshCw className="w-4 h-4 animate-spin" />
        ) : (
          <RefreshCw className="w-4 h-4" />
        )}
        {t("servers.guardian.quarantine.clearAction")}
      </button>
    </div>
  );
};
