import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Privacy } from './Privacy';
import { useAuthStore } from '@/stores/authStore';
import i18n from '@/i18n';

function renderPrivacy() {
  return render(
    <MemoryRouter>
      <Privacy />
    </MemoryRouter>,
  );
}

describe('Privacy page', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de');
    // Reset auth state
    useAuthStore.setState({ isAuthenticated: false });
  });

  it('renders privacy policy sections when unauthenticated (public page)', () => {
    useAuthStore.setState({ isAuthenticated: false });
    renderPrivacy();

    expect(screen.getByRole('link', { name: new RegExp(i18n.t('common.back')) })).toHaveAttribute('href', '/login');

    expect(screen.getAllByText(i18n.t('privacyPolicy.title')).length).toBeGreaterThan(0);
    expect(screen.getByText(i18n.t('privacyPolicy.sections.scope.heading'))).toBeInTheDocument();
    const calloutText1 = i18n.t('privacyPolicy.callout').replace(/^Kurzfassung:\s*|^Summary:\s*/i, '');
    expect(screen.getAllByText((content) => content.includes(calloutText1.substring(0, 15))).length).toBeGreaterThan(0);
    expect(screen.getByText(i18n.t('privacyPolicy.sections.accounts.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.infrastructure.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.protection.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.providers.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.storage.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.retention.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.responsibility.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.documentLabel'))).toBeInTheDocument();
  });


  it('renders S3 encrypted backup section', () => {
    useAuthStore.setState({ isAuthenticated: false });
    renderPrivacy();

    expect(screen.getByText(i18n.t('privacyPolicy.sections.protection.items.backups'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.providers.items.s3'))).toBeInTheDocument();
  });

  it('renders privacy policy sections when authenticated (in-app page)', () => {
    useAuthStore.setState({ isAuthenticated: true });
    renderPrivacy();

    expect(screen.getByRole('link', { name: new RegExp(i18n.t('common.back')) })).toHaveAttribute('href', '/docs');

    expect(screen.getAllByText(i18n.t('privacyPolicy.title')).length).toBeGreaterThan(0);
    expect(screen.getByText(i18n.t('privacyPolicy.sections.scope.heading'))).toBeInTheDocument();
    const calloutText2 = i18n.t('privacyPolicy.callout').replace(/^Kurzfassung:\s*|^Summary:\s*/i, '');
    expect(screen.getAllByText((content) => content.includes(calloutText2.substring(0, 15))).length).toBeGreaterThan(0);
    expect(screen.getByText(i18n.t('privacyPolicy.sections.accounts.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.infrastructure.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.protection.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.providers.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.storage.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.retention.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.responsibility.heading'))).toBeInTheDocument();
  });
});

