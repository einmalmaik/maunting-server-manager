/**
 * Kleine UI-Grundbausteine nach MauntingStudios Design-DNA.
 *
 * Bewusst nur das, was die App heute braucht (KISS): ein Knopf in zwei
 * Stimmen, ein Eingabefeld mit Label, eine Karte. Alles über semantische
 * Theme-Klassen (styles.css) — keine rohen Farbwerte im Komponenten-Code.
 */
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";

interface KnopfProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  stimme?: "primaer" | "leise";
}

export function Knopf({ stimme = "primaer", className = "", ...rest }: KnopfProps) {
  const basis =
    "rounded-[var(--radius-control)] px-4 py-2 text-sm font-medium transition disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
  const stimmen = {
    primaer: "bg-primary text-primary-foreground hover:opacity-90 shadow-accent-cta",
    leise: "bg-secondary text-foreground hover:bg-muted border border-border",
  } as const;
  return <button className={`${basis} ${stimmen[stimme]} ${className}`} {...rest} />;
}

interface EingabeProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hinweis?: string;
}

export function Eingabe({ label, hinweis, id, className = "", ...rest }: EingabeProps) {
  const feldId = id ?? label.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  return (
    <label className="flex flex-col gap-1.5" htmlFor={feldId}>
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <input
        id={feldId}
        className={`rounded-[var(--radius-control)] border border-input bg-secondary px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${className}`}
        {...rest}
      />
      {hinweis && <span className="text-xs text-muted-foreground/80">{hinweis}</span>}
    </label>
  );
}

export function Karte({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-[var(--radius-card)] border border-border bg-card p-6 shadow-panel ${className}`}
    >
      {children}
    </div>
  );
}

export function Fehlertext({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <p role="alert" className="text-xs text-destructive">
      {text}
    </p>
  );
}
