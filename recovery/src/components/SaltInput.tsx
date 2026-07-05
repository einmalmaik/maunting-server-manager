/**
 * SaltInput - base64 salt entry with a hint telling the user where to find it.
 *
 * The salt is NOT sensitive (it is stored in MSM `panel_settings` under
 * `backup.salt`), so the input uses `type="text"` (not password). The hint
 * text explicitly references `panel_settings` / `backup.salt` so users know
 * where to look (VAL-UI-004).
 */

import { useLanguage } from '@/lib/useLanguage';

export interface SaltInputProps {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}

export function SaltInput({ value, onChange, disabled = false }: SaltInputProps) {
  const { t } = useLanguage();

  return (
    <div className="flex flex-col gap-2" data-testid="salt-input">
      <label className="text-sm font-medium text-foreground" htmlFor="backup-salt">
        {t('salt.label')}
      </label>
      <input
        id="backup-salt"
        type="text"
        className="msm-input font-mono text-sm"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={t('salt.placeholder')}
        disabled={disabled}
        autoComplete="off"
        spellCheck={false}
        data-testid="salt-field"
      />
      <p className="text-xs text-muted-foreground/70" data-testid="salt-hint">
        {t('salt.hint')}
      </p>
    </div>
  );
}
