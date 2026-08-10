import { describe, expect, it } from 'vitest'

import i18n from '@/i18n'

/**
 * Zwei Eigenschaften, die zusammengehören und sich gegenseitig in Schach halten.
 *
 * `parseMissingKeyHandler` war einarmig: `(key) => key`. i18next ruft den Handler
 * aber **auch dann**, wenn ein `defaultValue` bereits sauber aufgelöst wurde — es
 * übergibt den Ersatztext nur als zweites Argument. Die einarmige Fassung hat ihn
 * ignoriert und damit jeden `defaultValue` der gesamten Anwendung verworfen.
 *
 * Sichtbar wurde das an einem KI-Fehler: der Chat zeigte den rohen Schlüssel
 * `ai.errors.codes.AI_TOOL_REJECTED` in einer Meldung, obwohl AiChat.tsx einen
 * zweistufigen Rückfall mitgibt und der passende deutsche Satz seit jeher in der
 * Sprachdatei stand.
 */
describe('i18n-Grundverhalten', () => {
  it('nimmt bei fehlendem Schlüssel den Ersatztext und nicht den Schlüsselnamen', () => {
    expect(i18n.t('ai.errors.codes.GIBT_ES_NICHT', { defaultValue: 'Ersatz' })).toBe('Ersatz')
  })

  it('zeigt ohne Ersatztext weiterhin den Schlüssel statt einer Leerstelle', () => {
    // Die Gegenprobe zur ersten Zusage: wer den Handler ganz entfernt, um die
    // erste zu erfüllen, bricht diese hier. Ein leeres Feld in der Oberfläche
    // wäre schlimmer als ein sichtbarer Schlüssel — es sähe nach Absicht aus.
    expect(i18n.t('gibt.es.wirklich.nicht')).toBe('gibt.es.wirklich.nicht')
  })

  it('löst den Rückfall auf, den der Chat bei einem Werkzeugfehler mitgibt', async () => {
    // Genau die Kette aus AiChat.tsx: erst der Code, dann der `message_key` des
    // Backends, dann der allgemeine Streamfehler.
    await i18n.changeLanguage('de')
    const text = i18n.t('ai.errors.codes.AI_TOOL_REJECTED', {
      defaultValue: i18n.t('ai.chat.errors.toolRejected', {
        defaultValue: i18n.t('ai.chat.errors.stream'),
      }),
    })

    expect(text).not.toMatch(/^ai\./)
    expect(text).toBe(i18n.t('ai.chat.errors.toolRejected'))
  })
})
