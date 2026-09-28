import { forwardRef, type InputHTMLAttributes } from 'react';
import { Check } from 'lucide-react';
import { cx } from '@/utils/classNames';

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'onChange'> {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}

/**
 * Ein Kästchen, drei Bauformen — so war es bis 09/2026: diese Komponente in
 * Mint, 28 nackte `type="checkbox"` im Browser-Grau mit cyanem `accent-color`,
 * und eine handgebaute dritte in den Gruppenrechten. Alle zeigen jetzt
 * dasselbe `primary`.
 *
 * Die Hülle ist bewusst ein `<span>` und kein `<label>`: fast jede Fundstelle
 * steht schon in einem `<label>`, und zwei ineinander sind nicht nur ungültig,
 * sie schalten in manchen Browsern zweimal — also gar nicht. Das durchsichtige
 * `<input>` liegt stattdessen über dem gezeichneten Kästchen und nimmt den
 * Klick selbst entgegen.
 */
export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(
  ({ className, checked, onCheckedChange, disabled, id, ...props }, ref) => {
    return (
      <span
        className={cx(
          'relative inline-flex h-4 w-4 shrink-0 align-middle',
          disabled && 'opacity-50',
          className
        )}
      >
        <input
          ref={ref}
          type="checkbox"
          checked={checked}
          onChange={(e) => !disabled && onCheckedChange(e.target.checked)}
          disabled={disabled}
          id={id}
          className="peer absolute inset-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed"
          {...props}
        />
        <span
          aria-hidden="true"
          className={cx(
            'pointer-events-none flex h-4 w-4 items-center justify-center rounded border transition-colors',
            'border-outline-variant bg-surface-container-high',
            'peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-primary/50',
            checked && 'border-primary/50 bg-primary/20'
          )}
        >
          {checked && <Check className="h-3 w-3 text-primary stroke-[3px]" />}
        </span>
      </span>
    );
  }
);
Checkbox.displayName = 'Checkbox';
