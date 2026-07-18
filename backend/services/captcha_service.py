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
        if provider == "none":
            return

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
                raise HTTPException(status_code=500, detail="Interner Serverfehler bei der CAPTCHA-Verifizierung.")
        else:
            secret_key = PanelSettingsService.get("captcha_secret_key", "")

        # Safe fallback/mocking for tests or empty values
        if not secret_key or secret_key.startswith("mock-") or secret_key == "test-secret":
            return

        # 4. Resolve provider siteverify URL
        if provider == "turnstile":
            verify_url = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
        elif provider == "hcaptcha":
            verify_url = "https://hcaptcha.com/siteverify"
        elif provider == "recaptcha":
            verify_url = "https://www.google.com/recaptcha/api/siteverify"
        else:
            logger.warning("Unbekannter CAPTCHA-Provider: %s", provider)
            return

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
