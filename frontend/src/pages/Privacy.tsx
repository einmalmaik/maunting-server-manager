import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import { LegalDocumentViewer, type LegalDocumentData } from '@/components/ui/LegalDocumentViewer'

export function Privacy() {
  const { isAuthenticated } = useAuthStore()
  const { t } = useTranslation()

  const document: LegalDocumentData = {
    title: t('privacyPolicy.title'),
    intro: t('privacyPolicy.intro'),
    callout: t('privacyPolicy.callout'),
    lastUpdated: '2026-09-24',
    version: '3.4',
    meta: 'Maunting Studios — Sicherheit braucht Vertrauen',
    sections: [
      { heading: t('privacyPolicy.sections.scope.heading'), body: t('privacyPolicy.sections.scope.body') },
      {
        heading: t('privacyPolicy.sections.accounts.heading'),
        body: t('privacyPolicy.sections.accounts.body'),
        items: [
          t('privacyPolicy.sections.accounts.items.identity'),
          t('privacyPolicy.sections.accounts.items.security'),
          t('privacyPolicy.sections.accounts.items.rbac'),
          t('privacyPolicy.sections.accounts.items.audit'),
        ],
      },
      {
        heading: t('privacyPolicy.sections.infrastructure.heading'),
        body: t('privacyPolicy.sections.infrastructure.body'),
        items: [
          t('privacyPolicy.sections.infrastructure.items.server'),
          t('privacyPolicy.sections.infrastructure.items.node'),
          t('privacyPolicy.sections.infrastructure.items.operations'),
        ],
      },
      {
        heading: t('privacyPolicy.sections.protection.heading'),
        body: t('privacyPolicy.sections.protection.body'),
        items: [
          t('privacyPolicy.sections.protection.items.dis'),
          t('privacyPolicy.sections.protection.items.backups'),
          t('privacyPolicy.sections.protection.items.metadata'),
        ],
      },
      {
        // Der Messenger ist der einzige Bereich, in dem die Instanz Inhalte
        // weiterleitet, die sie selbst nicht lesen kann. Dazu gehoert auch die
        // Kehrseite: der Schluessel gehoert dem Geraet, und wer alle Geraete
        // verliert, verliert den Verlauf. Niemand kann ihn zurueckholen, auch
        // der Betreiber nicht.
        heading: t('privacyPolicy.sections.messenger.heading'),
        body: t('privacyPolicy.sections.messenger.body'),
        items: [
          t('privacyPolicy.sections.messenger.items.envelopes'),
          t('privacyPolicy.sections.messenger.items.deviceKey'),
          t('privacyPolicy.sections.messenger.items.deviceFanout'),
          t('privacyPolicy.sections.messenger.items.deviceHistory'),
          t('privacyPolicy.sections.messenger.items.devicePin'),
          t('privacyPolicy.sections.messenger.items.ratchet'),
          t('privacyPolicy.sections.messenger.items.groups'),
          // Wer alle wecken darf, entscheidet das empfangende Geraet. Der
          // Server kann es nicht: er liest den Inhalt nicht.
          t('privacyPolicy.sections.messenger.items.mentions'),
          t('privacyPolicy.sections.messenger.items.attachments'),
          // Was beim Loeschen wirklich passiert. Steht hier, weil die Antwort
          // frueher "nichts" war: die Zeile verschwand aus der Anzeige, der
          // Umschlag blieb im Postfach und der Anhang auf der Platte.
          t('privacyPolicy.sections.messenger.items.deletion'),
          // Verschwindende Nachrichten samt der Grenze: beim Server loeschen
          // kann nur, wer hochgeladen hat.
          t('privacyPolicy.sections.messenger.items.retention'),
          t('privacyPolicy.sections.messenger.items.receipts'),
          // Anrufe sind die eine Stelle, an der die Instanz mehr erfaehrt als
          // bei Nachrichten: der Inhalt bleibt verschluesselt, aber wer wann
          // mit wem in einem Raum war, sieht der Medienserver. Das gehoert
          // hierher und nicht in eine Fussnote.
          t('privacyPolicy.sections.messenger.items.calls'),
          t('privacyPolicy.sections.messenger.items.callMetadata'),
          // Die eine Ausnahme von "kein Anruf wird gespeichert": eine
          // Moderationshandlung hinterlaesst einen Eintrag. Wer anderen das
          // Wort nehmen kann, muss dafuer nachvollziehbar sein.
          t('privacyPolicy.sections.messenger.items.callModeration'),
        ],
      },
      {
        heading: t('privacyPolicy.sections.providers.heading'),
        body: t('privacyPolicy.sections.providers.body'),
        items: [
          t('privacyPolicy.sections.providers.items.email'),
          t('privacyPolicy.sections.providers.items.captcha'),
          t('privacyPolicy.sections.providers.items.oauth'),
          t('privacyPolicy.sections.providers.items.support'),
          t('privacyPolicy.sections.providers.items.s3'),
          // Die Download-Hinweise in der Seitenleiste sind der einzige Ort, an dem das Panel
          // auf einen Fremdserver verweist, ohne dass der Betreiber ihn konfiguriert hat.
          t('privacyPolicy.sections.providers.items.downloads'),
        ],
      },
      {
        heading: t('privacyPolicy.sections.ai.heading'),
        body: t('privacyPolicy.sections.ai.body'),
        items: [
          t('privacyPolicy.sections.ai.items.messages'),
          t('privacyPolicy.sections.ai.items.context'),
          t('privacyPolicy.sections.ai.items.credentials'),
          t('privacyPolicy.sections.ai.items.usage'),
          t('privacyPolicy.sections.ai.items.memory'),
          // Phase E: Das Gedaechtnis ist standardmaessig aus und braucht eine
          // ausdrueckliche Zustimmung. Der Punkt benennt zugleich die Grenze
          // der Verschluesselung — sie schuetzt die Datenbank, nicht die
          // Uebertragung an den Modellanbieter.
          t('privacyPolicy.sections.ai.items.memoryConsent'),
          t('privacyPolicy.sections.ai.items.attachments'),
          // Zielpunkt 17: der autonome Modus veraendert, wer eine Aktion
          // ausloest. Das gehoert ausdruecklich in den Datenschutzhinweis.
          t('privacyPolicy.sections.ai.items.autonomy'),
          t('privacyPolicy.sections.ai.items.tools'),
          // Die Gegenrichtung zum Punkt darueber: was die Werkzeuge erreichen,
          // steht dort, und der Messenger gehoert seit 09/2026 ausdruecklich
          // nicht dazu. Das ist keine Selbstverstaendlichkeit, sondern ein
          // Rueckbau — es gab Werkzeuge dafuer.
          t('privacyPolicy.sections.ai.items.noMessenger'),
          t('privacyPolicy.sections.ai.items.voice'),
          // Die Kopplung an die Guardian-Engine: seit ihr kann eine
          // Verarbeitung beginnen, ohne dass jemand am Panel sitzt. Das ist die
          // eine Aussage, die aus keinem der anderen Punkte folgt — alle
          // beschreiben, was mit einer Eingabe geschieht, dieser beschreibt eine
          // Verarbeitung ohne Eingabe.
          t('privacyPolicy.sections.ai.items.guardian'),
          // Der zweite Ausloeser ohne anwesenden Menschen: die Uhr. Er steht
          // ausdruecklich neben `guardian` und nicht darin — dort weckt eine
          // Stoerung, hier ein Auftrag, den der Nutzer selbst erteilt hat, und
          // die Aufbewahrung (Auftragstext, Zeitplan, Zeitzone) ist eine
          // eigene Aussage.
          t('privacyPolicy.sections.ai.items.tasks'),
          // Verknuepfte Postfaecher (IMAP/SMTP) und Kalender (CalDAV) fuer den
          // KI-Assistenten: DIS AES-256-GCM verschluesselt, kein unbemerkter
          // E-Mail-Versand oder Terminanlage ohne manuelle Freigabekarte.
          t('privacyPolicy.sections.ai.items.mailboxes'),
          t('privacyPolicy.sections.ai.items.calendars'),
        ],
      },
      {
        // Die Desktop-App (Maunting Smart System).
        heading: t('privacyPolicy.sections.desktopApp.heading'),
        body: t('privacyPolicy.sections.desktopApp.body'),
        items: [
          t('privacyPolicy.sections.desktopApp.items.computerUse'),
          t('privacyPolicy.sections.desktopApp.items.wakeword'),
          t('privacyPolicy.sections.desktopApp.items.wakewordAufnahmen'),
          t('privacyPolicy.sections.desktopApp.items.sprachsitzung'),
          t('privacyPolicy.sections.desktopApp.items.benachrichtigungen'),
          t('privacyPolicy.sections.desktopApp.items.autostart'),
          t('privacyPolicy.sections.desktopApp.items.bildschirm'),
          t('privacyPolicy.sections.desktopApp.items.dateien'),
          t('privacyPolicy.sections.desktopApp.items.schreiben'),
          t('privacyPolicy.sections.desktopApp.items.aufraeumen'),
          t('privacyPolicy.sections.desktopApp.items.virenscan'),
          t('privacyPolicy.sections.desktopApp.items.eingabe'),
          t('privacyPolicy.sections.desktopApp.items.discordRpc'),
          t('privacyPolicy.sections.desktopApp.items.lokal'),
        ],
      },
      {
        // Phase 6: nur sichtbar relevant, wenn ein Hoster angebunden ist —
        // der Abschnitt erklaert aber unabhaengig davon, was MSM in dem Fall
        // speichert und was ausdruecklich nicht.
        heading: t('privacyPolicy.sections.hoster.heading'),
        body: t('privacyPolicy.sections.hoster.body'),
        items: [
          t('privacyPolicy.sections.hoster.items.identity'),
          t('privacyPolicy.sections.hoster.items.contract'),
          t('privacyPolicy.sections.hoster.items.noPassword'),
          t('privacyPolicy.sections.hoster.items.handoff'),
          t('privacyPolicy.sections.hoster.items.webhook'),
        ],
      },
      {
        // Phase 7: eigene Zugangsdaten sind personenbezogen und verdienen
        // einen eigenen Abschnitt statt einer Fussnote unter "Schutz".
        heading: t('privacyPolicy.sections.credentials.heading'),
        body: t('privacyPolicy.sections.credentials.body'),
        items: [
          t('privacyPolicy.sections.credentials.items.storage'),
          t('privacyPolicy.sections.credentials.items.visibility'),
          t('privacyPolicy.sections.credentials.items.usage'),
          t('privacyPolicy.sections.credentials.items.deletion'),
        ],
      },
      {
        heading: t('privacyPolicy.sections.storage.heading'),
        body: t('privacyPolicy.sections.storage.body'),
        items: [
          t('privacyPolicy.sections.storage.items.session'),
          t('privacyPolicy.sections.storage.items.csrf'),
          t('privacyPolicy.sections.storage.items.preferences'),
          t('privacyPolicy.sections.storage.items.offlineNotesAndCalendar'),
        ],
      },
      {
        heading: t('privacyPolicy.sections.retention.heading'),
        body: t('privacyPolicy.sections.retention.body'),
        items: [
          t('privacyPolicy.sections.retention.items.operator'),
          t('privacyPolicy.sections.retention.items.deletion'),
          t('privacyPolicy.sections.retention.items.audit'),
        ],
      },
      { heading: t('privacyPolicy.sections.responsibility.heading'), body: t('privacyPolicy.sections.responsibility.body') },
    ],
  }

  return (
    <LegalDocumentViewer
      document={document}
      backTo={isAuthenticated ? '/docs' : '/login'}
      backLabel={t('common.back')}
      docLabel={t('privacyPolicy.documentLabel')}
      summaryLabel={t('privacyPolicy.summaryLabel')}
      versionLabel={t('privacyPolicy.versionLabel')}
      updatedLabel={t('privacyPolicy.updatedLabel')}
    />
  )
}
