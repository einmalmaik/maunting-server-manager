import { forwardRef, type InputHTMLAttributes } from 'react';
import { Check } from 'lucide-react';
import { cx } from '@/utils/classNames';

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'onChange'> {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(
  ({ className, checked, onCheckedChange, disabled, id, ...props }, ref) => {
    return (
      <label
        className={cx(
          'relative inline-flex items-center justify-center cursor-pointer select-none',
          disabled && 'cursor-not-allowed opacity-50',
          className
        )}
      >
        <input
          ref={ref}
          type="checkbox"
          checked={checked}
          onChange={(e) => !disabled && onCheckedChange(e.target.checked)}
          disabled={disabled}
          className="sr-only peer"
          id={id}
          {...props}
        />
        <span
          className={cx(
            'flex h-4 w-4 items-center justify-center rounded border transition-colors focus-ring',
            'border-outline-variant bg-surface-container-high',
            'peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-primary/50',
            checked && 'border-secondary/50 bg-secondary/20',
            disabled && 'cursor-not-allowed',
            !disabled && 'hover:border-outline-variant'
          )}
        >
          {checked && <Check className="h-3 w-3 text-secondary stroke-[3px]" />}
        </span>
      </label>
    );
  }
);
Checkbox.displayName = 'Checkbox';
