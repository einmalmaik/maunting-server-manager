"""Tests für den strukturierten Ethics Trigger ohne Regex-Listen."""

from __future__ import annotations

import pytest

from services.ai_ethics_trigger import (
    evaluate_action_risk,
    should_trigger_ethics,
)


class TestEthicsTriggerRiskEvaluation:
    def test_destruktive_werkzeuge_sind_critical(self):
        destruktive = [
            "propose_server_delete",
            "propose_file_delete",
            "propose_backup_restore",
            "propose_blueprint_delete",
            "propose_ai_tarif_role",
        ]
        for tool_name in destruktive:
            level, is_destr, _ = evaluate_action_risk(tool_name)
            assert level == "critical"
            assert is_destr is True

    def test_desktop_eingriffe_haben_erhoehtes_risiko(self):
        level_clean, is_destr, _ = evaluate_action_risk("desktop_aufraeumen")
        assert level_clean == "critical"
        assert is_destr is True

        level_ctrl, _, _ = evaluate_action_risk("desktop_steuern")
        assert level_ctrl == "review"

    def test_schreibwerkzeuge_und_delegation_sind_review(self):
        review_tools = [
            "propose_config_update",
            "propose_config_patch",
            "propose_mod_install",
            "worker_start",
        ]
        for tool_name in review_tools:
            level, is_destr, _ = evaluate_action_risk(tool_name)
            assert level == "review"
            assert is_destr is False

    def test_lesewerkzeuge_sind_low(self):
        read_tools = [
            "list_my_servers",
            "read_server_status",
            "search_docs",
            "read_config",
            "get_my_limits",
        ]
        for tool_name in read_tools:
            level, is_destr, _ = evaluate_action_risk(tool_name)
            assert level == "low"
            assert is_destr is False

    def test_autonomer_modus_stuft_auf_review(self):
        level, _, _ = evaluate_action_risk("read_server_status", autonomous=True)
        assert level == "review"


class TestEthicsTriggerModes:
    def test_modus_off_loest_nie_aus(self):
        for tool in ("propose_server_delete", "propose_config_update", "list_my_servers"):
            res = should_trigger_ethics(tool, ethics_mode="off")
            assert res.should_evaluate is False
            assert res.decision_context.tool_name == tool

    def test_modus_always_loest_immer_aus(self):
        for tool in ("propose_server_delete", "propose_config_update", "list_my_servers"):
            res = should_trigger_ethics(tool, ethics_mode="always")
            assert res.should_evaluate is True

    def test_modus_critical_loest_nur_bei_critical_aus(self):
        # Critical -> True
        res_crit = should_trigger_ethics("propose_server_delete", ethics_mode="critical")
        assert res_crit.should_evaluate is True
        assert res_crit.risk_level == "critical"

        # Review -> False
        res_rev = should_trigger_ethics("propose_config_update", ethics_mode="critical")
        assert res_rev.should_evaluate is False
        assert res_rev.risk_level == "review"

        # Low -> False
        res_low = should_trigger_ethics("list_my_servers", ethics_mode="critical")
        assert res_low.should_evaluate is False
        assert res_low.risk_level == "low"

    def test_modus_auto_loest_bei_review_und_critical_aus(self):
        # Critical -> True
        assert should_trigger_ethics("propose_server_delete", ethics_mode="auto").should_evaluate is True

        # Review -> True
        assert should_trigger_ethics("propose_config_update", ethics_mode="auto").should_evaluate is True
        assert should_trigger_ethics("propose_email_send", ethics_mode="auto").should_evaluate is True

        # Low -> False
        assert should_trigger_ethics("list_my_servers", ethics_mode="auto").should_evaluate is False
