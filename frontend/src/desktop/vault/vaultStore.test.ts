import { describe, it, expect, beforeEach } from 'vitest'
import { useVaultStore } from './vaultStore'

describe('useVaultStore - Auto-Lock & Biometrics', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    useVaultStore.setState({
      isUnlocked: false,
      userKey: null,
      items: [],
      autoLockMinutes: 15,
      lockOnWindowBlur: false,
      isBiometricsEnabled: false,
      lastActivityTime: Date.now(),
    })
  })

  it('updates autoLockMinutes and persists to localStorage', () => {
    const store = useVaultStore.getState()
    store.setAutoLockMinutes(30)
    expect(useVaultStore.getState().autoLockMinutes).toBe(30)
    expect(localStorage.getItem('mss:vault_autolock_minutes')).toBe('30')
  })

  it('updates lockOnWindowBlur and persists to localStorage', () => {
    const store = useVaultStore.getState()
    store.setLockOnWindowBlur(true)
    expect(useVaultStore.getState().lockOnWindowBlur).toBe(true)
    expect(localStorage.getItem('mss:vault_lock_on_blur')).toBe('true')
  })

  it('records activity and updates lastActivityTime', () => {
    const past = Date.now() - 10000
    useVaultStore.setState({ lastActivityTime: past })
    useVaultStore.getState().recordActivity()
    expect(useVaultStore.getState().lastActivityTime).toBeGreaterThanOrEqual(past + 5000)
  })

  it('auto-locks when inactivity exceeds autoLockMinutes', () => {
    const fakeKey = {} as CryptoKey
    useVaultStore.setState({
      isUnlocked: true,
      userKey: fakeKey,
      items: [{ id: '1', service: 'Test', username: '', password: 'abc', createdAt: 1, updatedAt: 1, revision: 1 }],
      autoLockMinutes: 10,
      lastActivityTime: Date.now() - 11 * 60 * 1000, // 11 minutes ago
    })

    const locked = useVaultStore.getState().checkAutoLock()
    expect(locked).toBe(true)

    const state = useVaultStore.getState()
    expect(state.isUnlocked).toBe(false)
    expect(state.userKey).toBeNull()
    expect(state.items).toHaveLength(0)
  })

  it('does not auto-lock when within autoLockMinutes threshold', () => {
    const fakeKey = {} as CryptoKey
    useVaultStore.setState({
      isUnlocked: true,
      userKey: fakeKey,
      autoLockMinutes: 15,
      lastActivityTime: Date.now() - 5 * 60 * 1000, // 5 minutes ago
    })

    const locked = useVaultStore.getState().checkAutoLock()
    expect(locked).toBe(false)

    const state = useVaultStore.getState()
    expect(state.isUnlocked).toBe(true)
    expect(state.userKey).toBe(fakeKey)
  })

  it('disables biometrics and removes local envelope', async () => {
    localStorage.setItem('mss:vault_bio_wrapped', 'some_envelope')
    localStorage.setItem('mss:vault_biometrics_enabled', 'true')
    useVaultStore.setState({ isBiometricsEnabled: true })

    await useVaultStore.getState().disableBiometrics()

    expect(useVaultStore.getState().isBiometricsEnabled).toBe(false)
    expect(localStorage.getItem('mss:vault_bio_wrapped')).toBeNull()
    expect(localStorage.getItem('mss:vault_biometrics_enabled')).toBe('false')
  })
})
