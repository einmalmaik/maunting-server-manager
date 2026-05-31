import { useAuthStore } from '@/stores/authStore';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowLeft } from 'lucide-react';
import { Logo } from '@/components/Logo';
import { Card } from '@/components/ui/Card';
import { LegalFooter } from '@/components/LegalFooter';

export function Privacy() {
  const { isAuthenticated } = useAuthStore();
  const navigate = useNavigate();
  const { t } = useTranslation();

  const cardContent = (
    <Card className="p-8 space-y-6">
      <section>
        <h2 className="text-xl font-semibold mb-3 text-primary">1. Grundprinzip</h2>
        <p className="text-on-surface-variant leading-relaxed">
          Der Maunting Server Manager ist nach dem Prinzip der maximalen Datensparsamkeit entwickelt. Wir speichern keine Metadaten, keine Tracking-Daten und nutzen keine Analytics-Dienste. Diese Instanz wird eigenverantwortlich gehostet.
        </p>
      </section>

      <section>
        <h2 className="text-xl font-semibold mb-3 text-primary">2. Gespeicherte Daten</h2>
        <p className="text-on-surface-variant leading-relaxed mb-2">
          Wir speichern ausschließlich die Daten, die für den Betrieb Ihres Accounts zwingend erforderlich sind:
        </p>
        <ul className="list-disc pl-5 text-on-surface-variant space-y-1">
          <li>E-Mail-Adresse (für den Account-Login)</li>
        </ul>
      </section>

      <section>
        <h2 className="text-xl font-semibold mb-3 text-primary">3. Cookies</h2>
        <p className="text-on-surface-variant leading-relaxed mb-2">
          Es werden ausschließlich technisch notwendige Cookies gesetzt:
        </p>
        <ul className="list-disc pl-5 text-on-surface-variant space-y-1">
          <li><strong>Session-Cookie</strong> – Zweck: Sitzungsverwaltung</li>
          <li><strong>CSRF-Token</strong> – Zweck: Sicherheit gegen Cross-Site-Request-Forgery</li>
          <li><strong>Auth-Cookie</strong> – Zweck: Angemeldet bleiben</li>
        </ul>
      </section>

      <section>
        <h2 className="text-xl font-semibold mb-3 text-primary">4. Weitergabe an Dritte</h2>
        <p className="text-on-surface-variant leading-relaxed">
          Es erfolgt keine Weitergabe von Daten an Dritte. Alle Daten verbleiben lokal auf dem Server dieser Instanz.
        </p>
      </section>

      <section>
        <h2 className="text-xl font-semibold mb-3 text-primary">5. Recht auf Löschung</h2>
        <p className="text-on-surface-variant leading-relaxed">
          Sie haben jederzeit das Recht, Ihren Account zu löschen. Bei einer Löschung werden alle mit Ihrem Account verknüpften personenbezogenen Daten unwiderruflich aus der Datenbank entfernt.
        </p>
      </section>
    </Card>
  );

  if (!isAuthenticated) {
    return (
      <div className="min-h-screen bg-background text-on-surface flex flex-col items-center justify-center p-margin-mobile md:p-margin-desktop relative overflow-hidden">
        <div className="absolute inset-0 msm-deep-grid opacity-50" />

        <div className="relative z-10 w-full max-w-4xl my-8">
          <div className="flex items-center justify-between mb-8">
            <button
              onClick={() => navigate(-1)}
              className="flex items-center gap-2 text-sm text-on-surface-variant hover:text-on-surface transition-colors"
            >
              <ArrowLeft className="w-4 h-4" />
              {t('common.back')}
            </button>
            <div className="flex items-center gap-3">
              <Logo size="sm" />
              <div className="text-left">
                <span className="block font-headline text-sm font-bold text-primary">MauntingStudios</span>
                <span className="block font-mono text-[10px] text-on-surface-variant leading-none">Infrastructure Control</span>
              </div>
            </div>
          </div>
          <h1 className="text-3xl font-headline font-bold mb-8">Datenschutzerklärung</h1>
          {cardContent}
          <LegalFooter className="mt-8" />
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto py-12 px-4 sm:px-6">
      <div className="mb-8 flex items-center gap-3">
        <Logo size="sm" />
        <div>
          <h1 className="text-3xl font-headline font-bold text-on-surface">Datenschutzerklärung</h1>
          <p className="font-mono text-xs text-on-surface-variant">Maunting Server Manager</p>
        </div>
      </div>
      {cardContent}
    </div>
  );
}
