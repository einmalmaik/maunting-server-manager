from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FieldSpec:
    name: str
    group: str
    kind: str


FIELD_SPECS = (
    FieldSpec("ServerName", "identity", "string"),
    FieldSpec("AdminPassword", "identity", "string"),
    FieldSpec("ServerPassword", "identity", "string"),
    FieldSpec("ServerCommunity", "identity", "int"),
    FieldSpec("MaxNudity", "identity", "int"),
    FieldSpec("PVPEnabled", "rules", "boolean"),
    FieldSpec("IsBattlEyeEnabled", "rules", "boolean"),
    FieldSpec("ClanMaxSize", "rules", "int"),
    FieldSpec("MaxPlayers", "rules", "int"),
    FieldSpec("LogoutCharactersRemainInTheWorld", "rules", "boolean"),
    FieldSpec("AvatarsDisabled", "rules", "boolean"),
    FieldSpec("EnableSandStorm", "world", "boolean"),
    FieldSpec("HarvestAmountMultiplier", "world", "float"),
    FieldSpec("ResourceRespawnSpeedMultiplier", "world", "float"),
    FieldSpec("NPCRespawnMultiplier", "world", "float"),
    FieldSpec("DayCycleSpeedScale", "time", "float"),
    FieldSpec("DayTimeSpeedScale", "time", "float"),
    FieldSpec("NightTimeSpeedScale", "time", "float"),
    FieldSpec("DawnDuskSpeedScale", "time", "float"),
    FieldSpec("PlayerXPRateMultiplier", "progression", "float"),
    FieldSpec("PlayerXPKillMultiplier", "progression", "float"),
    FieldSpec("PlayerXPHarvestMultiplier", "progression", "float"),
    FieldSpec("PlayerXPCraftMultiplier", "progression", "float"),
    FieldSpec("PlayerXPTimeMultiplier", "progression", "float"),
    FieldSpec("PlayerDamageMultiplier", "combat", "float"),
    FieldSpec("PlayerDamageTakenMultiplier", "combat", "float"),
    FieldSpec("NPCDamageMultiplier", "combat", "float"),
    FieldSpec("NPCDamageTakenMultiplier", "combat", "float"),
    FieldSpec("MinionDamageMultiplier", "combat", "float"),
    FieldSpec("MinionDamageTakenMultiplier", "combat", "float"),
    FieldSpec("StructureDamageMultiplier", "building", "float"),
    FieldSpec("StructureDamageTakenMultiplier", "building", "float"),
    FieldSpec("StructureHealthMultiplier", "building", "float"),
    FieldSpec("CanDamagePlayerOwnedStructures", "building", "boolean"),
    FieldSpec("BuildingPreloadRadius", "performance", "float"),
    FieldSpec("ServerVoiceChat", "performance", "boolean"),
)

FIELD_BY_NAME = {field.name: field for field in FIELD_SPECS}
GROUP_ORDER = ("identity", "rules", "world", "time", "progression", "combat", "building", "performance")
GROUP_TITLES = {
    "identity": "Server Identity",
    "rules": "Access and Rules",
    "world": "World and PvE",
    "time": "Time",
    "progression": "XP and Progression",
    "combat": "Combat",
    "building": "Building and Raiding",
    "performance": "Performance",
}

_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_ASSIGNMENT_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def _default_known() -> dict[str, Any]:
    return {field.name: None for field in FIELD_SPECS}


def _strip_inline_comment(value: str) -> str:
    in_quotes = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_quotes = not in_quotes
            continue
        if not in_quotes and char in {";", "#"}:
            return value[:index].rstrip()
    return value.strip()


def _parse_bool(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _parse_number(value: str, kind: str) -> int | float:
    cleaned = value.strip()
    if kind == "int":
        return int(float(cleaned))
    return float(cleaned)


def _parse_string(value: str) -> str:
    cleaned = _strip_inline_comment(value)
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] == '"':
        return cleaned[1:-1].replace('\\"', '"')
    return cleaned


def _parse_value(value: str, kind: str) -> Any:
    cleaned = _strip_inline_comment(value)
    if kind == "boolean":
        return _parse_bool(cleaned)
    if kind in {"int", "float"}:
        return _parse_number(cleaned, kind)
    if kind == "string":
        return _parse_string(value)
    raise ValueError(f"Unsupported field kind: {kind}")


def parse_serverdz(raw: str) -> dict[str, Any]:
    # Compatibility name: the panel API used to expose serverDZ.cfg. For Conan,
    # this parses ConanSandbox/Saved/Config/LinuxServer/ServerSettings.ini.
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    known = _default_known()
    consumed: set[int] = set()
    current_section = ""
    seen_server_settings = False

    for index, line in enumerate(lines):
        section_match = _SECTION_RE.match(line)
        if section_match:
            current_section = section_match.group(1).strip()
            if current_section.lower() == "serversettings":
                seen_server_settings = True
                consumed.add(index)
            continue

        if seen_server_settings and current_section.lower() != "serversettings":
            continue

        assignment = _ASSIGNMENT_RE.match(line)
        if not assignment:
            continue

        name, value = assignment.groups()
        field = FIELD_BY_NAME.get(name)
        if not field:
            continue

        try:
            known[name] = _parse_value(value, field.kind)
        except ValueError:
            continue
        consumed.add(index)

    custom_lines = [line for index, line in enumerate(lines) if index not in consumed]
    custom_raw = "\n".join(custom_lines).strip("\n")
    return {
        "raw": normalized,
        "known": known,
        "custom_raw": custom_raw,
        "groups": [{"key": key, "title": GROUP_TITLES[key]} for key in GROUP_ORDER],
        "fields": [
            {"name": field.name, "group": field.group, "kind": field.kind}
            for field in FIELD_SPECS
        ],
    }


def _sanitize_scalar_text(value: Any) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").strip()


def _render_scalar(value: Any, kind: str) -> str:
    if kind == "string":
        return _sanitize_scalar_text(value)
    if kind == "int":
        return str(int(float(value)))
    if kind == "float":
        return f"{float(value):g}"
    if kind == "boolean":
        return "True" if bool(value) else "False"
    raise ValueError(f"Unsupported field kind: {kind}")


def render_serverdz(known: dict[str, Any], custom_raw: str) -> str:
    lines = ["[ServerSettings]"]
    for group in GROUP_ORDER:
        group_lines: list[str] = []
        for field in FIELD_SPECS:
            if field.group != group:
                continue
            value = known.get(field.name)
            if value is None or value == "":
                continue
            group_lines.append(f"{field.name}={_render_scalar(value, field.kind)}")
        if group_lines:
            lines.append("")
            lines.extend(group_lines)

    rendered = "\n".join(lines).rstrip() + "\n"
    preserved_custom = custom_raw.strip()
    if preserved_custom:
        rendered += "\n; Preserved custom and mod-specific settings\n" + preserved_custom + "\n"
    return rendered
