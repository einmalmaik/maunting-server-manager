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

    // Check header elements
    expect(screen.getByText('MauntingStudios')).toBeInTheDocument();
    expect(screen.getByText('Infrastructure Control')).toBeInTheDocument();
    
    // Check back button (German localization is default or fallback)
    expect(screen.getByRole('button')).toHaveTextContent(/Zurück|Back/);

    // Check main title and content
    expect(screen.getByText('Datenschutzerklärung')).toBeInTheDocument();
    expect(screen.getByText('1. Grundprinzip')).toBeInTheDocument();
    expect(screen.getByText((content) => content.includes('Datensparsamkeit'))).toBeInTheDocument();
    expect(screen.getByText('2. Gespeicherte Daten')).toBeInTheDocument();
    expect(screen.getByText('3. Cookies')).toBeInTheDocument();
    expect(screen.getByText('4. Weitergabe an Dritte')).toBeInTheDocument();
    expect(screen.getByText('5. Recht auf Löschung')).toBeInTheDocument();
  });

  it('renders privacy policy sections when authenticated (in-app page)', () => {
    useAuthStore.setState({ isAuthenticated: true });
    renderPrivacy();

    // In-app layout should NOT have the back button or standard public header
    expect(screen.queryByText('Infrastructure Control')).toBeNull();
    expect(screen.queryByRole('button')).toBeNull();

    // Check main title and content are still loaded correctly
    expect(screen.getByText('Datenschutzerklärung')).toBeInTheDocument();
    expect(screen.getByText('1. Grundprinzip')).toBeInTheDocument();
    expect(screen.getByText((content) => content.includes('Datensparsamkeit'))).toBeInTheDocument();
    expect(screen.getByText('2. Gespeicherte Daten')).toBeInTheDocument();
    expect(screen.getByText('3. Cookies')).toBeInTheDocument();
    expect(screen.getByText('4. Weitergabe an Dritte')).toBeInTheDocument();
    expect(screen.getByText('5. Recht auf Löschung')).toBeInTheDocument();
  });
});
