import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { StackedProgressBar } from './ProgressBar'

describe('StackedProgressBar', () => {
  it('liest die Abschnitte vor, statt sie im title zu verstecken', () => {
    // Bis 02.10.2026 standen die Abschnitte nur als title an den Segmenten.
    const { container } = render(
      <StackedProgressBar
        label="Disk"
        segments={[
          { value: 2, colorClass: 'bg-primary', label: 'Panel: 2 GB' },
          { value: 6, colorClass: 'bg-outline', label: 'System: 6 GB' },
          { value: 2, colorClass: 'bg-status-success' },
        ]}
      />,
    )
    const balken = screen.getByRole('progressbar', { name: 'Disk' })
    expect(balken).toHaveAttribute('aria-valuetext', 'Panel: 2 GB, System: 6 GB')
    expect(container.querySelectorAll('[title]')).toHaveLength(0)
  })
})
