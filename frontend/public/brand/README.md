# Brand Media Assets

These files are rendered by `BrandMedia` with a static PNG fallback first.

## Landing hero

- Static fallback: `landingpage.png`
- Optional animated image: `landingpage.gif`
- Optional videos: `landingpage.webm`, `landingpage.mp4`

## Auth panel

- Static fallback: `auth-panel.png`
- Optional animated image: `auth-panel.gif`
- Optional videos: `auth-panel.webm`, `auth-panel.mp4`

If multiple animation files exist, videos are preferred over GIF once the
browser can play them. Keep all files same-origin under this directory. Videos
must be muted/loop-safe background media and should avoid critical text near the
edges because the UI uses `object-fit: cover`.
