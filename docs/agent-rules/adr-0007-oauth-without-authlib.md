# ADR-0007: OAuth/OIDC-Implementation ohne `authlib`

Status: Accepted
Date: 2026-06-03

## Context
Phase 4 fuehrt Social-Login (Google, Discord, GitHub, Microsoft, Twitter/X, Custom OIDC, Custom OAuth2) ein. Im Plan stand zunaechst `authlib` als OAuth-Library. Nach KISS-Pruefung stellt sich die Frage, ob die Library den Aufwand rechtfertigt.

## Decision
Wir implementieren den OAuth2/OIDC-Flow **ohne** `authlib` — ausschliesslich mit `httpx` (bereits im Stack) und ~150 Zeilen eigenem Code in `services/oauth_service.py`.

## Begruendung
- **KISS**: Der OAuth2-Authorization-Code-Flow mit PKCE ist ~150 Zeilen Code. Die Vorteile von `authlib` (JWKS-Cache, automatische Retry-Logik, mehrere Grant-Types) brauchen wir in Phase 4 nicht.
- **Supply-Chain**: `authlib` waere ein zusaetzlicher High-Trust-Pfad auf einem Auth-Endpoint. Jedes Update waere ein Sicherheits-Risiko.
- **Transparenz**: Der eigene Code liest sich linear durch — Reviewer sehen exakt, was passiert. `authlib` abstrahiert Teile, die bei einem Audit schwer zu verifizieren sind.
- **Coverage**: Wir testen 96 Testfaelle, die den vollstaendigen Flow (PKCE, State-Validierung, Token-Exchange, User-Resolution, 2FA-Gate) abdecken.

## Konsequenzen
- **Kein ID-Token-JWKS-Validierung**: Wir akzeptieren ID-Token-Claims und kreuzpruefen via /userinfo. Ein signierter Token allein reicht uns nicht; das Cross-Check mit dem Userinfo-Endpoint (das den Auth-Header-Bearer-Token verifiziert) ist die strengere Pruefung. **Phase-2-Material**: Echte JWKS-Validierung mit Key-Rotation.
- **Manuelle PKCE-Implementierung**: ~20 Zeilen (S256-Challenge = base64url(sha256(verifier))). Ausreichend.
- **OIDC-Discovery**: Manuell via `httpx.get(issuer + "/.well-known/openid-configuration")`. ~5 Zeilen.

## Alternativen
- `authlib`: Wuerde ~80 LoC sparen, aber +1 Dependency im Auth-Pfad. Verworfen.
- `requests-oauthlib`: Aelter, weniger aktiv maintained als authlib. Verworfen.

## Review
Diese ADR ist zu reviewen, sobald:
- Wir > 1 weitere OAuth-Provider-Preset brauchen
- Wir SAML oder WS-Federation unterstuetzen sollen
- Ein IdP regelmaessig JWKS-Rotation durchfuehrt und unsere User sich beschweren
