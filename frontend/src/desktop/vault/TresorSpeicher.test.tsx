import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import i18n from '@/i18n'
import { TresorSpeicherAnzeige } from './TresorSpeicher'

describe('TresorSpeicherAnzeige', () => {
  it('nennt für den Owner keine Grenze statt „1024 TB“ und zeigt keinen Balken', () => {
    // Das Backend meldet „ohne Grenze“ als MAX_QUOTE = 1 PiB. Im Emulator stand
    // bis 02.10.2026 „26.5 MB of 1024.00 TB“.
    render(<TresorSpeicherAnzeige speicher={{ belegt: 26.5 * 1024 * 1024, quote: 1024 ** 5, in_loeschung: 0, blobs: 3 }} />)
    expect(screen.getByText(i18n.t('mss.vault.dateien.speicherOhneGrenze', { belegt: '26.5 MB' }))).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).toBeNull()
    expect(document.body.textContent).not.toMatch(/TB/)
  })

  it('zeigt eine echte Grenze als Balken', () => {
    render(<TresorSpeicherAnzeige speicher={{ belegt: 1024 ** 3, quote: 10 * 1024 ** 3, in_loeschung: 0, blobs: 3 }} />)
    expect(screen.getByRole('progressbar')).toBeInTheDocument()
  })
})
