"""Blueprint-Renderer: tokenisiert ``runtime.startup`` und substituiert Werte.

**Security-Modell**:
1. Wir spalten den Template-String *zuerst* via :func:`shlex.split`, damit
   Quoting Shell-konform behandelt wird.
2. Danach substituieren wir Tokens *innerhalb* der schon getrennten Argumente.
   Selbst wenn ein Mod-Name "; rm -rf /" hiesse, kann er nie ein neues argv-
   Element erzeugen, weil `docker run` keinen Shell-Aufruf macht.
3. Wir geben Docker eine argv-Liste mit, kein ``sh -c``-Konstrukt.

Das Modul ist absichtlich klein. Aenderungen hier sind sicherheitskritisch —
jede Erweiterung erfordert neue Tests in ``tests/test_blueprint_renderer.py``.
"""

from __future__ import annotations

import re
import shlex
from typing import Mapping

from .schema import (
    Blueprint,
    BlueprintModInjection,
    BlueprintValidationError,
    _ALLOWED_ENV_VALUE_TOKENS,
    _ALLOWED_STARTUP_TOKENS,
    _is_allowed_port_token,
)


_TOKEN_RE = re.compile(r"\{([A-Z][A-Z0-9_]*(?:\.[A-Z0-9_]+)?)\}")

# Reiner Port-Token-Regex (kein ``ENV.X``). Wird fuer env-Wert-Substitution genutzt.
_PORT_TOKEN_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")


def build_mod_arg(blueprint: Blueprint, active_mod_ids: list[str]) -> str:
    """Erzeugt den Wert fuer ``{MOD_ARG}`` anhand des Blueprint-Modus.

    - Mod-Support (Steam Workshop oder CurseForge) deaktiviert -> leerer String
    - ``modInjection=none`` -> leerer String
    - ``modInjection=file`` -> leerer String (Modliste landet in Datei)
    - ``modInjection=startupArg`` -> ``modStartupArgumentFormat`` mit ``{mods}``
      ersetzt durch mit ``modStartupArgumentSeparator`` (Default ``;``, z. B. ``,`` fuer ASA)
      separierte IDs. Wenn keine Mods aktiv sind, weiterhin leerer String.
    """
    mods = blueprint.effective_mods()
    if not (mods.supportsSteamWorkshop or mods.supportsCurseForge):
        return ""
    if mods.modInjection != BlueprintModInjection.STARTUP_ARG:
        return ""
    if not active_mod_ids:
        return ""
    fmt = mods.modStartupArgumentFormat or ""
    if "{mods}" not in fmt:
        # Schema schliesst das bereits aus, aber Defense-in-Depth.
        return ""
    sep = mods.modStartupArgumentSeparator or ";"
    return fmt.replace("{mods}", sep.join(active_mod_ids))


def render_env_values(
    env: Mapping[str, str],
    *,
    ports: Mapping[str, int | None],
    bind_ip: str | None = None,
) -> dict[str, str]:
    """Substituiert Port-Tokens in den Werten von ``runtime.env``.

    Token-Whitelist: :data:`blueprints.schema._ALLOWED_ENV_VALUE_TOKENS`
    (``GAME_PORT``, ``QUERY_PORT``, ``RCON_PORT``, ``VOICE_PORT``, ``WEB_PORT``).
    Nicht-zugewiesene Ports werden zu leerem String — Konsumenten muessen damit
    rechnen, dass z. B. ``RCON_PORT`` leer ist, wenn der Server keinen RCON-Port
    hat. Tokens ausserhalb der Whitelist sind schon im Schema-Validator
    abgewiesen, hier ist es Defense-in-Depth.
    """
    ports_map: dict[str, str] = {
        "GAME_PORT": "" if not ports.get("game") else str(ports["game"]),
        "QUERY_PORT": "" if not ports.get("query") else str(ports["query"]),
        "RCON_PORT": "" if not ports.get("rcon") else str(ports["rcon"]),
        "VOICE_PORT": "" if not ports.get("voice") else str(ports["voice"]),
        "WEB_PORT": "" if not ports.get("web") else str(ports["web"]),
    }
    for k, v in ports.items():
        if k not in ("game", "query", "rcon", "voice", "web"):
            if k.startswith("custom_"):
                num = k.split("_", 1)[1]
                ports_map[f"CUSTOM_PORT_{num}"] = str(v) if v else ""
            else:
                ports_map[f"{k.upper()}_PORT"] = str(v) if v else ""

    def _sub(match: re.Match[str]) -> str:
        token = match.group(1)
        if token == "BIND_IP":
            return bind_ip or ""
        if not _is_allowed_port_token(token, _ALLOWED_ENV_VALUE_TOKENS):
            raise BlueprintValidationError(
                f"Env-Wert-Token '{{{token}}}' ist nicht in der Whitelist."
            )
        return ports_map.get(token, "")

    out: dict[str, str] = {}
    for key, value in env.items():
        out[key] = _PORT_TOKEN_RE.sub(_sub, value)
    return out


