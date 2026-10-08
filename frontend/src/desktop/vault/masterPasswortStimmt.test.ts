import { beforeEach, describe, expect, it, vi } from 'vitest'

// Argon2 ist hier nicht Gegenstand: das richtige Passwort ergibt den Bucket des offenen Tresors.
vi.mock('./vaultCrypto', async (original) => ({
  ...(await original<typeof import('./vaultCrypto')>()),
  deriveVaultKeys: async (passwort: string) => ({ bucketId: passwort === 'richtig' ? 'bucket-1' : 'anderer', userKey: {}, bucketAuthToken: '' }),
}))

const { BESTAETIGEN_FEHLVERSUCHE, useVaultStore } = await import('./vaultStore')

describe('Master-Passwort zur Bestätigung', () => {
  beforeEach(() => {
    localStorage.setItem('mss:vault_salt', '00'.repeat(16))
    useVaultStore.setState({ isUnlocked: true, bucketId: 'bucket-1' })
  })

  it('stimmt nur, wenn es den Bucket des offenen Tresors ergibt', async () => {
    const tresor = useVaultStore.getState()
    expect(await tresor.masterPasswortStimmt('richtig')).toBe(true)
    expect(await tresor.masterPasswortStimmt('falsch')).toBe(false)
  })

  it('sperrt den Tresor nach zu vielen falschen Versuchen', async () => {
    const sperren = vi.fn(() => useVaultStore.setState({ isUnlocked: false, bucketId: null }))
    useVaultStore.setState({ lock: sperren })
    // Ein richtiger Versuch setzt den Zähler zurück.
    await useVaultStore.getState().masterPasswortStimmt('falsch')
    expect(await useVaultStore.getState().masterPasswortStimmt('richtig')).toBe(true)
    for (let i = 0; i < BESTAETIGEN_FEHLVERSUCHE - 1; i++) expect(await useVaultStore.getState().masterPasswortStimmt('falsch')).toBe(false)
    expect(sperren).not.toHaveBeenCalled()
    expect(await useVaultStore.getState().masterPasswortStimmt('falsch')).toBe(false)
    expect(sperren).toHaveBeenCalledTimes(1)
    // Gesperrt hilft auch das richtige Passwort nicht mehr.
    expect(await useVaultStore.getState().masterPasswortStimmt('richtig')).toBe(false)
  })
})
