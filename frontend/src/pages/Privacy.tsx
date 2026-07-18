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
    lastUpdated: '2026-07-18',
    version: '2.0',
    meta: 'Maunting Server Manager',
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
        heading: t('privacyPolicy.sections.providers.heading'),
        body: t('privacyPolicy.sections.providers.body'),
        items: [
          t('privacyPolicy.sections.providers.items.email'),
          t('privacyPolicy.sections.providers.items.captcha'),
          t('privacyPolicy.sections.providers.items.oauth'),
          t('privacyPolicy.sections.providers.items.support'),
          t('privacyPolicy.sections.providers.items.s3'),
        ],
      },
      {
        heading: t('privacyPolicy.sections.storage.heading'),
        body: t('privacyPolicy.sections.storage.body'),
        items: [
          t('privacyPolicy.sections.storage.items.session'),
          t('privacyPolicy.sections.storage.items.csrf'),
          t('privacyPolicy.sections.storage.items.preferences'),
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
