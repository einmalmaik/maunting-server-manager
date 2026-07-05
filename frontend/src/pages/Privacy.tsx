import { useAuthStore } from '@/stores/authStore';
import { useTranslation } from 'react-i18next';
import { LegalDocumentViewer, type LegalDocumentData } from '@/components/ui/LegalDocumentViewer';

export function Privacy() {
  const { isAuthenticated } = useAuthStore();
  const { t } = useTranslation();

  const document: LegalDocumentData = {
    title: 'Datenschutzerklärung',
    intro: 'Diese Instanz des Maunting Server Managers ist auf Datensparsamkeit ausgelegt und verarbeitet nur Daten, die für Account, Sicherheit und Panelbetrieb notwendig sind.',
    callout: 'Es werden keine Tracking-Daten erhoben und keine Analytics-Dienste eingebunden.',
    lastUpdated: '2026-07-05',
    version: '1.0',
    meta: 'Maunting Server Manager',
    sections: [
      {
        heading: '1. Grundprinzip',
        body: 'Der Maunting Server Manager ist nach dem Prinzip der maximalen Datensparsamkeit entwickelt. Wir speichern keine Metadaten, keine Tracking-Daten und nutzen keine Analytics-Dienste. Diese Instanz wird eigenverantwortlich gehostet.',
      },
      {
        heading: '2. Gespeicherte Daten',
        body: 'Wir speichern ausschließlich die Daten, die für den Betrieb Ihres Accounts zwingend erforderlich sind:',
        items: ['E-Mail-Adresse (für den Account-Login)'],
      },
      {
        heading: '3. Cookies und lokale Speicherung',
        body: 'Es werden ausschließlich technisch notwendige Cookies und lokale Speicherwerte eingesetzt:',
        items: [
          'Session-Cookie: Sitzungsverwaltung',
          'CSRF-Token: Schutz gegen Cross-Site-Request-Forgery',
          'Auth-Cookie: Angemeldet bleiben',
          'Lokaler Hinweis-Status: speichert nur, dass der Datenschutz-Hinweis gelesen wurde',
        ],
      },
      {
        heading: '4. Weitergabe an Dritte',
        body: 'Es erfolgt keine Weitergabe von Daten an Dritte. Alle Daten verbleiben lokal auf dem Server dieser Instanz.',
      },
      {
        heading: '5. Recht auf Löschung',
        body: 'Sie haben jederzeit das Recht, Ihren Account zu löschen. Bei einer Löschung werden alle mit Ihrem Account verknüpften personenbezogenen Daten unwiderruflich aus der Datenbank entfernt.',
      },
    ],
  }

  return (
    <LegalDocumentViewer
      document={document}
      backTo={isAuthenticated ? '/docs' : '/login'}
      backLabel={t('common.back')}
      docLabel="MSM Legal"
      summaryLabel="Datenschutz"
      versionLabel="Version"
      updatedLabel="Aktualisiert"
    />
  )
}
