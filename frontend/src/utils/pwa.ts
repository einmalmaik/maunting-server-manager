// PWA Utilities - Service Worker Registration & Update Handling
import { confirm } from '@/stores/confirmStore'
import i18n from '@/i18n'

export function registerServiceWorker() {
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js')
        .then((registration) => {
          // Check for updates
          registration.addEventListener('updatefound', () => {
            const newWorker = registration.installing;
            if (newWorker) {
              newWorker.addEventListener('statechange', () => {
                if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                  // New version available
                  void confirm({
                    title: i18n.t('pwa.updateTitle'),
                    message: i18n.t('pwa.updateMessage'),
                    confirmText: i18n.t('pwa.updateConfirm'),
                    cancelText: i18n.t('pwa.updateCancel'),
                  }).then((ok) => {
                    if (ok) {
                      window.location.reload();
                    }
                  });
                }
              });
            }
          });
        })
        .catch(() => {
          // Service worker support is optional; UI remains fully usable.
        });
    });
  }
}

export function unregisterServiceWorker() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.ready.then((registration) => {
      registration.unregister();
    });
  }
}

// Check if running as PWA
export function isPWA(): boolean {
  return window.matchMedia('(display-mode: standalone)').matches ||
         (window.navigator as any).standalone === true;
}
