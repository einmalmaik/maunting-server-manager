import React, { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import {
  ShieldCheck,
  RefreshCw,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Activity,
} from "lucide-react";
import { Server, GuardianIncident } from "../../types";
import { api } from "@/api/client";
import { toast } from "@/stores/toastStore";

interface GuardianTabProps {
  server: Server;
  onRefreshServer?: () => void;
}

export const GuardianTab: React.FC<GuardianTabProps> = ({
  server,
  onRefreshServer,
}) => {
  const { t } = useTranslation();
  const [incidents, setIncidents] = useState<GuardianIncident[]>([]);
  const [loading, setLoading] = useState(true);
  const [resolvingId, setResolvingId] = useState<number | null>(null);

  const fetchIncidents = useCallback(async () => {
    try {
      const res = await api<GuardianIncident[]>(
        `/servers/${server.id}/incidents`
      );
      setIncidents(res || []);
    } catch {
      // Graceful fallback if no incidents router or network error
      setIncidents([]);
    } finally {
      setLoading(false);
    }
  }, [server.id]);

  useEffect(() => {
    void fetchIncidents();
  }, [fetchIncidents]);

  const handleResolveIncident = async (incId: number) => {
    setResolvingId(incId);
    try {
      await api(`/servers/${server.id}/incidents/${incId}/resolve`, {
        method: "POST",
      });
      toast.success(t("servers.guardian.tab.resolvedSuccess"));
      await fetchIncidents();
      if (onRefreshServer) {
        onRefreshServer();
      }
    } catch {
      toast.error("Incident konnte nicht gelöst werden.");
    } finally {
      setResolvingId(null);
    }
  };

  if (!server.guardian_enabled) {
    return null;
  }

  const observedState = server.guardian_observed_state || "healthy";

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "quarantined":
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-mono-sm border border-status-error/30 bg-status-error/10 text-status-error">
            <AlertTriangle className="w-3 h-3" />
            {t("servers.guardian.tab.status.quarantined")}
          </span>
        );
      case "recovering":
      case "open":
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-mono-sm border border-status-warning/30 bg-status-warning/10 text-status-warning">
            <RefreshCw className="w-3 h-3 animate-spin" />
            {t(`servers.guardian.tab.status.${status}`, { defaultValue: status })}
          </span>
        );
      case "resolved":
      default:
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-mono-sm border border-status-success/30 bg-status-success/10 text-status-success">
            <CheckCircle2 className="w-3 h-3" />
            {t("servers.guardian.tab.status.resolved")}
          </span>
        );
    }
  };

  return (
    <div className="space-y-6">
      {/* Overview Card */}
      <div className="msm-card p-6">
        <h3 className="text-lg font-headline font-semibold text-on-surface mb-4 flex items-center gap-2">
          <Activity className="w-5 h-5 text-primary" />
          {t("servers.guardian.tab.overviewTitle")}
        </h3>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="p-4 rounded-lg bg-surface-container-low border border-outline-variant/40">
            <p className="text-xs text-on-surface-variant font-medium mb-1">
              {t("servers.guardian.tab.observedState")}
            </p>
            <div className="mt-1">{getStatusBadge(observedState)}</div>
          </div>

          <div className="p-4 rounded-lg bg-surface-container-low border border-outline-variant/40">
            <p className="text-xs text-on-surface-variant font-medium mb-1">
              {t("servers.guardian.tab.containerStatus")}
            </p>
            <p className="text-sm font-mono-sm text-on-surface font-semibold capitalize">
              {server.status || "Unknown"}
            </p>
          </div>

          <div className="p-4 rounded-lg bg-surface-container-low border border-outline-variant/40">
            <p className="text-xs text-on-surface-variant font-medium mb-1">
              {t("servers.guardian.tab.lastProbe")}
            </p>
            <p className="text-sm font-mono-sm text-on-surface font-semibold">
              {server.created_at
                ? new Date(server.created_at).toLocaleString()
                : t("servers.guardian.tab.noProbe")}
            </p>
          </div>
        </div>
      </div>

      {/* Incidents & History Card */}
      <div className="msm-card p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-headline font-semibold text-on-surface flex items-center gap-2">
            <Clock className="w-5 h-5 text-primary" />
            {t("servers.guardian.tab.historyTitle")}
          </h3>
          <button
            onClick={() => void fetchIncidents()}
            className="msm-btn-secondary px-3 py-1.5 text-xs flex items-center gap-1.5"
            title="Aktualisieren"
          >
            <RefreshCw
              className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`}
            />
            {t("common.refresh", { defaultValue: "Aktualisieren" })}
          </button>
        </div>

        {incidents.length === 0 ? (
          <div className="py-8 text-center border border-dashed border-outline-variant/60 rounded-lg bg-surface-container-lowest">
            <ShieldCheck className="w-8 h-8 text-status-success mx-auto mb-2 opacity-80" />
            <p className="text-sm text-on-surface-variant max-w-md mx-auto">
              {t("servers.guardian.tab.noIncidents")}
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {incidents.map((inc) => (
              <div
                key={inc.id}
                className="p-4 rounded-lg border border-outline-variant/40 bg-surface-container-lowest hover:border-outline-variant transition-colors"
              >
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-2">
                  <div className="flex items-center gap-2">
                    <span className="font-headline font-semibold text-sm text-on-surface">
                      {inc.title}
                    </span>
                    {getStatusBadge(inc.status)}
                  </div>
                  <span className="text-xs text-on-surface-variant font-mono-sm">
                    {new Date(inc.created_at).toLocaleString()}
                  </span>
                </div>

                <p className="text-xs text-on-surface-variant font-mono-sm bg-surface-container-low p-2 rounded border border-outline-variant/30 mb-3 whitespace-pre-wrap">
                  {inc.description}
                </p>

                {inc.attempts && inc.attempts.length > 0 && (
                  <div className="mb-3 text-xs text-on-surface-variant">
                    <span className="font-medium text-on-surface">
                      {t("servers.guardian.tab.attempts")}:{" "}
                    </span>
                    {inc.attempts.map((att, idx) => (
                      <span
                        key={idx}
                        className="inline-block mr-2 px-2 py-0.5 rounded bg-surface-container border border-outline-variant/30 font-mono-sm"
                      >
                        #{att.attempt} {att.action} ({att.result})
                      </span>
                    ))}
                  </div>
                )}

                {inc.status !== "resolved" && (
                  <div className="flex justify-end pt-2 border-t border-outline-variant/20">
                    <button
                      onClick={() => void handleResolveIncident(inc.id)}
                      disabled={resolvingId === inc.id}
                      className="msm-btn-primary px-3 py-1.5 text-xs flex items-center gap-1.5"
                    >
                      {resolvingId === inc.id ? (
                        <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <CheckCircle2 className="w-3.5 h-3.5" />
                      )}
                      {t("servers.guardian.tab.resolveAction")}
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
