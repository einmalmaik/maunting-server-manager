import React from "react";
import { useTranslation } from "react-i18next";
import { ShieldCheck, RefreshCw, AlertTriangle } from "lucide-react";
import { Server } from "../../types";

interface GuardianBadgeProps {
  server: Server;
}

export const GuardianBadge: React.FC<GuardianBadgeProps> = ({ server }) => {
  const { t } = useTranslation();

  if (!server.guardian_enabled) {
    return null;
  }

  const observedState = server.guardian_observed_state || "healthy";

  if (observedState === "quarantined") {
    return (
      <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full border border-status-error/30 bg-status-error/10 text-status-error font-mono-sm text-xs font-medium">
        <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
        {t("servers.guardian.badge.quarantined")}
      </span>
    );
  }

  if (observedState === "recovering" || observedState === "unhealthy") {
    return (
      <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full border border-status-warning/30 bg-status-warning/10 text-status-warning font-mono-sm text-xs font-medium">
        <RefreshCw className="w-3.5 h-3.5 flex-shrink-0 animate-spin" />
        {t("servers.guardian.badge.recovering")}
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full border border-status-success/30 bg-status-success/10 text-status-success font-mono-sm text-xs font-medium">
      <ShieldCheck className="w-3.5 h-3.5 flex-shrink-0" />
      {t("servers.guardian.badge.active")}
    </span>
  );
};
