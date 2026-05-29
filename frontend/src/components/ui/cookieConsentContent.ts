export type CookieConsentLanguage = 'en' | 'de'

export const COOKIE_BANNER_COPY = {
    en: {
        bannerAriaLabel: 'Cookie consent',
        description: 'We use cookies to improve your experience.',
        privacy: 'Privacy Policy',
        essentialOnly: 'Essential only',
        customize: 'Customize',
        acceptAll: 'Accept all',
        closeAriaLabel: 'Close',
    },
    de: {
        bannerAriaLabel: 'Cookie-Einstellungen',
        description: 'Wir verwenden Cookies, um Ihre Erfahrung zu verbessern.',
        privacy: 'Datenschutz',
        essentialOnly: 'Nur essenziell',
        customize: 'Anpassen',
        acceptAll: 'Alle akzeptieren',
        closeAriaLabel: 'Schliessen',
    },
} as const

export const COOKIE_DIALOG_COPY = {
    en: {
        title: 'Cookie Settings',
        description: 'Manage your cookie preferences. You can enable or disable non-essential cookies at any time.',
        save: 'Save Preferences',
        requiredBadge: 'Required',
        unavailableBadge: 'Not available',
        categories: {
            necessary: {
                title: 'Necessary',
                description: 'Required for authentication, security, and basic functionality. Cannot be disabled.',
                items: ['Authentication session', 'Session security controls', 'Cookie consent preferences'],
            },
            functional: {
                title: 'Functional Cookies',
                description: 'Remember your preferences and settings for a better experience.',
                items: ['Theme preference', 'Language preference', 'Auto-lock timer'],
            },
            analytics: {
                title: 'Analytics Cookies',
                description: 'Analytics cookies are currently not implemented and not planned in the near future.',
                items: ['Usage statistics (not available)', 'Feature engagement metrics (not available)'],
            },
        },
    },
    de: {
        title: 'Cookie-Einstellungen',
        description:
            'Verwalte deine Cookie-Praeferenzen. Du kannst nicht-essenzielle Cookies jederzeit aktivieren oder deaktivieren.',
        save: 'Einstellungen speichern',
        requiredBadge: 'Erforderlich',
        unavailableBadge: 'Nicht verfügbar',
        categories: {
            necessary: {
                title: 'Notwendig',
                description: 'Erforderlich für Authentifizierung, Sicherheit und grundlegende Funktionen. Kann nicht deaktiviert werden.',
                items: ['Authentifizierungs-Session', 'Sicherheitskontrollen der Sitzung', 'Cookie-Einwilligungspraeferenzen'],
            },
            functional: {
                title: 'Funktionale Cookies',
                description: 'Speichern Präferenzen und Einstellungen für eine bessere Erfahrung.',
                items: ['Theme-Praeferenz', 'Sprachpraeferenz', 'Auto-Lock-Timer'],
            },
            analytics: {
                title: 'Analytics-Cookies',
                description: 'Analytics-Cookies sind derzeit nicht implementiert und in naher Zukunft nicht geplant.',
                items: ['Nutzungsstatistiken (nicht verfügbar)', 'Feature-Engagement-Metriken (nicht verfügbar)'],
            },
        },
    },
} as const
