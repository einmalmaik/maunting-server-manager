import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, RefreshCw, ShieldCheck } from "lucide-react";
import { api } from "@/api/client";
import { Dropdown } from "@/components/ui/Dropdown";
import { toast } from "@/stores/toastStore";
import type { BlueprintListEntry, Server } from "@/types";

interface SwitchBlueprintDialogProps {
  open: boolean;
  onClose: () => void;
  server: Server;
  onSwitched: () => void;
}

export function SwitchBlueprintDialog({
  open,
  onClose,
  server,
  onSwitched,
}: SwitchBlueprintDialogProps) {
  const { t } = useTranslation();
  const [blueprints, setBlueprints] = useState<BlueprintListEntry[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const abbrechenRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    // `GET /blueprints` antwortet mit `{ blueprints: [...] }`, nicht mit einem
    // nackten Array — so liest es auch die Blueprint-Seite (Blueprints.tsx:62).
    //
    // Hier stand `Array.isArray(data) ? data : []`. Das war immer der leere
    // Fall: das Dropdown blieb ohne Eintraege, ohne Fehler, ohne Hinweis. Ein
    // stiller Rueckfall auf "nichts" ist schlimmer als ein Fehler — man sieht
    // eine leere Auswahl und haelt sie fuer die Wahrheit.
    api<{ blueprints: BlueprintListEntry[] }>("/blueprints")
      .then((data) => {
        const list = data?.blueprints ?? [];
        setBlueprints(list);
        if (list.length > 0) {
          const firstOther = list.find((b) => b.id !== server.game_type);
          setSelectedId(firstOther ? firstOther.id : list[0].id);
        }
      })
      .catch((err) => {
        const raw = err instanceof Error ? err.message : String(err);
        toast.error(t(raw, { defaultValue: raw }) || t("common.error"));
      })
      .finally(() => setLoading(false));
  }, [open, server.game_type, t]);

  // Fokus wie im Nachbardialog ResourceEditorDialog.tsx: beim Öffnen springt er
  // auf "Abbrechen", beim Schließen zurück auf den Knopf, der den Dialog
  // geöffnet hat. Ohne das startet ein Tastaturbenutzer hinter der Seite und
  // muss sich erst durch sie hindurch in den Dialog tabben.
  useEffect(() => {
    if (!open) return;
    const vorherAktiv = document.activeElement as HTMLElement | null;
    const zeitgeber = setTimeout(() => abbrechenRef.current?.focus(), 0);
    return () => {
      clearTimeout(zeitgeber);
      vorherAktiv?.focus();
    };
  }, [open]);

  // Escape schließt den Dialog, solange kein Wechsel läuft; Tab und Shift+Tab
  // bleiben im Dialog gefangen. Gleiches Vorgehen wie ResourceEditorDialog.tsx.
  useEffect(() => {
    if (!open) return;
    const beiTaste = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        // Die Blueprint-Auswahl behandelt Escape selbst und markiert die Taste
        // dabei als verbraucht (Dropdown.tsx). Ein Escape, das nur die
        // aufgeklappte Liste schließen soll, darf nicht gleich den ganzen
        // Dialog mitnehmen.
        if (submitting || e.defaultPrevented) return;
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key === "Tab") {
        const fokussierbar = dialogRef.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        );
        if (!fokussierbar || fokussierbar.length === 0) return;
        const erstes = fokussierbar[0];
        const letztes = fokussierbar[fokussierbar.length - 1];
        const drinnen = dialogRef.current?.contains(document.activeElement);
        if (e.shiftKey) {
          if (document.activeElement === erstes || !drinnen) {
            e.preventDefault();
            letztes.focus();
          }
        } else {
          if (document.activeElement === letztes || !drinnen) {
            e.preventDefault();
            erstes.focus();
          }
        }
      }
    };
    window.addEventListener("keydown", beiTaste);
    return () => window.removeEventListener("keydown", beiTaste);
  }, [open, onClose, submitting]);

  if (!open) return null;

  const handleSwitch = async () => {
    if (!selectedId || selectedId === server.game_type) return;
    setSubmitting(true);
    try {
      await api<{ message: string }>(`/servers/${server.id}/switch-blueprint`, {
        method: "POST",
        body: JSON.stringify({ new_blueprint_id: selectedId }),
      });
      toast.success(t("servers.blueprintSwitchedSuccess", "Spiel / Blueprint erfolgreich gewechselt! Pflicht-Backup wurde erstellt."));
      onSwitched();
      onClose();
    } catch (err: unknown) {
      const raw = err instanceof Error ? err.message : String(err);
      toast.error(t(raw, { defaultValue: raw }) || t("common.error"));
    } finally {
      setSubmitting(false);
    }
  };

  const currentBpName =
    (Array.isArray(blueprints) ? blueprints : []).find((b) => b.id === server.game_type)?.name || server.game_type;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm p-4">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="switch-blueprint-title"
        className="msm-card max-w-lg w-full p-6 shadow-2xl border border-outline/30 animate-in fade-in zoom-in duration-150"
      >
        <h2
          id="switch-blueprint-title"
          className="font-headline text-headline-sm text-on-surface mb-2 flex items-center gap-2"
        >
          <RefreshCw className="w-5 h-5 text-primary" />
          {t("servers.switchBlueprintTitle", "Spiel / Blueprint wechseln")}
        </h2>

        <p className="font-body-md text-sm text-on-surface-variant mb-4">
          {t("servers.switchBlueprintSubtitle", "Wechsle das Spiel oder die Blueprint-Variante für diesen Server.")}
        </p>

        <div className="msm-card p-3 bg-surface-container-highest/40 border-outline/20 mb-4 flex items-start gap-2.5">
          <ShieldCheck className="w-5 h-5 text-status-success flex-shrink-0 mt-0.5" />
          <p className="font-body-md text-xs text-on-surface-variant">
            <strong>{t("servers.backupProtectionTitle", "Zentraler Backup-Schutz:")}</strong>{" "}
            {t("servers.backupProtectionDesc", "Vor dem Wechsel wird ausnahmslos ein verschlüsseltes Pflicht-Backup deines aktuellen Spielstands erzeugt.")}
          </p>
        </div>

        {server.status !== "stopped" && (
          <div className="msm-card p-3 border-status-warning/40 bg-status-warning/5 mb-4 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-status-warning flex-shrink-0" />
            <p className="font-body-md text-xs text-status-warning">
              {t("servers.mustStopToSwitch", "Der Server muss gestoppt sein, um das Spiel zu wechseln.")}
            </p>
          </div>
        )}

        <div className="space-y-4 mb-6">
          <div>
            <label className="block font-headline text-xs text-on-surface-variant mb-1">
              {t("servers.currentBlueprint", "Aktuelles Spiel / Blueprint")}
            </label>
            <div className="font-mono text-sm px-3 py-2 rounded bg-surface-container-highest text-on-surface border border-outline/20">
              {currentBpName} ({server.game_type})
            </div>
          </div>

          <div>
            <label className="block font-headline text-xs text-on-surface-variant mb-1">
              {t("servers.selectNewBlueprint", "Neues Spiel / Blueprint auswählen")}
            </label>
            {loading ? (
              <div className="flex items-center gap-2 py-2 text-xs text-on-surface-variant">
                <span className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                {t("common.loading", "Laden...")}
              </div>
            ) : (
              // Vorher ein natives <select>. Bei 27 nativen Blueprints ist die
              // Liste hoeher als der Platz unter dem Feld, und der Browser
              // klappte sie nach oben auf — im Dialog wirkt das wie ein Fehler.
              // `Dropdown` ist die Auswahl, die das Panel ohnehin ueberall
              // benutzt: sie klappt nach unten und begrenzt ihre Hoehe selbst.
              <Dropdown
                data-testid="switch-blueprint-select"
                value={selectedId || null}
                onChange={setSelectedId}
                disabled={submitting || server.status !== "stopped"}
                placeholder={t("servers.selectNewBlueprint", "Neues Spiel / Blueprint auswählen")}
                aria-label={t("servers.selectNewBlueprint", "Neues Spiel / Blueprint auswählen")}
                options={blueprints.map((bp) => ({
                  value: bp.id,
                  label: `${bp.name} (${bp.category || "Native"})`,
                }))}
              />
            )}
          </div>
        </div>

        <div className="flex justify-end gap-3 pt-2 border-t border-outline/20">
          <button
            ref={abbrechenRef}
            type="button"
            className="msm-btn-secondary px-4 py-2 text-sm"
            onClick={onClose}
            disabled={submitting}
          >
            {t("common.cancel", "Abbrechen")}
          </button>
          <button
            type="button"
            className="msm-btn-primary px-4 py-2 text-sm flex items-center gap-2"
            onClick={handleSwitch}
            disabled={
              submitting ||
              loading ||
              !selectedId ||
              selectedId === server.game_type ||
              server.status !== "stopped"
            }
          >
            {submitting && <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />}
            {t("servers.confirmSwitchBtn", "Spiel wechseln & Backup erstellen")}
          </button>
        </div>
      </div>
    </div>
  );
}
