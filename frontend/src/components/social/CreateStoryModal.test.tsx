/**
 * Der Story-Editor verspricht nichts, was die Story nicht hält.
 *
 * Bis 29.09.2026 stand in der Vorschau „Ende-zu-Ende verschlüsselt“. Stories
 * liest das Backend aber mit; verschlüsselt sind sie nur in der Datenbank.
 * Dort muss das Bild auch verschlüsselt unter die Grenze des DIS-Sidecars
 * passen, deshalb weist der Editor zu große Bilder vor dem Senden ab.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'

const { createStory, toastError } = vi.hoisted(() => ({ createStory: vi.fn(), toastError: vi.fn() }))
vi.mock('@/api/social', () => ({ createStory }))
vi.mock('@/stores/toastStore', () => ({ toast: { error: toastError, success: vi.fn() } }))

import { CreateStoryModal } from './CreateStoryModal'

beforeAll(async () => {
  await i18n.changeLanguage('de')
})
beforeEach(() => {
  createStory.mockReset()
  toastError.mockReset()
})

describe('CreateStoryModal', () => {
  it('behauptet keine Ende-zu-Ende-Verschlüsselung', () => {
    render(<CreateStoryModal open onOpenChange={vi.fn()} onCreated={vi.fn()} />)
    expect(screen.getByText(i18n.t('social.story.create'))).toBeInTheDocument()
    expect(screen.queryByText(/Ende-zu-Ende|verschlüsselt/i)).not.toBeInTheDocument()
  })

  it('schickt kein Bild über 3 MB ab', () => {
    const riesig = 'data:image/gif;base64,' + 'A'.repeat(4 * 1024 * 1024 + 4)
    render(
      <CreateStoryModal open onOpenChange={vi.fn()} onCreated={vi.fn()} initialPhotoUrl={riesig} />,
    )
    fireEvent.click(screen.getByRole('button', { name: i18n.t('social.story.share') }))
    expect(toastError).toHaveBeenCalledWith(i18n.t('social.story.imageTooLarge'))
    expect(createStory).not.toHaveBeenCalled()
  })
})
