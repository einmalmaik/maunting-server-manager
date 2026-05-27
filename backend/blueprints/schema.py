"""Blueprint-Schema (Pydantic v2). Validierung mit klaren Fehlermeldungen.

Sicherheitsinvarianten:
- ``runtime.startup`` darf keine Shell-Metas enthalten (``$``, ``;``, ``&``, ``|``,
  Backticks, ``$(``). Tokens werden separat vom Renderer auf eine Whitelist
  geprueft.
- Pfad-Felder (``modListFilePath``, ``http.extractTo``) muessen *relative*
  Pfade ohne ``..``-Komponenten sein. Die endgueltige Pfad-Aufloesung gegen
  ``install_dir`` macht der jeweilige Konsument (`realpath`-Check).
- HTTP-URLs sind ``https://``-only.
- Es gibt **keine** Skript-/Hook-Felder. Wenn jemand sowas im Schema vermisst,
  ist das Absicht — Blueprints sind reine Daten.

KISS: ein File, alle Modelle hier. Keine Vererbungshierarchien.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


SUPPORTED_BLUEPRINT_VERSION = 1


class BlueprintValidationError(ValueError):
    """Aufrufer-freundliche Fehlerklasse — listet alle Pydantic-Fehler kompakt."""

    def __init__(self, message: str, *, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors: list[str] = errors or [message]

    @classmethod
    def from_pydantic(cls, err: ValidationError) -> "BlueprintValidationError":
        msgs: list[str] = []
        for e in err.errors():
            loc = ".".join(str(p) for p in e.get("loc", ()))
            msg = e.get("msg", "ungueltig")
            ctx = e.get("ctx") or {}
            extra = f" (kontext: {ctx})" if ctx else ""
            msgs.append(f"{loc}: {msg}{extra}" if loc else f"{msg}{extra}")
        return cls("Blueprint-Validierung fehlgeschlagen", errors=msgs)


# ── Enums ──────────────────────────────────────────────────────────────────


class BlueprintCategory(str, Enum):
    STEAM_GAME = "steam_game"
    NON_STEAM_GAME = "non_steam_game"
    VOICE_SERVER = "voice_server"
    BOT = "bot"


class BlueprintSourceType(str, Enum):
    STEAM = "steam"
    HTTP = "http"
    DOCKER_ONLY = "dockerOnly"
    CUSTOM = "custom"
    MANUAL_UPLOAD = "manualUpload"


class BlueprintPortName(str, Enum):
    GAME = "game"
    QUERY = "query"
    RCON = "rcon"
    VOICE = "voice"
    WEB = "web"
    CUSTOM = "custom"


class BlueprintPortProtocol(str, Enum):
    TCP = "tcp"
    UDP = "udp"


class BlueprintSteamPlatform(str, Enum):
    LINUX = "linux"
    WINDOWS = "windows"


class BlueprintSteamCompatibility(str, Enum):
    NATIVE = "native"
    WINE = "wine"
    PROTON = "proton"


class BlueprintArchiveType(str, Enum):
    ZIP = "zip"
    TAR_GZ = "tar.gz"
    TGZ = "tgz"
    TAR_XZ = "tar.xz"
    TXZ = "txz"
    TAR_BZ2 = "tar.bz2"
    TBZ2 = "tbz2"
    SEVEN_Z = "7z"


class BlueprintModInjection(str, Enum):
    NONE = "none"
    STARTUP_ARG = "startupArg"
    FILE = "file"


class BlueprintWorkshopFileOperation(str, Enum):
    COPY = "copy"
    SYMLINK = "symlink"


class BlueprintModListContent(str, Enum):
    WORKSHOP_IDS = "workshopIds"
    POST_INSTALL_TARGET_BASENAMES = "postInstallTargetBasenames"


class BlueprintConfigPatchType(str, Enum):
    INI = "ini"


# ── Helpers (statisch genug fuer KISS) ─────────────────────────────────────

_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_DOCKER_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}$")
_NUMERIC_ID_RE = re.compile(r"^\d{1,10}$")
_ALLOWED_STARTUP_TOKEN_RE = re.compile(r"^[A-Z][A-Z0-9_]*(?:\.[A-Z0-9_]+)?$")
_TOKEN_FIND_RE = re.compile(r"\{([^{}\s]+)\}")

# Substitutions-relevante Shell-Metas, die wir aus Startup-Templates verbannen.
# Hintergrund: ``runtime.startup`` wird von shlex.split tokenisiert und dann
# direkt als argv an ``docker run`` uebergeben — NIE ueber ``sh -c``. Damit sind
# Zeichen wie ``;`` oder ``|`` in argv-Strings harmlos (sie werden literal
# uebergeben). Trotzdem verbieten wir die *Substitutions*-Zeichen ``$`` und
# Backtick, weil sie in einer Shell-Auswertung dynamische Werte produzieren
# wuerden — Defense-in-Depth fuer den Fall, dass ein Konsument diesen String
# je versehentlich an einen Shell-Aufruf weitergibt.
_FORBIDDEN_STARTUP_CHARS = ("$", "`")
_FORBIDDEN_STARTUP_SEQ = ("$(", "${", "&&", "||")

_ALLOWED_STARTUP_TOKENS: frozenset[str] = frozenset({
    "GAME_PORT",
    "QUERY_PORT",
    "RCON_PORT",
    "VOICE_PORT",
    "WEB_PORT",
    "INSTALL_DIR",
    "MOD_ARG",
})

# Tokens, die in ``runtime.env``-Werten substituiert werden duerfen.
# Bewusst kleiner als ``_ALLOWED_STARTUP_TOKENS``: KEIN ``INSTALL_DIR`` (Container-
# Pfad ist nichts, was als Env-Var Sinn macht), KEIN ``MOD_ARG`` (Argument-String,
# nicht Env-Wert) und KEIN ``ENV.<KEY>`` (waere zirkular). Nur Port-Werte —
# Use-Case: Images wie ``itzg/minecraft-server`` lesen ``SERVER_PORT`` aus der
# Env. Damit kann der Host-Port in den Container weitergereicht werden.
_ALLOWED_ENV_VALUE_TOKENS: frozenset[str] = frozenset({
    "GAME_PORT",
    "QUERY_PORT",
    "RCON_PORT",
    "VOICE_PORT",
    "WEB_PORT",
})

_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_ALLOWED_CONFIG_VALUE_TOKENS: frozenset[str] = frozenset({
    "GAME_PORT",
    "QUERY_PORT",
    "RCON_PORT",
    "VOICE_PORT",
    "WEB_PORT",
})
_ALLOWED_WORKSHOP_PATH_TOKENS: frozenset[str] = frozenset({
    "WORKSHOP_APP_ID",
    "WORKSHOP_ID",
    "BASENAME",
})


def _is_safe_relative_path(value: str) -> bool:
    """Prueft, ob ``value`` ein sicherer relativer Pfad ist.

    Akzeptiert:
    - kein leading ``/`` und kein Windows-Drive-Prefix
    - keine ``..``-Komponenten
    - keine NUL-Bytes, keine Backslashes (vermeidet plattform-spezifische
      Mehrdeutigkeit; Blueprints leben unter Linux)
    """
    if not value:
        return False
    if value.startswith("/") or "\x00" in value or "\\" in value:
        return False
    if value.startswith("~"):
        return False
    parts = value.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            # leere Segmente bedeuten "//" oder leading "/"; ".." waere Escape;
            # "." waere nutzlos -> ablehnen, sonst entstehen ueberraschende Pfade
            return False
    return True


def _is_safe_relative_template(
    value: str,
    *,
    allowed_tokens: frozenset[str],
    allow_glob: bool = False,
) -> bool:
    """Wie _is_safe_relative_path, aber mit whitelisted Tokens und optional Glob.

    Blueprints duerfen damit Dateien innerhalb von install_dir beschreiben,
    ohne absolute Pfade, ``..`` oder unbekannte Platzhalter einzufuehren.
    """
    if not value:
        return False
    if value.startswith("/") or "\x00" in value or "\\" in value:
        return False
    if value.startswith("~"):
        return False

    for match in _TOKEN_FIND_RE.finditer(value):
        if match.group(1) not in allowed_tokens:
            return False

    sanitized = _TOKEN_FIND_RE.sub("token", value)
    parts = sanitized.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            return False
        if not allow_glob and ("*" in part or "?" in part or "[" in part or "]" in part):
            return False
    return True


# ── Modelle ────────────────────────────────────────────────────────────────


class BlueprintMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    category: BlueprintCategory
    author: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=1024)

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(
                "meta.id muss aus Kleinbuchstaben, Ziffern oder Unterstrich bestehen "
                "(Regex ^[a-z0-9_]{1,64}$)."
            )
        return v


class BlueprintRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str = Field(min_length=1, max_length=256)
    workdir: str | None = Field(default=None, max_length=512)
    env: dict[str, str] = Field(default_factory=dict)
    startup: str = Field(min_length=1, max_length=2048)
    configPatches: list["BlueprintConfigPatch"] = Field(default_factory=list, max_length=32)

    @field_validator("image")
    @classmethod
    def _check_image(cls, v: str) -> str:
        if not _DOCKER_IMAGE_RE.match(v):
            raise ValueError(
                "runtime.image ist kein gueltiger Docker-Image-Name "
                "(erlaubt: Buchstaben, Ziffern, ``._/:@-``)."
            )
        return v

    @field_validator("workdir")
    @classmethod
    def _check_workdir(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not v.startswith("/"):
            raise ValueError("runtime.workdir muss absoluter Container-Pfad sein.")
        if "\x00" in v or ".." in v.split("/"):
            raise ValueError("runtime.workdir enthaelt unsichere Komponenten.")
        return v

    @field_validator("env")
    @classmethod
    def _check_env(cls, v: dict[str, str]) -> dict[str, str]:
        for key, value in v.items():
            if not _ENV_KEY_RE.match(key):
                raise ValueError(
                    f"runtime.env-Key '{key}' ist ungueltig "
                    "(erlaubt: ^[A-Z][A-Z0-9_]*$)."
                )
            # Shell-Substitutionszeichen auch in Env-Werten ausschliessen.
            # ``docker run -e KEY=VALUE`` uebergibt das ohne Shell-Auswertung,
            # aber Defense-in-Depth fuer den Fall, dass ein Konsument den Wert
            # je in ein Shell-Skript einbaut.
            for seq in _FORBIDDEN_STARTUP_SEQ:
                if seq in value:
                    raise ValueError(
                        f"runtime.env['{key}'] enthaelt verbotene Shell-Sequenz '{seq}'."
                    )
            for ch in _FORBIDDEN_STARTUP_CHARS:
                if ch in value:
                    raise ValueError(
                        f"runtime.env['{key}'] enthaelt verbotenes Shell-Sonderzeichen '{ch}'."
                    )
            # Token-Whitelist fuer Env-Werte. Nur Port-Tokens — siehe
            # ``_ALLOWED_ENV_VALUE_TOKENS``.
            for match in _TOKEN_FIND_RE.finditer(value):
                token = match.group(1)
                if not _ALLOWED_STARTUP_TOKEN_RE.match(token):
                    raise ValueError(
                        f"runtime.env['{key}']: Token '{{{token}}}' hat unzulaessige Syntax."
                    )
                if token not in _ALLOWED_ENV_VALUE_TOKENS:
                    raise ValueError(
                        f"runtime.env['{key}']: Token '{{{token}}}' nicht erlaubt "
                        f"(erlaubt in Env-Werten: {sorted(_ALLOWED_ENV_VALUE_TOKENS)})."
                    )
        return v

    @field_validator("startup")
    @classmethod
    def _check_startup(cls, v: str) -> str:
        # 1) Shell-Metas verbieten (Defense-in-Depth gegen Tokenisierungs-Fehler).
        for seq in _FORBIDDEN_STARTUP_SEQ:
            if seq in v:
                raise ValueError(
                    f"runtime.startup enthaelt verbotene Shell-Sequenz '{seq}'."
                )
        for ch in _FORBIDDEN_STARTUP_CHARS:
            if ch in v:
                raise ValueError(
                    f"runtime.startup enthaelt verbotenes Shell-Sonderzeichen '{ch}'."
                )
        # 2) Tokens parsen und gegen Whitelist pruefen.
        for match in _TOKEN_FIND_RE.finditer(v):
            token = match.group(1)
            if not _ALLOWED_STARTUP_TOKEN_RE.match(token):
                raise ValueError(
                    f"runtime.startup: Token '{{{token}}}' hat unzulaessige Syntax."
                )
            if token.startswith("ENV."):
                env_key = token.split(".", 1)[1]
                if not _ENV_KEY_RE.match(env_key):
                    raise ValueError(
                        f"runtime.startup: ENV-Token '{{{token}}}' ungueltig."
                    )
                continue
            if token not in _ALLOWED_STARTUP_TOKENS:
                raise ValueError(
                    f"runtime.startup: Token '{{{token}}}' nicht in der Whitelist "
                    f"({sorted(_ALLOWED_STARTUP_TOKENS)} + ENV.<KEY>)."
                )
        return v


class BlueprintPort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: BlueprintPortName
    protocol: BlueprintPortProtocol


class BlueprintSteamSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appId: str
    platform: BlueprintSteamPlatform
    compatibility: BlueprintSteamCompatibility | None = None
    requiresLogin: bool = False

    @field_validator("appId")
    @classmethod
    def _check_app_id(cls, v: str) -> str:
        if not _NUMERIC_ID_RE.match(v):
            raise ValueError("source.steam.appId muss numerischer String sein (^\\d{1,10}$).")
        return v

    @model_validator(mode="after")
    def _platform_compat(self) -> "BlueprintSteamSource":
        if self.platform == BlueprintSteamPlatform.LINUX:
            if self.compatibility is None:
                # Default ist native, kein Fehler.
                object.__setattr__(self, "compatibility", BlueprintSteamCompatibility.NATIVE)
        else:  # WINDOWS
            if self.compatibility in (None, BlueprintSteamCompatibility.NATIVE):
                raise ValueError(
                    "Windows-Steam-Sources brauchen compatibility=wine oder proton."
                )
        return self


class BlueprintHttpSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8, max_length=2048)
    archiveType: BlueprintArchiveType | None = None
    extractTo: str | None = Field(default=None, max_length=512)
    # Optionaler SHA-256-Hash (lowercase hex). Wenn gesetzt, prueft der
    # Downloader das Archiv vor dem Entpacken — Supply-Chain-Hardening.
    sha256: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("source.http.url muss mit 'https://' beginnen.")
        if "\x00" in v or "\n" in v or "\r" in v:
            raise ValueError("source.http.url enthaelt verbotene Zeichen.")
        return v

    @field_validator("extractTo")
    @classmethod
    def _check_extract_to(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not _is_safe_relative_path(v):
            raise ValueError(
                "source.http.extractTo muss ein sicherer relativer Pfad ohne '..' sein."
            )
        return v

    @field_validator("sha256")
    @classmethod
    def _check_sha256(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not re.fullmatch(r"[0-9a-f]{64}", v):
            raise ValueError(
                "source.http.sha256 muss ein 64-Zeichen lowercase Hex-String sein."
            )
        return v


class BlueprintManualSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requiredFiles: list[str] = Field(min_length=1, max_length=16)
    instructions: str = Field(min_length=1, max_length=4096)
    instructionsUrl: str | None = Field(default=None, max_length=2048)

    @field_validator("requiredFiles")
    @classmethod
    def _check_required_files(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        for p in v:
            if not _is_safe_relative_path(p):
                raise ValueError(
                    f"source.manual.requiredFiles enthält unsicheren Pfad '{p}' "
                    "(absolute/'..'-Pfade sind verboten)."
                )
            if p in seen:
                raise ValueError(f"source.manual.requiredFiles: Duplikat '{p}'.")
            seen.add(p)
        return v

    @field_validator("instructionsUrl")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not v.startswith("https://"):
            raise ValueError("source.manual.instructionsUrl muss mit 'https://' beginnen.")
        return v


class BlueprintSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: BlueprintSourceType
    steam: BlueprintSteamSource | None = None
    http: BlueprintHttpSource | None = None
    manual: BlueprintManualSource | None = None

    @model_validator(mode="after")
    def _check_subobjects(self) -> "BlueprintSource":
        if self.type == BlueprintSourceType.STEAM:
            if self.steam is None:
                raise ValueError("source.type=steam benoetigt source.steam.")
            if self.http is not None or self.manual is not None:
                raise ValueError("source.type=steam darf source.http/manual nicht setzen.")
        elif self.type == BlueprintSourceType.HTTP:
            if self.http is None:
                raise ValueError("source.type=http benoetigt source.http.")
            if self.steam is not None or self.manual is not None:
                raise ValueError("source.type=http darf source.steam/manual nicht setzen.")
        elif self.type == BlueprintSourceType.MANUAL_UPLOAD:
            if self.manual is None:
                raise ValueError("source.type=manualUpload benötigt source.manual.")
            if self.steam is not None or self.http is not None:
                raise ValueError("source.type=manualUpload darf source.steam/http nicht setzen.")
        else:  # dockerOnly / custom
            if self.steam is not None or self.http is not None or self.manual is not None:
                raise ValueError(
                    f"source.type={self.type.value} darf weder source.steam, source.http noch source.manual setzen."
                )
        return self


class BlueprintMods(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supportsMods: bool = False
    supportsSteamWorkshop: bool = False
    workshopAppId: str | None = None
    modInjection: BlueprintModInjection = BlueprintModInjection.NONE
    modStartupArgumentFormat: str | None = Field(default=None, max_length=256)
    modListFilePath: str | None = Field(default=None, max_length=512)
    modListContent: BlueprintModListContent = BlueprintModListContent.WORKSHOP_IDS
    postInstall: list["BlueprintWorkshopFileAction"] = Field(default_factory=list, max_length=32)

    @field_validator("workshopAppId")
    @classmethod
    def _check_workshop_app_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _NUMERIC_ID_RE.match(v):
            raise ValueError("mods.workshopAppId muss numerischer String sein (^\\d{1,10}$).")
        return v

    @field_validator("modStartupArgumentFormat")
    @classmethod
    def _check_format(cls, v: str | None) -> str | None:
        if v is None:
            return None
        for ch in _FORBIDDEN_STARTUP_CHARS:
            if ch in v:
                raise ValueError(
                    f"mods.modStartupArgumentFormat enthaelt verbotenes Shell-Zeichen '{ch}'."
                )
        return v

    @field_validator("modListFilePath")
    @classmethod
    def _check_modlist_path(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not _is_safe_relative_path(v):
            raise ValueError(
                "mods.modListFilePath muss sicherer relativer Pfad ohne '..' sein."
            )
        return v

    @model_validator(mode="after")
    def _check_consistency(self) -> "BlueprintMods":
        if self.supportsSteamWorkshop:
            if not self.workshopAppId:
                raise ValueError(
                    "mods.workshopAppId ist Pflicht, wenn supportsSteamWorkshop=true."
                )
        else:
            # Workshop deaktiviert -> Mod-Injection ist effektiv none.
            # Wir akzeptieren noch im Schema, dass die Felder gesetzt sind, sodass
            # ein User Workshop spaeter aktivieren kann, ohne erneut alles
            # einzutragen. Der Renderer/Helper ignoriert sie aber.
            pass

        if self.modInjection == BlueprintModInjection.STARTUP_ARG:
            if not self.modStartupArgumentFormat:
                raise ValueError(
                    "mods.modStartupArgumentFormat ist Pflicht bei modInjection=startupArg."
                )
            if "{mods}" not in self.modStartupArgumentFormat:
                raise ValueError(
                    "mods.modStartupArgumentFormat muss den Platzhalter '{mods}' enthalten."
                )
        if self.modInjection == BlueprintModInjection.FILE:
            if not self.modListFilePath:
                raise ValueError(
                    "mods.modListFilePath ist Pflicht bei modInjection=file."
                )
        if self.modListContent == BlueprintModListContent.POST_INSTALL_TARGET_BASENAMES:
            if not self.postInstall:
                raise ValueError(
                    "mods.postInstall ist Pflicht bei modListContent=postInstallTargetBasenames."
                )
        return self


class BlueprintWorkshopFileAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: BlueprintWorkshopFileOperation
    source: str = Field(min_length=1, max_length=512)
    target: str = Field(min_length=1, max_length=512)
    required: bool = False

    @field_validator("source")
    @classmethod
    def _check_source(cls, v: str) -> str:
        if not _is_safe_relative_template(
            v,
            allowed_tokens=_ALLOWED_WORKSHOP_PATH_TOKENS,
            allow_glob=True,
        ):
            raise ValueError(
                "mods.postInstall.source muss ein sicherer relativer Pfad mit "
                "whitelisted Workshop-Tokens sein."
            )
        return v

    @field_validator("target")
    @classmethod
    def _check_target(cls, v: str) -> str:
        if not _is_safe_relative_template(
            v,
            allowed_tokens=_ALLOWED_WORKSHOP_PATH_TOKENS,
            allow_glob=False,
        ):
            raise ValueError(
                "mods.postInstall.target muss ein sicherer relativer Pfad mit "
                "whitelisted Workshop-Tokens sein."
            )
        return v

    @model_validator(mode="after")
    def _check_glob_target(self) -> "BlueprintWorkshopFileAction":
        source_has_glob = any(ch in self.source for ch in ("*", "?", "["))
        if source_has_glob and "{BASENAME}" not in self.target:
            raise ValueError(
                "mods.postInstall.target muss {BASENAME} enthalten, wenn source ein Glob ist."
            )
        return self


class BlueprintConfigPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: BlueprintConfigPatchType
    file: str = Field(min_length=1, max_length=512)
    section: str = Field(min_length=1, max_length=128)
    key: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=512)

    @field_validator("file")
    @classmethod
    def _check_file(cls, v: str) -> str:
        if not _is_safe_relative_path(v):
            raise ValueError("runtime.configPatches.file muss ein sicherer relativer Pfad sein.")
        return v

    @field_validator("section", "key")
    @classmethod
    def _check_ini_name(cls, v: str) -> str:
        if "\x00" in v or "\n" in v or "\r" in v or "[" in v or "]" in v or "=" in v:
            raise ValueError("INI-Section/Key enthaelt verbotene Zeichen.")
        return v

    @field_validator("value")
    @classmethod
    def _check_value(cls, v: str) -> str:
        if "\x00" in v or "\n" in v or "\r" in v:
            raise ValueError("runtime.configPatches.value enthaelt verbotene Zeichen.")
        for match in _TOKEN_FIND_RE.finditer(v):
            token = match.group(1)
            if token not in _ALLOWED_CONFIG_VALUE_TOKENS:
                raise ValueError(
                    f"runtime.configPatches.value: Token '{{{token}}}' nicht erlaubt."
                )
        return v


class Blueprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    meta: BlueprintMeta
    runtime: BlueprintRuntime
    ports: list[BlueprintPort] = Field(min_length=0)
    source: BlueprintSource
    mods: BlueprintMods | None = None

    @field_validator("version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        if v != SUPPORTED_BLUEPRINT_VERSION:
            raise ValueError(
                f"Unsupported blueprint version {v}; supported = {SUPPORTED_BLUEPRINT_VERSION}."
            )
        return v

    @field_validator("ports")
    @classmethod
    def _check_ports_unique(cls, v: list[BlueprintPort]) -> list[BlueprintPort]:
        seen: set[tuple[str, str]] = set()
        for p in v:
            key = (p.name.value, p.protocol.value)
            if key in seen:
                raise ValueError(
                    f"Doppelte Port-Rolle '{p.name.value}/{p.protocol.value}' in ports."
                )
            seen.add(key)
        return v

    def effective_mods(self) -> BlueprintMods:
        """Liefert ``self.mods`` oder ein neutrales Default-Objekt."""
        return self.mods or BlueprintMods()


# ── Loader ────────────────────────────────────────────────────────────────


def load_blueprint_dict(data: dict[str, Any]) -> Blueprint:
    """Validiert ein dict gegen das Schema. Wirft ``BlueprintValidationError``."""
    try:
        return Blueprint.model_validate(data)
    except ValidationError as exc:
        raise BlueprintValidationError.from_pydantic(exc) from exc


def load_blueprint_file(path: Path | str) -> Blueprint:
    """Liest + parst + validiert eine ``.blueprint.json``-Datei."""
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise BlueprintValidationError(
            f"Blueprint-Datei {p} nicht lesbar: {exc}"
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BlueprintValidationError(
            f"Blueprint-Datei {p} ist kein gueltiges JSON: {exc.msg} "
            f"(Zeile {exc.lineno}, Spalte {exc.colno})."
        ) from exc
    if not isinstance(data, dict):
        raise BlueprintValidationError(
            f"Blueprint-Datei {p} muss ein JSON-Objekt enthalten, kein {type(data).__name__}."
        )
    return load_blueprint_dict(data)


# ── Downloadbares Template ────────────────────────────────────────────────


EMPTY_TEMPLATE: dict[str, Any] = {
    "version": SUPPORTED_BLUEPRINT_VERSION,
    "meta": {
        "id": "",
        "name": "",
        "category": "steam_game",
        "author": "",
        "description": "",
    },
    "runtime": {
        "image": "",
        "workdir": "/data",
        "env": {},
        "startup": "",
        "configPatches": [],
    },
    "ports": [
        {"name": "game", "protocol": "udp"},
    ],
    "source": {
        "type": "steam",
        "steam": {
            "appId": "",
            "platform": "linux",
            "compatibility": "native",
            "requiresLogin": False,
        },
        "http": None,
        "manual": None,
    },
    "mods": {
        "supportsMods": False,
        "supportsSteamWorkshop": False,
        "workshopAppId": None,
        "modInjection": "none",
        "modStartupArgumentFormat": None,
        "modListFilePath": None,
        "modListContent": "workshopIds",
        "postInstall": [],
    },
}
