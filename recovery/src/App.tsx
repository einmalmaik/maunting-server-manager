/**
 * MSM Backup Recovery - main application component.
 *
 * Step-flow state machine (M1 foundation):
 *   input → decrypting → success | error
 *
 * The decrypt button is wired to `decryptBackup` from `src/lib/decrypt.ts`
 * (DIS: Argon2id + AES-256-GCM). The backup password lives only in React
 * state during the operation and is cleared immediately afterwards
 * (VAL-CROSS-003: never persisted, never logged). The app makes zero network
 * requests — all crypto runs locally via @msdis/shield (VAL-CROSS-002).
 */

import { useCallback, useState } from 'react';
import { LanguageProvider, useLanguage } from '@/lib/useLanguage';
import { decryptBackup, DecryptError } from '@/lib/decrypt';
import type { TranslationKey } from '@/i18n';
import { FilePicker } from '@/components/FilePicker';
import { PasswordInput } from '@/components/PasswordInput';
import { SaltInput } from '@/components/SaltInput';
import { DecryptButton } from '@/components/DecryptButton';
import { DisBadge } from '@/components/DisBadge';
import { LanguageSwitcher } from '@/components/LanguageSwitcher';
import { SuccessState } from '@/components/SuccessState';
import { ErrorState } from '@/components/ErrorState';

type Step = 'input' | 'decrypting' | 'success' | 'error';

function AppContent() {
  const { t } = useLanguage();

  const [step, setStep] = useState<Step>('input');
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileBytes, setFileBytes] = useState<Uint8Array | null>(null);
  const [password, setPassword] = useState('');
  const [salt, setSalt] = useState('');
  const [errorMsgKey, setErrorMsgKey] = useState<TranslationKey>('state.error.default');
  const [decryptedSize, setDecryptedSize] = useState(0);
  const [validationError, setValidationError] = useState<TranslationKey | null>(null);

  const handleFileSelected = useCallback((name: string, bytes: Uint8Array) => {
    setFileName(name);
    setFileBytes(bytes);
    setValidationError(null);
  }, []);

  const handleDecrypt = useCallback(async () => {
    // Validate inputs before any crypto work.
    if (!fileBytes) {
      setValidationError('validation.fileRequired');
      return;
    }
    if (!password) {
      setValidationError('validation.passwordRequired');
      return;
    }
    if (!salt) {
      setValidationError('validation.saltRequired');
      return;
    }

    setValidationError(null);
    setStep('decrypting');

    try {
      const decrypted = await decryptBackup(fileBytes, password, salt);
      setDecryptedSize(decrypted.length);
      setStep('success');
    } catch (err) {
      if (err instanceof DecryptError) {
        setErrorMsgKey('state.error.empty');
      } else {
        setErrorMsgKey('state.error.default');
      }
      setStep('error');
    } finally {
      // VAL-CROSS-003: clear the password from memory immediately after use.
      setPassword('');
    }
  }, [fileBytes, password, salt]);

  const handleRetry = useCallback(() => {
    setStep('input');
    setErrorMsgKey('state.error.default');
    setDecryptedSize(0);
    setValidationError(null);
  }, []);

  const handleReset = useCallback(() => {
    setStep('input');
    setFileName(null);
    setFileBytes(null);
    setPassword('');
    setSalt('');
    setDecryptedSize(0);
    setErrorMsgKey('state.error.default');
    setValidationError(null);
  }, []);

  return (
    <main className="mx-auto flex min-h-full max-w-2xl flex-col gap-6 bg-background p-6 text-foreground" data-testid="app-root">
      {/* Header */}
      <header className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-4">
          <h1 className="text-xl font-bold tracking-tight text-foreground">
            {t('app.title')}
          </h1>
          <LanguageSwitcher />
        </div>
        <p className="text-sm text-muted-foreground">{t('app.subtitle')}</p>
      </header>

      {/* Main card */}
      {step === 'input' || step === 'decrypting' ? (
        <section className="msm-card flex flex-col gap-5 p-6" data-testid="input-card">
          <div className="flex flex-col gap-1">
            <h2 className="text-lg font-semibold text-foreground">
              {t('step.input.heading')}
            </h2>
            <p className="text-sm text-muted-foreground">{t('step.input.description')}</p>
          </div>

          <FilePicker
            fileName={fileName}
            onFileSelected={handleFileSelected}
            disabled={step === 'decrypting'}
          />

          <PasswordInput
            value={password}
            onChange={setPassword}
            disabled={step === 'decrypting'}
          />

          <SaltInput value={salt} onChange={setSalt} disabled={step === 'decrypting'} />

          {validationError ? (
            <p
              className="text-sm text-destructive"
              role="alert"
              data-testid="validation-error"
            >
              {t(validationError)}
            </p>
          ) : null}

          <DecryptButton
            onClick={handleDecrypt}
            loading={step === 'decrypting'}
            disabled={step === 'decrypting'}
          />
        </section>
      ) : null}

      {step === 'success' ? (
        <SuccessState decryptedBytes={decryptedSize} onRetry={handleReset} />
      ) : null}

      {step === 'error' ? <ErrorState messageKey={errorMsgKey} onRetry={handleRetry} /> : null}

      {/* Footer */}
      <footer className="flex flex-col items-center gap-3 pt-2">
        <DisBadge />
        <p className="text-xs text-muted-foreground/60">{t('footer.offline')}</p>
      </footer>
    </main>
  );
}

function App() {
  return (
    <LanguageProvider>
      <AppContent />
    </LanguageProvider>
  );
}

export default App;
