import { Card } from '../components/ui/Card';

export function Privacy() {
  return (
    <div className="max-w-4xl mx-auto py-12 px-4 sm:px-6">
      <h1 className="text-3xl font-headline font-bold mb-8">Datenschutzerklärung</h1>
      
      <Card className="p-8 space-y-6 bg-surface-container-high border-border">
        <section>
          <h2 className="text-xl font-semibold mb-3 text-foreground">1. Grundprinzip</h2>
          <p className="text-on-surface-variant leading-relaxed">
            Der Maunting Server Manager ist nach dem Prinzip der maximalen Datensparsamkeit entwickelt. Wir speichern keine Metadaten, keine Tracking-Daten und nutzen keine Analytics-Dienste. Diese Instanz wird eigenverantwortlich gehostet.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3 text-foreground">2. Gespeicherte Daten</h2>
          <p className="text-on-surface-variant leading-relaxed mb-2">
            Wir speichern ausschließlich die Daten, die für den Betrieb Ihres Accounts zwingend erforderlich sind:
          </p>
          <ul className="list-disc pl-5 text-on-surface-variant space-y-1">
            <li>E-Mail-Adresse (für den Account-Login)</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3 text-foreground">3. Cookies</h2>
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
          <h2 className="text-xl font-semibold mb-3 text-foreground">4. Weitergabe an Dritte</h2>
          <p className="text-on-surface-variant leading-relaxed">
            Es erfolgt keine Weitergabe von Daten an Dritte. Alle Daten verbleiben lokal auf dem Server dieser Instanz.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3 text-foreground">5. Recht auf Löschung</h2>
          <p className="text-on-surface-variant leading-relaxed">
            Sie haben jederzeit das Recht, Ihren Account zu löschen. Bei einer Löschung werden alle mit Ihrem Account verknüpften personenbezogenen Daten unwiderruflich aus der Datenbank entfernt.
          </p>
        </section>
      </Card>
    </div>
  );
}
