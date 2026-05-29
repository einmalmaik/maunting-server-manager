import { X } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { COOKIE_BANNER_COPY } from './cookieConsentContent';

interface CookieBannerProps {
    visible: boolean;
    isActive: boolean;
    onAcceptAll: () => void;
    onEssentialOnly: () => void;
    onCustomize: () => void;
}

export function CookieBanner({
    visible,
    isActive,
    onAcceptAll,
    onEssentialOnly,
    onCustomize,
}: CookieBannerProps) {
    const { i18n } = useTranslation();
    const language = i18n.language.startsWith('de') ? 'de' : 'en';
    const t = COOKIE_BANNER_COPY[language];

    if (!visible) return null;

    const ghostButtonClassName = "h-7 px-3 rounded-lg text-[0.72rem] shrink-0 text-muted-foreground/60 hover:text-foreground border border-border/35 hover:border-border/60 hover:bg-surface-variant/50 transition-all duration-150 whitespace-nowrap";

    return (
        <>
            <div
                className={`fixed inset-0 z-40 pointer-events-none transition-opacity duration-300 ${isActive ? 'opacity-100' : 'opacity-0'}`}
                style={{ background: 'hsl(0 0% 0% / 0.15)' }}
            />

            <div
                role="dialog"
                aria-live="polite"
                aria-label={t.bannerAriaLabel}
                className={`fixed bottom-4 left-4 right-4 z-50 flex justify-center transition-all duration-300 ease-out ${isActive ? 'translate-y-0 opacity-100' : 'translate-y-4 opacity-0'}`}
            >
                <div className="w-full max-w-2xl rounded-xl overflow-hidden border border-border/35 bg-surface-container-high/95 backdrop-blur-xl shadow-xl shadow-black/25">
                    <div className="flex flex-col gap-3 px-4 py-3.5 sm:flex-row sm:items-center sm:gap-5">
                        <p className="flex-1 min-w-0 text-[0.72rem] text-muted-foreground/65 leading-relaxed">
                            {t.description}{' '}
                            <Link to="/privacy" className="text-muted-foreground/80 underline underline-offset-2 hover:text-foreground transition-colors duration-150">
                                {t.privacy}
                            </Link>
                        </p>

                        <div className="flex items-center gap-1.5 flex-wrap shrink-0">
                            <button type="button" onClick={onEssentialOnly} className={ghostButtonClassName}>
                                {t.essentialOnly}
                            </button>
                            <button type="button" onClick={onCustomize} className={ghostButtonClassName}>
                                {t.customize}
                            </button>
                            <button
                                type="button"
                                onClick={onAcceptAll}
                                className="h-7 px-3 rounded-lg text-[0.72rem] font-medium shrink-0 bg-primary text-primary-foreground hover:bg-primary/90 active:bg-primary/80 transition-all duration-150 whitespace-nowrap"
                            >
                                {t.acceptAll}
                            </button>
                            <button
                                type="button"
                                onClick={onEssentialOnly}
                                aria-label={t.closeAriaLabel}
                                className="h-7 w-7 flex items-center justify-center rounded-lg shrink-0 text-muted-foreground/35 hover:text-muted-foreground/70 hover:bg-surface-variant/50 transition-all duration-150 ml-0.5"
                            >
                                <X className="h-3.5 w-3.5" />
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </>
    );
}
