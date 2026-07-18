import httpx
from fastapi import HTTPException
import logging

from services.panel_settings_service import PanelSettingsService

logger = logging.getLogger(__name__)


class CaptchaService:
    @classmethod
    async def verify_token(cls, token: str | None, client_ip: str | None = None) -> None:
        """Validates the CAPTCHA token generically based on active provider configuration.
        
        Raises HTTPException(400) if validation fails.
        """
        # 1. Check if CAPTCHA is enabled
        enabled = PanelSettingsService.get("captcha_enabled", "false") == "true"
        if not enabled:
            return

        provider = PanelSettingsService.get("captcha_provider", "none")
        verify_urls = {
            "turnstile": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            "hcaptcha": "https://hcaptcha.com/siteverify",
            "recaptcha": "https://www.google.com/recaptcha/api/siteverify",
        }
        verify_url = verify_urls.get(provider)
        if verify_url is None:
            logger.error("CAPTCHA ist aktiviert, aber der Provider ist ungueltig konfiguriert")
            raise HTTPException(
                status_code=503,
                detail="CAPTCHA ist derzeit nicht korrekt konfiguriert.",
            )

        # 2. Require token
        if not token:
            raise HTTPException(status_code=400, detail="CAPTCHA-Verifizierung erforderlich.")

        # 3. Decrypt secret key
        from services.auth_service import AuthService
        enc_secret = PanelSettingsService.get("captcha_secret_key_encrypted", "")
        if enc_secret:
            try:
                secret_key = AuthService.decrypt_secret(enc_secret, aad="msm:settings:captcha_secret_key")
            except Exception as exc:
                logger.error("Konnte CAPTCHA-Secret-Key nicht entschlüsseln: %s", exc)
                raise HTTPException(status_code=503, detail="CAPTCHA ist derzeit nicht korrekt konfiguriert.")
        else:
            secret_key = PanelSettingsService.get("captcha_secret_key", "")

        placeholders = {"test-secret", "changeme", "change-me", "placeholder"}
        normalized_secret = secret_key.strip().lower()
        if (
            not normalized_secret
            or normalized_secret in placeholders
            or normalized_secret.startswith("mock-")
        ):
            logger.error("CAPTCHA ist aktiviert, aber das Secret fehlt oder ist ein Platzhalter")
            raise HTTPException(
                status_code=503,
                detail="CAPTCHA ist derzeit nicht korrekt konfiguriert.",
            )

        # 5. Execute HTTP siteverify POST request (Form encoded)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                data = {
                    "secret": secret_key,
                    "response": token,
                }
                if client_ip:
                    data["remoteip"] = client_ip

                resp = await client.post(verify_url, data=data)
                resp_json = resp.json()

                if not resp_json.get("success"):
                    errors = resp_json.get("error-codes", [])
                    logger.warning("CAPTCHA-Verifizierung fehlgeschlagen: %s", errors)
                    raise HTTPException(
                        status_code=400,
                        detail="CAPTCHA-Verifizierung fehlgeschlagen. Bitte erneut versuchen."
                    )
        except httpx.HTTPError as exc:
            logger.error("Fehler bei CAPTCHA-Netzwerkanfrage: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="CAPTCHA-Verifizierungsdienst vorübergehend nicht erreichbar."
            )
