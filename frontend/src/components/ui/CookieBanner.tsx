import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { X } from 'lucide-react';
import { Button } from './Button';

export function CookieBanner() {
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    // Check if user already accepted
    const accepted = localStorage.getItem('cookie_accepted');
    if (!accepted) {
      setIsVisible(true);
    }
  }, []);

  const handleAccept = () => {
    localStorage.setItem('cookie_accepted', '1');
    setIsVisible(false);
  };

  if (!isVisible) return null;

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm pointer-events-none transition-opacity duration-300" />
      
      {/* Banner */}
      <div role="dialog" className="fixed bottom-4 left-4 right-4 z-50 flex justify-center">
        <div className="w-full max-w-2xl rounded-xl border border-border bg-surface-container-high/95 backdrop-blur-xl shadow-panel p-4 sm:p-5 flex flex-col gap-4">
          <div className="flex justify-between items-start">
            <h3 className="text-lg font-headline font-semibold text-foreground">Datenschutz & Cookies</h3>
            <button onClick={handleAccept} className="text-muted-foreground hover:text-foreground">
              <X className="h-5 w-5" />
            </button>
          </div>
          
          <div className="text-sm text-muted-foreground leading-relaxed space-y-2">
            <p>
              Diese Anwendung legt größten Wert auf Datensparsamkeit. Wir setzen keine Tracking- oder Analytics-Cookies ein. Es werden lediglich technisch notwendige Cookies gespeichert:
            </p>
            <ul className="list-disc pl-5 space-y-1 text-on-surface-variant">
              <li><strong>Session-Cookie</strong> – Zweck: Sitzungsverwaltung</li>
              <li><strong>CSRF-Token</strong> – Zweck: Sicherheit gegen Cross-Site-Request-Forgery</li>
              <li><strong>Auth-Cookie</strong> – Zweck: Angemeldet bleiben</li>
            </ul>
            <p className="pt-2">
              Weitere Details findest du in unserer{' '}
              <Link to="/privacy" className="text-primary hover:underline">Datenschutzerklärung</Link>.
            </p>
          </div>
          
          <div className="flex justify-end pt-2">
            <Button onClick={handleAccept} variant="primary">
              Verstanden
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}
