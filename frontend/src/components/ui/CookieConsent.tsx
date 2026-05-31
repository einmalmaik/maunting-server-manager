import { useEffect, useState } from 'react';
import { CookieBanner } from './CookieBanner';
import { CookieSettingsDialog } from './CookieSettingsDialog';

const BANNER_ENTER_DELAY_MS = 80;
const BANNER_EXIT_DELAY_MS = 250;

export function CookieConsent() {
    const [isBannerVisible, setIsBannerVisible] = useState(false);
    const [isBannerMounted, setIsBannerMounted] = useState(false);
    const [isSettingsOpen, setIsSettingsOpen] = useState(false);
    const [optional, setOptional] = useState(false);

    useEffect(() => {
        const consentRaw = localStorage.getItem('cookie_consent');
        if (!consentRaw) {
            setIsBannerMounted(true);
            const timer = window.setTimeout(() => setIsBannerVisible(true), BANNER_ENTER_DELAY_MS);
            return () => window.clearTimeout(timer);
        }

        const consent = JSON.parse(consentRaw);
        setOptional(consent.optional === true);
    }, []);

    useEffect(() => {
        const handleOpenSettings = () => {
            const consentRaw = localStorage.getItem('cookie_consent');
            if (consentRaw) {
                const consent = JSON.parse(consentRaw);
                setOptional(consent.optional === true);
            }
            setIsSettingsOpen(true);
        };

        window.addEventListener('msm:open-cookie-settings', handleOpenSettings);
        return () => window.removeEventListener('msm:open-cookie-settings', handleOpenSettings);
    }, []);

    const dismissBanner = () => {
        setIsBannerVisible(false);
        window.setTimeout(() => setIsBannerMounted(false), BANNER_EXIT_DELAY_MS);
    };

    const handleAcceptAll = () => {
        localStorage.setItem('cookie_consent', JSON.stringify({ optional: true }));
        setOptional(true);
        dismissBanner();
    };

    const handleEssentialOnly = () => {
        localStorage.setItem('cookie_consent', JSON.stringify({ optional: false }));
        setOptional(false);
        dismissBanner();
    };

    const handleCustomize = () => {
        setIsSettingsOpen(true);
    };

    const handleSettingsOpenChange = (open: boolean) => {
        if (open) {
            const consentRaw = localStorage.getItem('cookie_consent');
            if (consentRaw) {
                const consent = JSON.parse(consentRaw);
                setOptional(consent.optional === true);
            }
        }
        setIsSettingsOpen(open);
    };

    const handleSaveSettings = () => {
        localStorage.setItem('cookie_consent', JSON.stringify({ optional }));
        setIsSettingsOpen(false);
        if (isBannerMounted) {
            dismissBanner();
        }
    };

    if (!isBannerMounted && !isSettingsOpen) {
        return null;
    }

    return (
        <>
            <CookieBanner
                visible={isBannerMounted}
                isActive={isBannerVisible}
                onAcceptAll={handleAcceptAll}
                onEssentialOnly={handleEssentialOnly}
                onCustomize={handleCustomize}
            />
            {isSettingsOpen && (
                <CookieSettingsDialog
                    optional={optional}
                    onOpenChange={handleSettingsOpenChange}
                    onOptionalChange={setOptional}
                    onSave={handleSaveSettings}
                />
            )}
        </>
    );
}
