/**
 * MSM Backup Recovery - main application component.
 *
 * Step-flow state machine (M2 full features):
 *   input → decrypting → extracting → success | error
 *
 * Full flow: select .enc → enter password → decrypt (DIS) → write tar.gz to
 * temp → extract (Rust) → file tree + preview → save.
 *
 * The decrypt button is wired to `decryptBackup` from `src/lib/decrypt.ts`
 * (DIS: Argon2id + AES-256-GCM). After decryption, the tar.gz bytes are
 * written to a temp directory via Rust commands, then extracted with
 * `extract_tar_gz`. The backup password lives only in React state during the
 * operation and is cleared immediately afterwards (VAL-CROSS-003). Temp files
 * are cleaned up on reset and on app close (VAL-CROSS-004).
 */

import { useCallback, useRef, useState } from 'react';
import { LanguageProvider, useLanguage } from '@/lib/useLanguage';
import { decryptBackup, DecryptError } from '@/lib/decrypt';
import {
  createTempDir,
  writeTempFile,
  extractTarGz,
  cleanupTempDir,
  type FileTreeNode,
} from '@/lib/tauri-commands';
import type { TranslationKey } from '@/i18n';
import { FilePicker } from '@/components/FilePicker';
import { PasswordInput } from '@/components/PasswordInput';
import { SaltInput } from '@/components/SaltInput';
import { DecryptButton } from '@/components/DecryptButton';
import { DisBadge } from '@/components/DisBadge';
import { LanguageSwitcher } from '@/components/LanguageSwitcher';
import { SuccessState } from '@/components/SuccessState';
import { ErrorState } from '@/components/ErrorState';
import { ProgressBar } from '@/components/ProgressBar';
import { BrandMark } from '@/components/BrandMark';

type Step = 'input' | 'decrypting' | 'extracting' | 'success' | 'error';

/** Maps an error to the most specific German i18n key. */
function classifyError(err: unknown): TranslationKey {
  if (err instanceof DecryptError) {
    return 'state.error.empty';
  }
  const msg = err instanceof Error ? err.message : String(err);
  if (msg.includes('Invalid frame format')) {
    return 'state.error.corruptFrame';
  }
  if (msg.includes('Entpacken fehlgeschlagen') || msg.includes('nicht lesbar')) {
    return 'state.error.extraction';
  }
  return 'state.error.default';
}

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
  const [fileTree, setFileTree] = useState<FileTreeNode | null>(null);
  // Progress feedback for the decrypt/extract steps (null = indeterminate).
  const [progress, setProgress] = useState<number | null>(null);
  const [progressLabel, setProgressLabel] = useState<string | undefined>(undefined);

  // Track temp dir for cleanup (VAL-CROSS-004). Use a ref so it persists
  // across re-renders without triggering re-render on assignment.
  const tempDirRef = useRef<string | null>(null);

  /** Clean up temp directory if one was created. */
  const cleanupTemp = useCallback(async () => {
    const dir = tempDirRef.current;
    if (dir) {
      try {
        await cleanupTempDir(dir);
      } catch {
        // Best-effort: the Rust exit handler is a safety net.
      }
      tempDirRef.current = null;
    }
  }, []);

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
    setProgress(null);
    setProgressLabel(undefined);
    setStep('decrypting');

    try {
      // 1. Decrypt with DIS (Argon2id + AES-256-GCM)
      const decrypted = await decryptBackup(fileBytes, password, salt, (update) => {
        if (update.stage === 'deriving') {
          setProgressLabel(t('progress.deriving'));
        } else {
          setProgressLabel(t('progress.decrypting'));
        }
        setProgress(update.progress);
      });
      setDecryptedSize(decrypted.length);

      // 2. Create temp dir and write the decrypted tar.gz
      setProgressLabel(t('progress.extracting'));
      setProgress(null); // extracting is indeterminate
      setStep('extracting');
      const tempDir = await createTempDir();
      tempDirRef.current = tempDir;
      const tarGzPath = await writeTempFile(tempDir, 'backup.tar.gz', decrypted);

      // 3. Extract the tar.gz to a subdirectory
      const extractDir = `${tempDir}\\extracted`;
      const tree = await extractTarGz(tarGzPath, extractDir);
      setFileTree(tree);
      setStep('success');
    } catch (err) {
      // Clean up any temp files created before the failure
      await cleanupTemp();
      setErrorMsgKey(classifyError(err));
      setStep('error');
    } finally {
      // VAL-CROSS-003: clear the password from memory immediately after use.
      setPassword('');
    }
  }, [fileBytes, password, salt, cleanupTemp, t]);

  const handleRetry = useCallback(() => {
    setStep('input');
    setErrorMsgKey('state.error.default');
    setDecryptedSize(0);
    setValidationError(null);
    setFileTree(null);
    setProgress(null);
    setProgressLabel(undefined);
  }, []);

  const handleReset = useCallback(async () => {
    // Clean up temp files from the previous session (VAL-CROSS-004)
    await cleanupTemp();
    setStep('input');
    setFileName(null);
    setFileBytes(null);
    setPassword('');
    setSalt('');
    setDecryptedSize(0);
    setErrorMsgKey('state.error.default');
    setValidationError(null);
    setFileTree(null);
    setProgress(null);
    setProgressLabel(undefined);
  }, [cleanupTemp]);

  return (
    <main
      className="mx-auto flex min-h-full max-w-3xl flex-col gap-6 bg-background p-6 text-foreground"
      data-testid="app-root"
    >
      {/* Header */}
      <header className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <BrandMark />
            <div className="flex flex-col">
              <h1 className="text-xl font-bold tracking-tight text-foreground">
                {t('app.title')}
              </h1>
              <p className="text-xs text-muted-foreground/60">{t('app.subtitle')}</p>
            </div>
          </div>
          <LanguageSwitcher />
        </div>
      </header>

      {/* Main card */}
      {step === 'input' || step === 'decrypting' || step === 'extracting' ? (
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
            disabled={step !== 'input'}
          />

          <PasswordInput
            value={password}
            onChange={setPassword}
            disabled={step !== 'input'}
          />

          <SaltInput value={salt} onChange={setSalt} disabled={step !== 'input'} />

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
            loading={step === 'decrypting' || step === 'extracting'}
            disabled={step !== 'input'}
          />

          {step === 'decrypting' ? (
            <ProgressBar label={progressLabel} progress={progress ?? undefined} />
          ) : null}
          {step === 'extracting' ? <ProgressBar label={t('progress.extracting')} /> : null}
        </section>
      ) : null}

      {step === 'success' && fileTree ? (
        <SuccessState
          decryptedBytes={decryptedSize}
          fileTree={fileTree}
          extractedDir={tempDirRef.current ? `${tempDirRef.current}\\extracted` : ''}
          onRetry={handleReset}
        />
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
