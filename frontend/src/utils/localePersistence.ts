export function isLocalePersistenceAllowed(): boolean {
  const consentRaw = localStorage.getItem('cookie_consent');
  if (!consentRaw) return false;
  try {
    const consent = JSON.parse(consentRaw);
    return consent.optional === true;
  } catch {
    return false;
  }
}

export function getPersistedLocale(): string | null {
  if (!isLocalePersistenceAllowed()) {
    return null;
  }
  return localStorage.getItem('i18nextLng');
}

export function setPersistedLocale(locale: string): void {
  if (isLocalePersistenceAllowed()) {
    localStorage.setItem('i18nextLng', locale);
  } else {
    localStorage.removeItem('i18nextLng');
  }
}

export function clearPersistedLocale(): void {
  localStorage.removeItem('i18nextLng');
}
