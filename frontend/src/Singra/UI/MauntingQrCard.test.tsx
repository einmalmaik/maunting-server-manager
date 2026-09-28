import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MauntingQrCard } from './MauntingQrCard'

describe('MauntingQrCard', () => {
  it('renders with title and hint and generates QR code', async () => {
    render(
      <MauntingQrCard
        value="otpauth://totp/MSM:user@example.com?secret=JBSWY3DPEHPK3PXP"
        title="Scanne den QR-Code"
        hint="Nutze deinen Authenticator"
        alt="Mein QR-Code"
      />,
    )

    // Wait for the QR image to be generated and rendered
    await waitFor(() => {
      expect(screen.getByText('Scanne den QR-Code')).toBeInTheDocument()
      expect(screen.getByText('Nutze deinen Authenticator')).toBeInTheDocument()
      const img = screen.getByRole('img', { name: 'Mein QR-Code' })
      expect(img).toBeInTheDocument()
      expect(img.getAttribute('src')).toMatch(/^data:image\/png;base64,/)
    })
  })

  it('renders provided qrDataUri directly', () => {
    const customUri = 'data:image/png;base64,fakecustomqrdata'
    render(
      <MauntingQrCard
        value="custom-value"
        qrDataUri={customUri}
        title="Custom QR"
        alt="Custom QR"
      />,
    )

    const img = screen.getByRole('img', { name: 'Custom QR' })
    expect(img).toBeInTheDocument()
    expect(img.getAttribute('src')).toBe(customUri)
    expect(screen.getByText('Custom QR')).toBeInTheDocument()
  })
})
