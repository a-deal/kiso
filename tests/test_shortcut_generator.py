"""Tests for the iOS Shortcut generator."""

import plistlib
import pytest

from engine.shortcuts.generator import generate_shortcut, QUANTITY_METRICS


class TestGenerateShortcut:
    """Test shortcut file generation."""

    def test_returns_bytes(self):
        result = generate_shortcut(user_id="test", api_token="tok123")
        assert isinstance(result, bytes)

    def test_valid_binary_plist(self):
        result = generate_shortcut(user_id="test", api_token="tok123")
        # Should be parseable as a binary plist
        parsed = plistlib.loads(result)
        assert isinstance(parsed, dict)

    def test_has_required_top_level_keys(self):
        result = generate_shortcut(user_id="test", api_token="tok123")
        parsed = plistlib.loads(result)
        assert "WFWorkflowActions" in parsed
        assert "WFWorkflowIcon" in parsed
        assert "WFWorkflowClientVersion" in parsed

    def test_actions_array_not_empty(self):
        result = generate_shortcut(user_id="test", api_token="tok123")
        parsed = plistlib.loads(result)
        actions = parsed["WFWorkflowActions"]
        assert len(actions) > 0

    def test_contains_health_sample_actions(self):
        result = generate_shortcut(user_id="test", api_token="tok123")
        parsed = plistlib.loads(result)
        actions = parsed["WFWorkflowActions"]
        health_actions = [
            a for a in actions
            if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.filter.health.quantity"
        ]
        # 8 quantity metrics + 1 sleep = 9 health sample queries
        assert len(health_actions) == 9

    def test_contains_post_action(self):
        result = generate_shortcut(user_id="test", api_token="tok123")
        parsed = plistlib.loads(result)
        actions = parsed["WFWorkflowActions"]
        post_actions = [
            a for a in actions
            if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.downloadurl"
        ]
        assert len(post_actions) == 1

    def test_post_action_uses_correct_method(self):
        result = generate_shortcut(user_id="test", api_token="tok123")
        parsed = plistlib.loads(result)
        actions = parsed["WFWorkflowActions"]
        post = next(
            a for a in actions
            if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.downloadurl"
        )
        assert post["WFWorkflowActionParameters"]["WFHTTPMethod"] == "POST"

    def test_post_url_is_default(self):
        result = generate_shortcut(user_id="test", api_token="tok123")
        parsed = plistlib.loads(result)
        actions = parsed["WFWorkflowActions"]
        post = next(
            a for a in actions
            if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.downloadurl"
        )
        url = post["WFWorkflowActionParameters"]["WFURL"]["Value"]["string"]
        assert "ingest_health_snapshot" in url

    def test_custom_api_url(self):
        result = generate_shortcut(
            user_id="test", api_token="tok123",
            api_url="https://custom.example.com/api/health"
        )
        parsed = plistlib.loads(result)
        actions = parsed["WFWorkflowActions"]
        post = next(
            a for a in actions
            if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.downloadurl"
        )
        url = post["WFWorkflowActionParameters"]["WFURL"]["Value"]["string"]
        assert url == "https://custom.example.com/api/health"

    def test_user_id_baked_in(self):
        result = generate_shortcut(user_id="paul", api_token="tok123")
        parsed = plistlib.loads(result)
        actions = parsed["WFWorkflowActions"]
        # Find the dictionary action that builds the payload (has token + user_id)
        dict_actions = [
            a for a in actions
            if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.dictionary"
        ]
        # Last dict action is the payload dict
        payload_dict = dict_actions[-1]
        items = payload_dict["WFWorkflowActionParameters"]["WFItems"]["Value"]["WFDictionaryFieldValueItems"]
        user_id_item = next(
            item for item in items
            if item["WFKey"]["Value"]["string"] == "user_id"
        )
        assert user_id_item["WFValue"]["Value"]["string"] == "paul"

    def test_api_token_baked_in(self):
        result = generate_shortcut(user_id="test", api_token="my_secret_token")
        parsed = plistlib.loads(result)
        actions = parsed["WFWorkflowActions"]
        dict_actions = [
            a for a in actions
            if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.dictionary"
        ]
        payload_dict = dict_actions[-1]
        items = payload_dict["WFWorkflowActionParameters"]["WFItems"]["Value"]["WFDictionaryFieldValueItems"]
        token_item = next(
            item for item in items
            if item["WFKey"]["Value"]["string"] == "token"
        )
        assert token_item["WFValue"]["Value"]["string"] == "my_secret_token"

    def test_all_quantity_metrics_present(self):
        result = generate_shortcut(user_id="test", api_token="tok123")
        parsed = plistlib.loads(result)
        actions = parsed["WFWorkflowActions"]

        # Collect all health metric names from Find Health Samples actions
        metric_names = set()
        for a in actions:
            if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.filter.health.quantity":
                templates = a["WFWorkflowActionParameters"]["WFContentItemFilter"]["Value"]["WFActionParameterFilterTemplates"]
                for t in templates:
                    if "Enumeration" in t.get("Values", {}):
                        metric_names.add(t["Values"]["Enumeration"]["Value"])

        expected = {name for name, _, _ in QUANTITY_METRICS}
        expected.add("Sleep Analysis")
        assert metric_names == expected

    def test_contains_set_variable_actions(self):
        result = generate_shortcut(user_id="test", api_token="tok123")
        parsed = plistlib.loads(result)
        actions = parsed["WFWorkflowActions"]
        var_actions = [
            a for a in actions
            if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.setvariable"
        ]
        # 8 quantity metrics + sleep_start + sleep_end + metricsDict = 11 variables
        assert len(var_actions) == 11

    def test_contains_dictionary_actions(self):
        result = generate_shortcut(user_id="test", api_token="tok123")
        parsed = plistlib.loads(result)
        actions = parsed["WFWorkflowActions"]
        dict_actions = [
            a for a in actions
            if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.dictionary"
        ]
        # metrics dict + payload dict = 2
        assert len(dict_actions) == 2

    def test_unique_uuids(self):
        result = generate_shortcut(user_id="test", api_token="tok123")
        parsed = plistlib.loads(result)
        actions = parsed["WFWorkflowActions"]
        uuids = [
            a["WFWorkflowActionParameters"]["UUID"]
            for a in actions
            if "UUID" in a.get("WFWorkflowActionParameters", {})
        ]
        assert len(uuids) == len(set(uuids)), "UUIDs should be unique"

    def test_different_users_get_different_shortcuts(self):
        a = generate_shortcut(user_id="alice", api_token="tok123")
        b = generate_shortcut(user_id="bob", api_token="tok123")
        assert a != b

    def test_sleep_properties_extracted(self):
        result = generate_shortcut(user_id="test", api_token="tok123")
        parsed = plistlib.loads(result)
        actions = parsed["WFWorkflowActions"]
        prop_actions = [
            a for a in actions
            if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.properties.health.quantity"
        ]
        prop_names = [
            a["WFWorkflowActionParameters"]["WFContentItemPropertyName"]
            for a in prop_actions
        ]
        assert "Start Date" in prop_names
        assert "End Date" in prop_names
        assert "Value" in prop_names