def render_argv(
    blueprint: Blueprint,
    *,
    install_dir: str,
    ports: Mapping[str, int | None],
    bind_ip: str | None = None,
    active_mod_ids: list[str] | None = None,
    extra_env: Mapping[str, str] | None = None,
    host_install_dir: str | None = None,
) -> list[str]:
    """Rendert ``runtime.startup`` (oder passendes ``startupProfile``) zu argv."""
    from .github_source import resolve_startup_template

    startup_template = resolve_startup_template(blueprint, host_install_dir)

    ports_map: dict[str, str] = {
        "GAME_PORT": "" if not ports.get("game") else str(ports["game"]),
        "QUERY_PORT": "" if not ports.get("query") else str(ports["query"]),
        "RCON_PORT": "" if not ports.get("rcon") else str(ports["rcon"]),
        "VOICE_PORT": "" if not ports.get("voice") else str(ports["voice"]),
        "WEB_PORT": "" if not ports.get("web") else str(ports["web"]),
    }
    for k, v in ports.items():
        if k not in ("game", "query", "rcon", "voice", "web"):
            if k.startswith("custom_"):
                num = k.split("_", 1)[1]
                ports_map[f"CUSTOM_PORT_{num}"] = str(v) if v else ""
            else:
                ports_map[f"{k.upper()}_PORT"] = str(v) if v else ""
    mod_arg = build_mod_arg(blueprint, list(active_mod_ids or []))

    env_values: dict[str, str] = dict(extra_env or {})

    raw_args = shlex.split(startup_template)
    rendered: list[str] = []
    for arg in raw_args:
        token_count = 0
        empty_count = 0

        def _sub(match: re.Match[str]) -> str:
            nonlocal token_count, empty_count
            token_count += 1
            token = match.group(1)
            if token == "INSTALL_DIR":
                value = install_dir
            elif token == "MOD_ARG":
                value = mod_arg
            elif token == "BIND_IP":
                value = bind_ip or ""
            elif token in ports_map:
                value = ports_map[token]
            elif token.startswith("ENV."):
                key = token.split(".", 1)[1]
                value = env_values.get(key, "")
            elif _is_allowed_port_token(token, _ALLOWED_STARTUP_TOKENS):
                value = ""
            else:
                raise BlueprintValidationError(
                    f"Unbekanntes Startup-Token '{{{token}}}' beim Rendern."
                )
            if value == "":
                empty_count += 1
            return value

        replaced = _TOKEN_RE.sub(_sub, arg)
        # Das argv-Element wird nur gedroppt, wenn ALLE seine Tokens leer sind —
        # sonst landen Stubs wie ``-port=`` im Container. Args ohne Tokens
        # werden 1:1 übernommen.
        #
        # Entscheidend ist das Wort "alle": UE-Spiele packen ihre komplette
        # Options-URL in EIN argv-Element (``TheIsland?listen?Port=7777?
        # ServerPassword={ENV.SERVER_PASSWORD}``). Löschte ein einziges leeres
        # Token das Element, verlöre ein Server ohne Passwort — der Normalfall
        # — Map und ``?listen`` gleich mit: Er liefe, wäre aber unsichtbar.
        if token_count > 0 and empty_count == token_count:
            continue
        if replaced != "":
            rendered.append(replaced)
    return rendered
