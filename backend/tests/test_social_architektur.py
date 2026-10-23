"""Der Schnitt der Social-Domänen bleibt erhalten.

Der Umbau von 10/2026 hat ``social_service.py`` und ``routers/social.py`` in
fachliche Dateien geteilt. Ohne diesen Wächter könnte eine Domäne unbemerkt
wieder zusammenwachsen, eine Route verschwinden oder eine Methode in einer
falschen Mixin-Klasse landen. Geprüft wird die Struktur, nicht das Verhalten:
dafür sind die 625 Social-/Messenger-Tests zuständig.
"""

from pathlib import Path

from routers import social_router
from services import social as social_domaenen
from services.social_service import SocialService

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
SOCIAL_SERVICE = BACKEND / "services" / "social"
SOCIAL_ROUTER = BACKEND / "routers" / "social"

#: Höchstgrenze je Datei. Die Zielgröße ist „deutlich unter 800“; die Grenze
#: fängt ein, was sonst schleichend wieder zu einem Monolithen wird.
MAX_ZEILEN = 800

#: Erwartete Domänen mit ihrer Mixin-Klasse und den Methoden, ohne die die
#: jeweilige Domäne nicht mehr wäre, was ihr Name sagt.
DOMAENEN = {
    "freundschaften": (
        "FreundschaftenMixin",
        {"get_friends", "send_friend_request", "block_user", "is_confirmed_friend"},
    ),
    "praesenz": (
        "PraesenzMixin",
        {"get_presence", "update_presence", "broadcast_user_joined"},
    ),
    "profile": (
        "ProfilMixin",
        {"update_privacy", "get_profile", "get_public_profiles"},
    ),
    "direktchat": (
        "DirektchatMixin",
        {
            "derive_blind_mailbox_id",
            "resolve_mailbox_target",
            "can_message_user",
            "ensure_direct_chat",
        },
    ),
    "mailbox": (
        "MailboxMixin",
        {
            "relay_blind_envelope",
            "get_blind_envelopes",
            "delete_blind_envelopes",
            "cleanup_expired_envelopes",
        },
    ),
    "mailbox_zugang": (
        "MailboxZugangMixin",
        {
            "register_blind_mailbox",
            "assert_mailbox_token",
            "assert_mailbox_zugang",
            "erlaubte_mailboxen",
        },
    ),
    "gruppen": (
        "GruppenMixin",
        {
            "create_group",
            "join_group_by_invite_code",
            "delete_group",
            "write_group_config",
        },
    ),
    "gruppen_rollen": (
        "GruppenRollenMixin",
        {"expand_group_permissions", "effective_permissions", "has_group_permission"},
    ),
    "stories": (
        "StoriesMixin",
        {"create_story", "list_active_stories", "delete_story", "cleanup_expired_stories"},
    ),
}


def _mixin_klassen() -> dict[str, type]:
    return {name: getattr(social_domaenen, name) for name, _ in DOMAENEN.values()}


def test_keine_social_datei_ueberschreitet_die_zeilengrenze() -> None:
    dateien = sorted(SOCIAL_SERVICE.glob("*.py")) + sorted(SOCIAL_ROUTER.glob("*.py"))
    assert dateien, "Die Social-Domänen wurden nicht gefunden."
    zeilen = {str(p.relative_to(BACKEND)): len(p.read_text(encoding="utf-8").splitlines()) for p in dateien}
    zu_gross = {pfad: anzahl for pfad, anzahl in zeilen.items() if anzahl > MAX_ZEILEN}
    assert not zu_gross, f"Über {MAX_ZEILEN} Zeilen: {zu_gross}"


def test_jede_domaene_hat_ihre_mixin_klasse_und_ihre_kernmethoden() -> None:
    for modul, (klassenname, methoden) in DOMAENEN.items():
        datei = SOCIAL_SERVICE / f"{modul}.py"
        assert datei.is_file(), f"Domänendatei fehlt: {datei}"
        assert f"class {klassenname}" in datei.read_text(encoding="utf-8"), (
            f"{datei.name} enthält nicht {klassenname}"
        )
        mixin = _mixin_klassen()[klassenname]
        assert mixin.__module__ == f"services.social.{modul}", (mixin.__module__, modul)
        fehlend = sorted(methoden - set(vars(mixin)))
        assert not fehlend, f"{klassenname} fehlt: {fehlend}"


def test_social_service_setzt_alle_domaenen_zusammen() -> None:
    eigenen = [k for k in SocialService.__mro__ if k.__module__.startswith("services.social.")]
    assert len(eigenen) == len(DOMAENEN), [k.__name__ for k in eigenen]
    assert {k.__name__ for k in eigenen} == {k for k, _ in DOMAENEN.values()}

    methoden = {
        name
        for klasse in eigenen
        for name in vars(klasse)
        if not name.startswith("__") and callable(getattr(klasse, name, None))
    }
    assert len(methoden) == 74, f"Erwartet 74 Domänenmethoden, gefunden {len(methoden)}"
    fehlend = sorted(name for name in methoden if not callable(getattr(SocialService, name, None)))
    assert not fehlend, f"SocialService erreicht nicht: {fehlend}"


def test_sozialrouter_behaelt_allen_vertraegen() -> None:
    assert social_router.prefix == "/api/social"

    vertraege: list[tuple[str, str]] = []
    for route in social_router.routes:
        if hasattr(route, "methods"):
            vertraege.extend((methode, route.path) for methode in route.methods)
        else:
            vertraege.append(("WEBSOCKET", route.path))

    assert len(vertraege) == 69, f"Erwartet 69 Social-Routen, gefunden {len(vertraege)}"
    doppelt = sorted({v for v in vertraege if vertraege.count(v) > 1})
    assert not doppelt, f"Doppelte Route-Verträge: {doppelt}"

    pfade = {pfad for _, pfad in vertraege}
    for pfad in (
        "/api/social/friends",
        "/api/social/profile/{target_user_id}",
        "/api/social/e2ee/relay",
        "/api/social/e2ee/deliveries",
        "/api/social/e2ee/deliveries/ack",
        "/api/social/media/upload",
        "/api/social/groups",
        "/api/social/stories",
        "/api/social/ws",
    ):
        assert pfad in pfade, f"Social-Route fehlt: {pfad}"
