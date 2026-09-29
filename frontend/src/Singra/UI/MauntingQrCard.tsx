import { useEffect, useState } from 'react'
import QRCode from 'qrcode'
import firmenLogo from '@/desktop/assets/firmen-logo.png'

export interface MauntingQrCardProps {
  /** Der Inhalt, der im QR-Code kodiert werden soll (z. B. TOTP-URI oder Kopplungscode). */
  value: string
  /** Optional vorgefertigte Data-URI als Fallback. */
  qrDataUri?: string | null
  /** Alternativtext für das Bild. */
  alt?: string
  /** Hinweis oder Scan-Anleitung unter dem Code. */
  hint?: string
  /** Optionaler Titel über dem Code. */
  title?: string
  /** Größe des Codes. Default ist 'md'. */
  size?: 'sm' | 'md' | 'lg'
  /** Ob das zentrale Firmen-Emblem eingebettet werden soll. Default ist true. */
  showLogo?: boolean
  /** Zusätzliche CSS-Klassen für den äußeren Container. */
  className?: string
}

const SIZE_MAP = {
  sm: {
    container: 'h-44 w-44 sm:h-48 sm:w-48',
    logo: 'h-8 w-8 sm:h-9 sm:w-9',
  },
  md: {
    container: 'h-56 w-56 sm:h-64 sm:w-64',
    logo: 'h-10 w-10 sm:h-11 sm:w-11',
  },
  lg: {
    container: 'h-64 w-64 sm:h-72 sm:w-72',
    logo: 'h-12 w-12 sm:h-14 sm:w-14',
  },
}

/**
 * Signatur-QR-Code der Maunting Design-DNA.
 *
 * Rendert einen hochauflösenden Level-H QR-Code (30 % Fehlertoleranz) mit
 * minimalem Rand, sanfter Schattierung, glühendem Cyan/Primär-Ring und
 * zentriertem MauntingStudios-Markenemblem.
 */
export function MauntingQrCard({
  value,
  qrDataUri,
  alt = 'QR-Code',
  hint,
  title,
  size = 'md',
  showLogo = true,
  className = '',
}: MauntingQrCardProps) {
  const [generiertesQr, setGeneriertesQr] = useState<string | null>(qrDataUri || null)

  useEffect(() => {
    // Das Erzeugen ist asynchron; nach dem Aushängen darf es keinen State mehr setzen.
    let aktiv = true
    if (qrDataUri) {
      setGeneriertesQr(qrDataUri)
    } else if (value) {
      QRCode.toDataURL(value.trim(), {
        errorCorrectionLevel: 'H', // 30% Fehlertoleranz für das zentrierte Logo
        margin: 1, // Minimaler Rand: QR-Code füllt maximalen Raum aus
        scale: 12,
        color: {
          dark: '#020617',
          light: '#ffffff',
        },
      })
        .then((url) => {
          if (aktiv) setGeneriertesQr(url)
        })
        .catch(() => {
          if (aktiv) setGeneriertesQr(null)
        })
    }
    return () => {
      aktiv = false
    }
  }, [value, qrDataUri])

  if (!generiertesQr) return null

  const sizeStyle = SIZE_MAP[size] || SIZE_MAP.md

  return (
    <div
      className={`flex flex-col items-center justify-center gap-3 rounded-2xl bg-surface-container-lowest/80 p-4 sm:p-5 border border-primary/25 shadow-[0_0_35px_rgba(56,189,248,0.12)] ${className}`}
    >
      {title && (
        <p className="text-sm font-semibold text-on-surface text-center mb-1">
          {title}
        </p>
      )}

      <div className="relative inline-flex items-center justify-center rounded-2xl bg-white p-2 sm:p-2.5 shadow-xl ring-4 ring-primary/20">
        <img
          src={generiertesQr}
          alt={alt}
          className={`${sizeStyle.container} object-contain rounded-lg`}
          draggable={false}
        />
        {showLogo && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <div
              className={`flex ${sizeStyle.logo} items-center justify-center rounded-xl bg-surface-container-lowest p-1 shadow-2xl border-2 border-white ring-2 ring-primary/60`}
            >
              <img
                src={firmenLogo}
                alt="MauntingStudios"
                className="h-full w-full object-contain rounded-lg"
                draggable={false}
              />
            </div>
          </div>
        )}
      </div>

      {hint && (
        <p className="text-center text-xs font-medium text-on-surface-variant max-w-sm">
          {hint}
        </p>
      )}
    </div>
  )
}
