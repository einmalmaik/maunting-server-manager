import React from "react";
import { useTranslation } from "react-i18next";
import { ShieldCheck, RefreshCw, AlertTriangle, CircleHelp, WifiOff } from "lucide-react";
import { Server } from "../../types";

interface GuardianBadgeProps {
  server: Server;
}

type GuardianDisplayState =
  | "healthy"
  | "activity"
  | "warning"
  | "quarantined"
  | "offline"
  | "error"
  | "stopped"
  | "unknown";

export function getGuardianDisplayState(server: Server): GuardianDisplayState {
  if (server.status === "stopped") return "stopped";
  if (server.guardian_sync_error_statistics) return "error";
  if (["offline", "unavailable", "error"].includes(server.status)) return "offline";
  switch (server.guardian_observed_state) {
    case "healthy":
      return "healthy";
    case "starting":
    case "recovering":
    case "verifying":
      return "activity";
    case "degraded":
    case "unhealthy":
      return "warning";
    case "quarantined":
      return "quarantined";
    case "stopped":
      return "stopped";
    default:
      return "unknown";
  }
}

export const GuardianBadge: React.FC<GuardianBadgeProps> = ({ server }) => {
  const { t } = useTranslation();

  if (!server.guardian_enabled) {
    return null;
  }

  const observedState = getGuardianDisplayState(server);

  if (observedState === "quarantined") {
    return (
      <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full border border-status-error/30 bg-status-error/10 text-status-error font-mono-sm text-xs font-medium">
        <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
        {t("servers.guardian.badge.quarantined")}
      </span>
    );
  }

  if (observedState === "activity" || observedState === "warning") {
    return (
      <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full border border-status-warning/30 bg-status-warning/10 text-status-warning font-mono-sm text-xs font-medium">
        {observedState === "activity" ? (
          <RefreshCw className="w-3.5 h-3.5 flex-shrink-0 animate-spin" />
        ) : (
          <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
        )}
        {t(`servers.guardian.badge.${observedState}`)}
      </span>
    );
  }

  if (observedState !== "healthy") {
    const Icon = observedState === "offline" ? WifiOff : observedState === "error" ? AlertTriangle : CircleHelp;
    return (
      <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full border font-mono-sm text-xs font-medium ${observedState === "error" ? "border-status-error/30 bg-status-error/10 text-status-error" : "border-outline-variant bg-surface-container-low text-on-surface-variant"}`}>
        <Icon className="w-3.5 h-3.5 flex-shrink-0" />
        {t(`servers.guardian.badge.${observedState}`)}
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
