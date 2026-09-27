import '@testing-library/jest-dom'
import i18n, { textBereit } from '@/i18n'

// Die Sprachdateien laden seit 27.09.2026 nach (`config/locales.ts`). Ein Test,
// der sofort rendert oder die Sprache wechselt, sähe sonst Schlüssel statt Text.
await textBereit
await i18n.loadLanguages(['de', 'en'])
