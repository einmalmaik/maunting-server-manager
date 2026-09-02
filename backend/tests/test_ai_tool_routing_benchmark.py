from services.semantic_tool_router_adapter import SemanticToolRouterAdapter
from services.ai_tool_registry import WERKZEUGE

GOLDEN = [
    ("Was ist in Moskau los?", "analyze_region"),
    ("Wie ist das Wetter in Berlin heute?", "analyze_region"),
    ("Zeig mir Satellitenbild fuer 52.5,13.4", "analyze_region"),
    ("Server neu starten", "propose_server_lifecycle"),
    ("Fahr Server 7 herunter", "propose_server_lifecycle"),
    ("Welche Server habe ich?", "list_my_servers"),
    ("Status von Server 7?", "read_server_status"),
    ("Log von Server 3 zeigen", "read_server_logs"),
    ("Welche Ports nutzt mein Valheim Server?", "read_server_ports"),
    ("Mach ein Backup von allen Servern", "propose_backup"),
    ("Spiel zuruecksetzen", "propose_backup_restore"),
    ("Setze hostname auf MauntARK", "propose_config_set"),
    ("Aendere Harvest auf 2x", "propose_config_set"),
    ("Suche nach Mod ValheimPlus im Workshop", "search_workshop_mods"),
    ("Installiere Mod 12345", "propose_mod_install"),
    ("Schalte Mod 7 aus", "propose_mod_toggle"),
    ("Wie richte ich Auto-Backup ein?", "search_docs"),
    ("Suche aktuelle Valheim Ports im Netz", "web_search"),
    ("Merke: ich hoste Valheim", "remember"),
    ("Was weisst du ueber meine Spielvorlieben?", "search_memory"),
    ("Welche Termine habe ich heute?", "calendar_read"),
    ("Schick Testmail", "send_test_email"),
    ("Starte Aufgabe alle 8h neu starten", "propose_task_set"),
    ("Schau auf meinen Bildschirm", "desktop_system"),
    ("Raeume meine Downloads auf", "desktop_aufraeumen"),
]

ALLOWED = frozenset(WERKZEUGE.keys())


def test_hit_at_k_offline():
    router = SemanticToolRouterAdapter()
    router.warm(ALLOWED)
    hits1 = 0
    hits5 = 0
    misses = []
    for query, expected in GOLDEN:
        top5 = router.select_with_hot(query, ALLOWED, top_k=5)
        if top5 and top5[0] == expected:
            hits1 += 1
        if expected in top5:
            hits5 += 1
        else:
            misses.append((query, expected, top5[:3]))
    fallback_rate = len(misses) / len(GOLDEN)
    assert hits5 / len(GOLDEN) >= 0.4, f"hit@5 {hits5}/{len(GOLDEN)} misses={misses}"
    assert fallback_rate < 0.7, f"fallback {fallback_rate} too high misses={misses}"


def test_hotset_always_in_shortlist():
    router = SemanticToolRouterAdapter()
    short = router.select_with_hot("beliebige anfrage", ALLOWED, top_k=5)
    assert "list_my_servers" in short
    assert "analyze_region" in short
    assert "web_search" in short
    assert "remember" in short
    assert "search_memory" in short
    assert "notes_read" in short
    assert "learn_skill" in short


def test_routing_is_deterministic():
    router = SemanticToolRouterAdapter()
    a = router.select("Server neu starten", ALLOWED, top_k=5)
    b = router.select("Server neu starten", ALLOWED, top_k=5)
    assert a == b
