import React, { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import {
  ShieldCheck,
  RefreshCw,
  AlertTriangle,
  Bot,
  CheckCircle2,
  Clock,
  Activity,
} from "lucide-react";
import { Link } from "react-router-dom";
import { Server, GuardianIncident } from "../../types";
import { api } from "@/api/client";
import { toast } from "@/stores/toastStore";
import { getGuardianDisplayState } from "./GuardianBadge";

/** Zustaende, in denen ein Heilungslauf noch arbeitet (inkl. Warten auf einen Menschen). */
const LAUF_OFFEN = ["running", "waiting_confirmation", "waiting_user"];

/**
 * Welcher Satz unter einem Vorfall steht, wenn die KI etwas veranlasst hat.
 *
 * "Behoben" verlangt **beides**: der Lauf ist sauber zu Ende gegangen *und* der
 * Vorfall steht auf `resolved`. Dieselbe Und-Verknuepfung entscheidet im Backend
 * ueber den Text der Ergebnis-Mail (`ai_guardian_report.bericht_versenden`) — es
 * waere die schlechteste Art von Fehler, wenn Mail und Panel demselben Vorfall
 * zwei verschiedene Ausgaenge bescheinigten.
 */
function aiSchluessel(inc: GuardianIncident): string | null {
  const ai = inc.ai;
  if (!ai) return null;
  if (ai.mode === "briefed") return "servers.guardian.tab.aiBriefed";
  if (ai.run_status === null) return "servers.guardian.tab.aiUnknown";
  if (LAUF_OFFEN.includes(ai.run_status)) return "servers.guardian.tab.aiHealing";
  if (ai.run_status === "completed" && inc.status === "resolved") {
    return "servers.guardian.tab.aiHealed";
  }
  return "servers.guardian.tab.aiFailed";
}

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
  const [incidentError, setIncidentError] = useState(false);
  const [resolvingId, setResolvingId] = useState<number | null>(null);

  const fetchIncidents = useCallback(async () => {
    setLoading(true);
    setIncidentError(false);
    try {
      const res = await api<GuardianIncident[]>(
        `/servers/${server.id}/incidents`
      );
      if (!Array.isArray(res)) throw new Error("INVALID_INCIDENT_RESPONSE");
      setIncidents(res);
    } catch {
      setIncidentError(true);
    } finally {
      setLoading(false);
    }
  }, [server.id]);

  useEffect(() => {
    void fetchIncidents();
  }, [fetchIncidents]);

  const handleResolveIncident = async (incident: GuardianIncident) => {
    setResolvingId(incident.id);
    try {
      await api(`/servers/${server.id}/incidents/${incident.id}/resolve`, {
        method: "POST",
      });
      toast.success(t(incident.status === "quarantined" ? "servers.guardian.quarantine.requestedSuccess" : "servers.guardian.tab.resolvedSuccess"));
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

  const observedState = getGuardianDisplayState(server);
  const quarantineClearPending = Boolean(server.guardian_quarantine_clear_pending);

  const getStatusBadge = (status: string, pending = false) => {
    if (pending) {
      return (
        <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-mono-sm border border-status-warning/30 bg-status-warning/10 text-status-warning">
          <Clock className="w-3 h-3" />
          {t("servers.guardian.tab.status.releasePending")}
        </span>
      );
    }
    switch (status) {
      case "healthy":
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-mono-sm border border-status-success/30 bg-status-success/10 text-status-success">
            <ShieldCheck className="w-3 h-3" />
            {t("servers.guardian.tab.status.healthy")}
          </span>
        );
      case "activity":
      case "recovering":
      case "open":
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-mono-sm border border-status-warning/30 bg-status-warning/10 text-status-warning">
            <RefreshCw className="w-3 h-3 animate-spin" />
            {t(`servers.guardian.tab.status.${status}`, { defaultValue: status })}
          </span>
        );
      case "warning":
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-mono-sm border border-status-warning/30 bg-status-warning/10 text-status-warning">
            <AlertTriangle className="w-3 h-3" />
            {t("servers.guardian.tab.status.warning")}
          </span>
        );
      case "quarantined":
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-mono-sm border border-status-error/30 bg-status-error/10 text-status-error">
            <AlertTriangle className="w-3 h-3" />
            {t("servers.guardian.tab.status.quarantined")}
          </span>
        );
      case "resolved":
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-mono-sm border border-status-success/30 bg-status-success/10 text-status-success">
            <CheckCircle2 className="w-3 h-3" />
            {t("servers.guardian.tab.status.resolved")}
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-mono-sm border border-outline-variant bg-surface-container-low text-on-surface-variant">
            <AlertTriangle className="w-3 h-3" />
            {t(`servers.guardian.tab.status.${status}`, { defaultValue: t("servers.guardian.tab.status.unknown") })}
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
              {server.guardian_probe_timestamp || server.guardian_transition_timestamp
                ? new Date(
                    (server.guardian_probe_timestamp || server.guardian_transition_timestamp)!
                  ).toLocaleString()
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

        {(() => {
          const safeIncidents = Array.isArray(incidents) ? incidents : [];
          if (incidentError) {
            return (
              <div className="msm-alert-warning" role="alert">
                <p className="font-semibold">{t("servers.guardian.tab.historyErrorTitle")}</p>
                <p className="mt-1 text-sm">{t("servers.guardian.tab.historyErrorBody")}</p>
                <button type="button" className="msm-btn-secondary mt-3 px-3 py-1.5 text-xs" onClick={() => void fetchIncidents()}>
                  {t("common.retry")}
                </button>
              </div>
            );
          }
          if (loading) {
            return <p className="py-8 text-center text-sm text-on-surface-variant" role="status">{t("common.loading")}</p>;
          }
          const pendingNotice = quarantineClearPending ? (
            <div className="msm-alert-warning mb-4" role="status">
              {t("servers.guardian.tab.quarantinePendingNote")}
            </div>
          ) : null;
          if (safeIncidents.length === 0) {
            return (
              <>
                {pendingNotice}
                <div className="py-8 text-center border border-dashed border-outline-variant/60 rounded-lg bg-surface-container-lowest">
                  <ShieldCheck className="w-8 h-8 text-status-success mx-auto mb-2 opacity-80" />
                  <p className="text-sm text-on-surface-variant max-w-md mx-auto">
                    {t("servers.guardian.tab.noIncidents")}
                  </p>
                </div>
              </>
            );
          }
          return (
            <>
              {pendingNotice}
              <div className="space-y-4">
                {safeIncidents.map((inc) => {
                const safeAttempts = Array.isArray(inc?.attempts) ? inc.attempts : [];
                return (
                  <div
                    key={inc.id}
                    className="p-4 rounded-lg border border-outline-variant/40 bg-surface-container-lowest hover:border-outline-variant transition-colors"
                  >
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-2">
                      <div className="flex items-center gap-2">
                        <span className="font-headline font-semibold text-sm text-on-surface">
                          {inc.title}
                        </span>
                        {getStatusBadge(
                          inc.status,
                          quarantineClearPending && inc.status === "resolved",
                        )}
                      </div>
                      <span className="text-xs text-on-surface-variant font-mono-sm">
                        {new Date(inc.created_at).toLocaleString()}
                      </span>
                    </div>

                    <p className="text-xs text-on-surface-variant font-mono-sm bg-surface-container-low p-2 rounded border border-outline-variant/30 mb-3 whitespace-pre-wrap">
                      {inc.description}
                    </p>

                    {safeAttempts.length > 0 && (
                      <div className="mb-3 text-xs text-on-surface-variant">
                        <span className="font-medium text-on-surface">
                          {t("servers.guardian.tab.attempts")}:{" "}
                        </span>
                        {safeAttempts.map((att, idx) => (
                          <span
                            key={idx}
                            className="inline-block mr-2 px-2 py-0.5 rounded bg-surface-container border border-outline-variant/30 font-mono-sm"
                          >
                            #{att.attempt} {att.action} ({att.result})
                          </span>
                        ))}
                      </div>
                    )}

                    {(() => {
                      const schluessel = aiSchluessel(inc);
                      if (!schluessel) return null;
                      return (
                        <div className="mb-3 flex flex-wrap items-center gap-2 rounded border border-outline-variant/30 bg-surface-container-low p-2 text-xs text-on-surface-variant">
                          <Bot className="w-3.5 h-3.5 shrink-0 text-primary" aria-hidden="true" />
                          <span className="font-medium text-on-surface">
                            {t("servers.guardian.tab.aiHeading")}:
                          </span>
                          <span>{t(schluessel)}</span>
                          {/* Nur der eigene Lauf ist zu oeffnen: es gibt eine
                              Unterhaltung je Benutzer, und der Chat eines
                              anderen Freigebers laesst sich nicht anzeigen. */}
                          {inc.ai?.mine && inc.ai.mode === "healing" && (
                            <Link to="/ai" className="text-primary underline underline-offset-2">
                              {t("servers.guardian.tab.aiOpenChat")}
                            </Link>
                          )}
                        </div>
                      );
                    })()}

                    {inc.status !== "resolved" && (
                      <div className="flex justify-end pt-2 border-t border-outline-variant/20">
                        <button
                          onClick={() => void handleResolveIncident(inc)}
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
                );
                })}
              </div>
            </>
          );
        })()}
      </div>
    </div>
  );
};
