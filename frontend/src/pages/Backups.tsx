import { useEffect, useState, useRef } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/api/client";
import { toast } from "@/stores/toastStore";
import { confirm } from "@/stores/confirmStore";
import { HardDrive, Plus, RotateCcw, Trash2, Settings } from "lucide-react";

interface Backup {
  id: number;
  server_id: number;
  name: string | null;
  filename: string;
  size_mb: number | null;
  created_at: string;
  expires_at: string | null;
}

interface BackupSettings {
  backup_on_start: boolean;
  backup_interval_hours: number | null;
  backup_retention_count: number;
}

const INTERVAL_OPTIONS = [
  { value: 0, label: "Deaktiviert" },
  { value: 1, label: "Stündlich" },
  { value: 2, label: "Alle 2 Stunden" },
  { value: 3, label: "Alle 3 Stunden" },
  { value: 6, label: "Alle 6 Stunden" },
  { value: 12, label: "Alle 12 Stunden" },
  { value: 24, label: "Täglich" },
];

interface BackupsProps {
  serverId: number;
}

export function Backups({ serverId }: BackupsProps) {
  const { t } = useTranslation();
  const [backups, setBackups] = useState<Backup[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // Create modal
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [backupName, setBackupName] = useState("");

  // Scheduling settings
  const [showSettings, setShowSettings] = useState(false);
  const [settings, setSettings] = useState<BackupSettings | null>(null);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [backupStatus, setBackupStatus] = useState<any>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  const fetchBackups = async () => {
    if (!serverId) return;
    try {
      const data = await api<Backup[]>(`/backups/${serverId}`);
      setBackups(data);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  };

  const fetchSettings = async () => {
    if (!serverId) return;
    try {
      const data = await api<BackupSettings>(`/backups/${serverId}/settings`);
      setSettings(data);
    } catch {
      // silent
    }
  };

  const fetchStatus = async () => {
    if (!serverId) return;
    try {
      const s = await api<any>(`/backups/${serverId}/status?_t=${Date.now()}`);
      setBackupStatus(s);
    } catch {
      setBackupStatus(null);
    }
  };

  useEffect(() => {
    fetchBackups();
    fetchSettings();
    fetchStatus(); // AUFGABE 1: sofort bei Mount (Tab-Wechsel)
  }, [serverId]);

  // Live-Status Polling (alle 2s) mit Cache-Buster
  useEffect(() => {
    if (!serverId) return;
    const interval = setInterval(async () => {
      try {
        const s = await api<any>(`/backups/${serverId}/status?_t=${Date.now()}`);
        setBackupStatus(s);
      } catch {
        setBackupStatus(null);
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [serverId]);

  // Live-Tick für "Läuft seit" (separate Sekundenuhr, nicht vom Poll)
  const tickRef = useRef<number | null>(null);
  const createTimeoutRef = useRef<number | null>(null);
  useEffect(() => {
    if (backupStatus?.active && backupStatus?.started_at) {
      const start = new Date(backupStatus.started_at).getTime();
      const update = () =>
        setElapsedSeconds(Math.floor((Date.now() - start) / 1000));
      update();
      if (tickRef.current) clearInterval(tickRef.current);
      tickRef.current = window.setInterval(update, 1000);
    } else {
      if (tickRef.current) {
        clearInterval(tickRef.current);
        tickRef.current = null;
      }
      setElapsedSeconds(0);
    }
    return () => {
      if (tickRef.current) {
        clearInterval(tickRef.current);
        tickRef.current = null;
      }
    };
  }, [backupStatus?.active, backupStatus?.started_at]);

  // AUFGABE 3: Bei Ende (active true -> false) sofort fetchBackups (nicht auf Tick warten)
  // Backend-Completion-Event: Modal hier schließen statt mit Timeout
  const prevActiveRef = useRef(false);
  useEffect(() => {
    const nowActive = !!backupStatus?.active;
    if (prevActiveRef.current && !nowActive) {
      fetchBackups();
      setShowCreateModal(false);
      setBackupName("");
      toast.success(t("backups.created", "Backup erfolgreich abgeschlossen"));
    }
    prevActiveRef.current = nowActive;
  }, [backupStatus?.active, t]);

  // Cleanup pending create timeout on unmount (review Issue 9)
  useEffect(() => {
    return () => {
      if (createTimeoutRef.current) {
        clearTimeout(createTimeoutRef.current);
        createTimeoutRef.current = null;
      }
    };
  }, []);

  const createBackup = async () => {
    setActionLoading("create");
    try {
      await api(`/backups/${serverId}`, {
        method: "POST",
        body: JSON.stringify({ name: backupName.trim() || null }),
      });
      // Der Aufruf blockiert nicht (async Backend).
      // actionLoading wird zurückgesetzt, aber das Modal bleibt offen, bis das Polling (Completion-Event) das Ende signalisiert.
      setActionLoading(null);
    } catch (err: any) {
      toast.error(err.message || t("common.error"));
      setActionLoading(null);
    }
  };

  const restoreBackup = async (backupId: number) => {
    if (!(await confirm({ message: t("backups.confirmRestore") }))) return;
    setActionLoading(`restore-${backupId}`);
    try {
      await api(`/backups/${serverId}/restore/${backupId}`, { method: "POST" });
      toast.success(t("backups.restored"));
    } catch (err: any) {
      toast.error(err.message || t("common.error"));
    } finally {
      setActionLoading(null);
    }
  };

  const deleteBackup = async (backupId: number) => {
    if (
      !(await confirm({
        message: t("backups.confirmDelete"),
        danger: true,
        confirmText: t("common.delete"),
      }))
    )
      return;
    setActionLoading(`delete-${backupId}`);
    try {
      await api(`/backups/${serverId}/${backupId}`, { method: "DELETE" });
      toast.success(t("backups.deletedBackup"));
      fetchBackups();
    } catch (err: any) {
      toast.error(err.message || t("common.error"));
    } finally {
      setActionLoading(null);
    }
  };

  const saveSettings = async () => {
    if (!settings) return;
    setSettingsSaving(true);
    try {
      await api(`/backups/${serverId}/settings`, {
        method: "PATCH",
        body: JSON.stringify(settings),
      });
      toast.success(t("backups.settingsSaved", "Einstellungen gespeichert"));
    } catch (err: any) {
      toast.error(err.message || t("common.error"));
    } finally {
      setSettingsSaving(false);
    }
  };

  const formatDate = (iso: string) => {
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  };

  const isActive = !!backupStatus?.active;
  const operationLabel =
    backupStatus?.operation === "creating"
      ? t("backups.creating")
      : backupStatus?.operation === "restoring"
        ? t("backups.restoring")
        : "";
  const elapsedLabel = isActive
    ? `${elapsedSeconds} Sekunden`
    : "";

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <span className="w-6 h-6 border-2 border-secondary border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Live-Status Banner (wenn aktiv) */}
      {isActive && (
        <div className="msm-card p-4 border border-secondary/40 bg-surface-container space-y-2">
          <div className="flex items-center gap-3 text-sm text-on-surface">
            <span className="w-4 h-4 border-2 border-secondary border-t-transparent rounded-full animate-spin flex-shrink-0" />
            <span className="font-body-md">{operationLabel || t("backups.creating", "Backup wird erstellt...")}</span>
            {elapsedLabel && (
              <span className="text-on-surface-variant">
                {t("backups.runningSince")} {elapsedLabel}
              </span>
            )}
            {backupStatus?.estimated_size_mb != null && backupStatus.estimated_size_mb > 0 && (
              <span className="text-on-surface-variant">
                Geschätzte Größe: {backupStatus.estimated_size_mb} MB
              </span>
            )}
          </div>
          <div className="h-1 bg-secondary/30 rounded overflow-hidden">
            <div className="h-1 bg-secondary animate-pulse w-1/3" />
          </div>
        </div>
      )}

      {/* Header (Tab-Body) */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <p className="font-body-md text-body-md text-on-surface-variant">
          {t("backups.subtitle")}
        </p>
        <div className="flex gap-2">
          <button
            onClick={() => setShowSettings(!showSettings)}
            disabled={isActive}
            className={`msm-btn-secondary flex items-center gap-2 px-3 py-2 ${showSettings ? "bg-surface-container" : ""}`}
            title={t("backups.scheduling", "Einstellungen")}
          >
            <Settings className="w-4 h-4" />
            {t("backups.scheduling", "Einstellungen")}
          </button>
          <button
            onClick={() => setShowCreateModal(true)}
            disabled={isActive || !!actionLoading}
            className="msm-btn-primary flex items-center gap-2 px-4 py-2 disabled:opacity-50"
          >
            <Plus className="w-4 h-4" />
            {t("backups.create")}
          </button>
        </div>
      </div>

      {/* Scheduling Settings Panel */}
      {showSettings && settings && (
        <div className="msm-card p-5 space-y-4">
          <h2 className="font-headline text-body-lg text-on-surface">
            {t("backups.schedulingTitle", "Backup-Einstellungen")}
          </h2>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {/* Backup on start */}
            <label className="flex items-center gap-2 cursor-pointer self-end pb-1">
              <div
                className={`relative w-10 h-6 rounded-full transition-colors ${settings.backup_on_start ? "bg-secondary" : "bg-surface-container-highest"}`}
              >
                <input
                  type="checkbox"
                  checked={settings.backup_on_start}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      backup_on_start: e.target.checked,
                    })
                  }
                  className="sr-only"
                />
                <span
                  className={`absolute top-1 left-1 w-4 h-4 rounded-full transition-transform ${settings.backup_on_start ? "translate-x-4 bg-on-secondary" : "bg-on-surface"}`}
                />
              </div>
              <span className="font-body-md text-sm text-on-surface-variant">
                {t("backups.backupOnStart", "Backup vor dem Start erstellen")}
              </span>
            </label>

            {/* Interval */}
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider text-xs">
                {t("backups.interval", "Intervall")}
              </label>
              <select
                value={settings.backup_interval_hours ?? 0}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    backup_interval_hours: parseInt(e.target.value) || null,
                  })
                }
                className="msm-input"
              >
                {INTERVAL_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Retention */}
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider text-xs">
                {t("backups.retention", "Aufbewahrung (Anzahl)")}
              </label>
              <input
                type="number"
                min={1}
                max={50}
                value={settings.backup_retention_count}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    backup_retention_count: Math.max(
                      1,
                      parseInt(e.target.value) || 1,
                    ),
                  })
                }
                className="msm-input"
              />
              <p className="mt-1 text-xs text-on-surface-variant">
                Die {settings.backup_retention_count} ältesten Backups werden
                automatisch gelöscht, wenn ein neues erstellt wird. Gilt für
                manuelle und automatische Backups.
              </p>
            </div>
          </div>

          <div className="flex justify-end">
            <button
              onClick={saveSettings}
              disabled={isActive || settingsSaving}
              className="msm-btn-primary px-4 py-2 disabled:opacity-50"
            >
              {settingsSaving ? t("common.loading") : t("common.save")}
            </button>
          </div>
        </div>
      )}

      {/* Backup List */}
      {backups.length === 0 ? (
        <div className="msm-card p-12 text-center border-dashed border-2 border-outline-variant">
          <HardDrive className="w-10 h-10 text-on-surface-variant mx-auto mb-4" />
          <h3 className="font-headline text-body-lg text-on-surface mb-1">
            {t("backups.noBackups")}
          </h3>
          <p className="font-body-md text-sm text-on-surface-variant">
            {t("backups.createHint")}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {backups.map((backup) => (
            <div
              key={backup.id}
              className="msm-card p-4 flex items-center justify-between"
            >
              <div className="flex items-center gap-4">
                <HardDrive className="w-5 h-5 text-on-surface-variant flex-shrink-0" />
                <div>
                  {backup.name && (
                    <p className="font-headline text-sm text-on-surface">
                      {backup.name}
                    </p>
                  )}
                  <p className="font-body-md text-on-surface text-sm">
                    {formatDate(backup.created_at)}
                  </p>
                  <p className="font-mono-sm text-xs text-on-surface-variant">
                    {backup.size_mb != null ? `${backup.size_mb} MB` : "—"}
                  </p>
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => restoreBackup(backup.id)}
                  disabled={isActive || !!actionLoading}
                  className="msm-btn-secondary flex items-center gap-1 px-3 py-1.5 text-sm disabled:opacity-50"
                  title={t("backups.restore")}
                >
                  <RotateCcw className="w-3.5 h-3.5" />
                  {t("backups.restore")}
                </button>
                <button
                  onClick={() => deleteBackup(backup.id)}
                  disabled={isActive || !!actionLoading}
                  className="msm-btn-danger flex items-center gap-1 px-3 py-1.5 text-sm disabled:opacity-50"
                  title={t("common.delete")}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create Backup Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
          <div className="msm-card w-full max-w-md p-6">
            <h2 className="font-headline text-headline-md text-primary mb-1">
              {t("backups.create")}
            </h2>
            <p className="font-body-md text-sm text-on-surface-variant mb-5">
              {t(
                "backups.createModalHint",
                "Erstellt ein komprimiertes Archiv des Server-Verzeichnisses.",
              )}
            </p>

            <div className="space-y-4">
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider text-xs">
                  {t("backups.backupName", "Name (optional)")}
                </label>
                <input
                  type="text"
                  placeholder={t(
                    "backups.backupNamePlaceholder",
                    "z.B. Vor Update v1.5",
                  )}
                  value={backupName}
                  onChange={(e) => setBackupName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && createBackup()}
                  className="msm-input"
                  autoFocus
                />
              </div>
            </div>

            <div className="flex gap-3 mt-6">
              <button
                onClick={() => {
                  setShowCreateModal(false);
                  setBackupName("");
                }}
                className="msm-btn-secondary flex-1 px-4 py-2"
              >
                {t("common.cancel")}
              </button>
              <button
                onClick={createBackup}
                disabled={isActive || actionLoading === "create"}
                className="msm-btn-primary flex-1 px-4 py-2 disabled:opacity-50"
              >
                {actionLoading === "create"
                  ? t("common.loading")
                  : t("backups.createNow", "Backup erstellen")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
