import { useTranslation } from 'react-i18next';
import { BarChart3, Shield, Sparkles, X } from 'lucide-react';
import { COOKIE_DIALOG_COPY } from './cookieConsentContent';

interface CookieSettingsDialogProps {
    optional: boolean;
    onOpenChange: (open: boolean) => void;
    onOptionalChange: (value: boolean) => void;
    onSave: () => void;
}

export function CookieSettingsDialog({
    optional,
    onOpenChange,
    onOptionalChange,
    onSave,
}: CookieSettingsDialogProps) {
    const { i18n } = useTranslation();
    const language = i18n.language.startsWith('de') ? 'de' : 'en';
    const copy = COOKIE_DIALOG_COPY[language];
    const necessaryItems = copy.categories.necessary.items;
    const functionalItems = copy.categories.functional.items;
    const analyticsItems = copy.categories.analytics.items;

    return (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" onClick={() => onOpenChange(false)}>
            <div className="msm-card w-full max-w-2xl max-h-[80vh] overflow-y-auto p-6" onClick={(e) => e.stopPropagation()}>
                <div className="flex justify-between items-start mb-6">
                    <div>
                        <h2 className="text-lg font-semibold text-foreground">{copy.title}</h2>
                        <p className="text-sm text-muted-foreground mt-1">{copy.description}</p>
                    </div>
                    <button onClick={() => onOpenChange(false)} className="text-muted-foreground hover:text-foreground">
                        <X className="w-5 h-5" />
                    </button>
                </div>

                <div className="space-y-6">
                    {/* Necessary */}
                    <div className="flex items-start justify-between gap-4">
                        <div className="flex gap-3 flex-1">
                            <div className="flex-shrink-0 mt-1">
                                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
                                    <Shield className="h-5 w-5 text-primary" />
                                </div>
                            </div>
                            <div className="flex-1">
                                <div className="flex items-center gap-2 mb-1">
                                    <label className="text-base font-semibold text-foreground">{copy.categories.necessary.title}</label>
                                    <span className="text-xs px-2 py-0.5 rounded-full bg-surface-variant text-on-surface-variant font-medium">
                                        {copy.requiredBadge}
                                    </span>
                                </div>
                                <p className="text-sm text-muted-foreground mb-2">{copy.categories.necessary.description}</p>
                                <ul className="text-xs text-muted-foreground space-y-1 list-disc list-inside">
                                    {necessaryItems.map((item) => <li key={item}>{item}</li>)}
                                </ul>
                            </div>
                        </div>
                        <input type="checkbox" checked disabled className="mt-2 w-5 h-5 accent-primary opacity-50" />
                    </div>

                    <div className="h-px w-full bg-border/50" />

                    {/* Functional */}
                    <div className="flex items-start justify-between gap-4">
                        <div className="flex gap-3 flex-1">
                            <div className="flex-shrink-0 mt-1">
                                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
                                    <Sparkles className="h-5 w-5 text-primary" />
                                </div>
                            </div>
                            <div className="flex-1">
                                <label className="text-base font-semibold text-foreground mb-1 block">{copy.categories.functional.title}</label>
                                <p className="text-sm text-muted-foreground mb-2">{copy.categories.functional.description}</p>
                                <ul className="text-xs text-muted-foreground space-y-1 list-disc list-inside">
                                    {functionalItems.map((item) => <li key={item}>{item}</li>)}
                                </ul>
                            </div>
                        </div>
                        <input type="checkbox" checked={optional} onChange={(e) => onOptionalChange(e.target.checked)} className="mt-2 w-5 h-5 accent-primary cursor-pointer" />
                    </div>

                    <div className="h-px w-full bg-border/50" />

                    {/* Analytics */}
                    <div className="flex items-start justify-between gap-4 opacity-60">
                        <div className="flex gap-3 flex-1">
                            <div className="flex-shrink-0 mt-1">
                                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
                                    <BarChart3 className="h-5 w-5 text-primary" />
                                </div>
                            </div>
                            <div className="flex-1">
                                <div className="flex items-center gap-2 mb-1">
                                    <label className="text-base font-semibold text-foreground">{copy.categories.analytics.title}</label>
                                    <span className="text-xs px-2 py-0.5 rounded-full bg-surface-variant text-on-surface-variant font-medium">
                                        {copy.unavailableBadge}
                                    </span>
                                </div>
                                <p className="text-sm text-muted-foreground mb-2">{copy.categories.analytics.description}</p>
                                <ul className="text-xs text-muted-foreground space-y-1 list-disc list-inside">
                                    {analyticsItems.map((item) => <li key={item}>{item}</li>)}
                                </ul>
                            </div>
                        </div>
                        <input type="checkbox" checked={false} disabled className="mt-2 w-5 h-5 accent-primary opacity-50" />
                    </div>
                </div>

                <div className="flex justify-end mt-8">
                    <button onClick={onSave} className="msm-btn-primary w-full sm:w-auto px-6 py-2">
                        {copy.save}
                    </button>
                </div>
            </div>
        </div>
    );
}
