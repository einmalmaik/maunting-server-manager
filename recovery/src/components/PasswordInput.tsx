/**
 * PasswordInput - masked backup password entry.
 *
 * Uses `type="password"` so characters are never shown in clear text
 * (VAL-UI-003). The value lives only in the parent's React state (memory)
 * and is never logged, persisted, or written to storage.
 */

import { useLanguage } from '@/lib/useLanguage';

export interface PasswordInputProps {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}

export function PasswordInput({ value, onChange, disabled = false }: PasswordInputProps) {
  const { t } = useLanguage();

  return (
    <div className="flex flex-col gap-2" data-testid="password-input">
      <label className="text-sm font-medium text-foreground" htmlFor="backup-password">
        {t('password.label')}
      </label>
      <input
        id="backup-password"
        type="password"
        className="msm-input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={t('password.placeholder')}
        disabled={disabled}
        autoComplete="off"
        spellCheck={false}
        data-testid="password-field"
      />
      <p className="text-xs text-muted-foreground/70">{t('password.hint')}</p>
    </div>
  );
}
