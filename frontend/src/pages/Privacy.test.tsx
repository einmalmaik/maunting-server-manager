import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Privacy } from './Privacy';
import { useAuthStore } from '@/stores/authStore';

function renderPrivacy() {
  return render(
    <MemoryRouter>
      <Privacy />
    </MemoryRouter>,
  );
}

describe('Privacy page', () => {
  beforeEach(() => {
    // Reset auth state
    useAuthStore.setState({ isAuthenticated: false });
  });

  it('renders privacy policy sections when unauthenticated (public page)', () => {
    useAuthStore.setState({ isAuthenticated: false });
    renderPrivacy();

    expect(screen.getByRole('link', { name: /Zurück|Back/ })).toHaveAttribute('href', '/login');

    expect(screen.getAllByText('Datenschutzerklärung').length).toBeGreaterThan(0);
    expect(screen.getByText('1. Grundprinzip')).toBeInTheDocument();
    expect(screen.getAllByText((content) => content.includes('Datensparsamkeit')).length).toBeGreaterThan(0);
    expect(screen.getByText('2. Gespeicherte Daten')).toBeInTheDocument();
    expect(screen.getByText('3. Cookies und lokale Speicherung')).toBeInTheDocument();
    expect(screen.getByText('4. Weitergabe an Dritte')).toBeInTheDocument();
    expect(screen.getByText('5. Recht auf Löschung')).toBeInTheDocument();
    expect(screen.getByText('MSM Legal')).toBeInTheDocument();
  });

  it('renders S3 encrypted backup section with zero-knowledge emphasis', () => {
    useAuthStore.setState({ isAuthenticated: false });
    renderPrivacy();

    expect(screen.getByText('6. Verschlüsselte Cloud-Backups (S3)')).toBeInTheDocument();
    // Zero-knowledge emphasis: provider cannot read data
    expect(screen.getAllByText((c) => c.includes('Zero-Knowledge')).length).toBeGreaterThan(0);
    expect(screen.getAllByText((c) => c.includes('kann die Backup-Inhalte nicht lesen')).length).toBeGreaterThan(0);
    // Encrypted before upload
    expect(screen.getAllByText((c) => c.includes('clientseitig verschlüsselt')).length).toBeGreaterThan(0);
  });

  it('renders privacy policy sections when authenticated (in-app page)', () => {
    useAuthStore.setState({ isAuthenticated: true });
    renderPrivacy();

    expect(screen.getByRole('link', { name: /Zurück|Back/ })).toHaveAttribute('href', '/docs');

    expect(screen.getAllByText('Datenschutzerklärung').length).toBeGreaterThan(0);
    expect(screen.getByText('1. Grundprinzip')).toBeInTheDocument();
    expect(screen.getAllByText((content) => content.includes('Datensparsamkeit')).length).toBeGreaterThan(0);
    expect(screen.getByText('2. Gespeicherte Daten')).toBeInTheDocument();
    expect(screen.getByText('3. Cookies und lokale Speicherung')).toBeInTheDocument();
    expect(screen.getByText('4. Weitergabe an Dritte')).toBeInTheDocument();
    expect(screen.getByText('5. Recht auf Löschung')).toBeInTheDocument();
  });
});
