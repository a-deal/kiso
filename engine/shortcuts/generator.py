"""Generate personalized iOS Shortcuts for Apple Health data sync.

Creates a .shortcut file (binary plist) that reads HealthKit data and
POSTs it to the health-engine API. Each file is personalized with the
user's ID and API token baked in.

Usage:
    from engine.shortcuts.generator import generate_shortcut
    shortcut_bytes = generate_shortcut(user_id="paul", api_token="xxx")
"""

import plistlib
import uuid


def _uuid():
    return str(uuid.uuid4()).upper()


# Health metrics we want to read, with their Shortcuts display names and units
QUANTITY_METRICS = [
    ("Resting Heart Rate", "count/min", "resting_hr"),
    ("Heart Rate Variability", "ms", "hrv_sdnn"),
    ("Step Count", "count", "steps"),
    ("Weight", "lb", "weight_lbs"),
    ("VO2 Max", "mL/kg·min", "vo2_max"),
    ("Oxygen Saturation", "%", "blood_oxygen"),
    ("Active Energy", "kcal", "active_calories"),
    ("Respiratory Rate", "count/min", "respiratory_rate"),
]

# Sleep is a category type, handled separately
SLEEP_METRIC = ("Sleep Analysis", "sleep_hours")


def _text_token(s: str) -> dict:
    """Create a plain text token (no variable substitution)."""
    return {
        "Value": {
            "string": s,
            "attachmentsByRange": {},
        },
        "WFSerializationType": "WFTextTokenString",
    }


def _action_ref(output_uuid: str, output_name: str) -> dict:
    """Reference the output of a previous action by UUID."""
    return {
        "Value": {
            "OutputUUID": output_uuid,
            "Type": "ActionOutput",
            "OutputName": output_name,
        },
        "WFSerializationType": "WFTextTokenAttachment",
    }


def _var_ref(var_name: str) -> dict:
    """Reference a named variable in a text field."""
    return {
        "Value": {
            "string": "\ufffc",
            "attachmentsByRange": {
                "{0, 1}": {
                    "VariableName": var_name,
                    "Type": "Variable",
                },
            },
        },
        "WFSerializationType": "WFTextTokenString",
    }


def _dict_text_item(key: str, value_token: dict) -> dict:
    """Create a dictionary item with a text value."""
    return {
        "WFKey": _text_token(key),
        "WFItemType": 0,  # 0 = Text
        "WFValue": value_token,
    }


def _dict_number_item(key: str, value_token: dict) -> dict:
    """Create a dictionary item with a number value."""
    return {
        "WFKey": _text_token(key),
        "WFItemType": 1,  # 1 = Number
        "WFValue": value_token,
    }


def _find_health_samples(metric_name: str, unit: str, limit: int = 1) -> tuple[str, dict]:
    """Create a 'Find Health Samples' action for a quantity type.
    Returns (uuid, action_dict)."""
    action_uuid = _uuid()
    action = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.filter.health.quantity",
        "WFWorkflowActionParameters": {
            "UUID": action_uuid,
            "WFContentItemSortProperty": "Start Date",
            "WFContentItemSortOrder": "Latest First",
            "WFContentItemLimitEnabled": True,
            "WFContentItemLimitNumber": float(limit),
            "WFHKSampleFilteringUnit": unit,
            "WFHKSampleFilteringFillMissing": False,
            "WFContentItemFilter": {
                "Value": {
                    "WFActionParameterFilterPrefix": 1,
                    "WFActionParameterFilterTemplates": [
                        {
                            "Bounded": True,
                            "Operator": 4,  # "is" operator
                            "Values": {
                                "Enumeration": {
                                    "Value": metric_name,
                                    "WFSerializationType": "WFStringSubstitutableState",
                                },
                            },
                            "Removable": False,
                            "Property": "Type",
                        },
                    ],
                    "WFContentPredicateBoundedDate": False,
                },
                "WFSerializationType": "WFContentPredicateTableTemplate",
            },
        },
    }
    return action_uuid, action


def _get_health_value(input_uuid: str) -> tuple[str, dict]:
    """Get the 'Value' property from health samples output.
    Returns (uuid, action_dict)."""
    action_uuid = _uuid()
    action = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.properties.health.quantity",
        "WFWorkflowActionParameters": {
            "UUID": action_uuid,
            "WFContentItemPropertyName": "Value",
            "WFInput": _action_ref(input_uuid, "Health Samples"),
        },
    }
    return action_uuid, action


def _set_variable(input_uuid: str, input_name: str, var_name: str) -> dict:
    """Set a variable from a previous action's output."""
    return {
        "WFWorkflowActionIdentifier": "is.workflow.actions.setvariable",
        "WFWorkflowActionParameters": {
            "WFVariableName": var_name,
            "WFInput": _action_ref(input_uuid, input_name),
        },
    }


def _find_sleep_samples() -> tuple[str, dict]:
    """Find sleep analysis samples (category type, not quantity).
    Returns (uuid, action_dict)."""
    action_uuid = _uuid()
    action = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.filter.health.quantity",
        "WFWorkflowActionParameters": {
            "UUID": action_uuid,
            "WFContentItemSortProperty": "Start Date",
            "WFContentItemSortOrder": "Latest First",
            "WFContentItemLimitEnabled": True,
            "WFContentItemLimitNumber": 1.0,
            "WFHKSampleFilteringFillMissing": False,
            "WFContentItemFilter": {
                "Value": {
                    "WFActionParameterFilterPrefix": 1,
                    "WFActionParameterFilterTemplates": [
                        {
                            "Bounded": True,
                            "Operator": 4,
                            "Values": {
                                "Enumeration": {
                                    "Value": "Sleep Analysis",
                                    "WFSerializationType": "WFStringSubstitutableState",
                                },
                            },
                            "Removable": False,
                            "Property": "Type",
                        },
                    ],
                    "WFContentPredicateBoundedDate": False,
                },
                "WFSerializationType": "WFContentPredicateTableTemplate",
            },
        },
    }
    return action_uuid, action


def _get_sleep_property(input_uuid: str, prop: str) -> tuple[str, dict]:
    """Get a property (Start Date, End Date, Value) from sleep samples."""
    action_uuid = _uuid()
    action = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.properties.health.quantity",
        "WFWorkflowActionParameters": {
            "UUID": action_uuid,
            "WFContentItemPropertyName": prop,
            "WFInput": _action_ref(input_uuid, "Health Samples"),
        },
    }
    return action_uuid, action


def generate_shortcut(user_id: str, api_token: str,
                      api_url: str = "https://auth.mybaseline.health/api/ingest_health_snapshot") -> bytes:
    """Generate a personalized .shortcut file as bytes.

    The shortcut:
    1. Reads 8 quantity metrics + sleep from Apple Health
    2. Builds a JSON payload with user_id and token baked in
    3. POSTs to the health-engine API

    Args:
        user_id: The user's ID (baked into the shortcut)
        api_token: API authentication token (baked in)
        api_url: The API endpoint URL

    Returns:
        bytes: Binary plist data (unsigned .shortcut file)
    """
    actions = []
    metric_vars = {}  # metric_key -> variable_name

    # Step 1: Read each quantity metric from HealthKit
    for metric_name, unit, metric_key in QUANTITY_METRICS:
        var_name = f"val_{metric_key}"

        # Find health samples
        find_uuid, find_action = _find_health_samples(metric_name, unit, limit=1)
        actions.append(find_action)

        # Get the value
        val_uuid, val_action = _get_health_value(find_uuid)
        actions.append(val_action)

        # Store in variable
        actions.append(_set_variable(val_uuid, "Value", var_name))
        metric_vars[metric_key] = var_name

    # Step 2: Read sleep data
    sleep_uuid, sleep_action = _find_sleep_samples()
    actions.append(sleep_action)

    # Get sleep start time
    start_uuid, start_action = _get_sleep_property(sleep_uuid, "Start Date")
    actions.append(start_action)
    actions.append(_set_variable(start_uuid, "Start Date", "val_sleep_start"))

    # Get sleep end time
    end_uuid, end_action = _get_sleep_property(sleep_uuid, "End Date")
    actions.append(end_action)
    actions.append(_set_variable(end_uuid, "End Date", "val_sleep_end"))

    # Step 3: Build the metrics dictionary
    metrics_items = []
    for metric_key, var_name in metric_vars.items():
        metrics_items.append(_dict_text_item(metric_key, _var_ref(var_name)))

    # Add sleep times
    metrics_items.append(_dict_text_item("sleep_start", _var_ref("val_sleep_start")))
    metrics_items.append(_dict_text_item("sleep_end", _var_ref("val_sleep_end")))

    metrics_uuid = _uuid()
    metrics_dict_action = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.dictionary",
        "WFWorkflowActionParameters": {
            "UUID": metrics_uuid,
            "WFItems": {
                "Value": {
                    "WFDictionaryFieldValueItems": metrics_items,
                },
                "WFSerializationType": "WFDictionaryFieldValue",
            },
        },
    }
    actions.append(metrics_dict_action)
    actions.append(_set_variable(metrics_uuid, "Dictionary", "metricsDict"))

    # Step 4: Build the top-level payload dictionary
    payload_items = [
        _dict_text_item("token", _text_token(api_token)),
        _dict_text_item("user_id", _text_token(user_id)),
        {
            "WFKey": _text_token("metrics"),
            "WFItemType": 3,  # 3 = Dictionary
            "WFValue": _var_ref("metricsDict"),
        },
    ]

    payload_uuid = _uuid()
    payload_dict_action = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.dictionary",
        "WFWorkflowActionParameters": {
            "UUID": payload_uuid,
            "WFItems": {
                "Value": {
                    "WFDictionaryFieldValueItems": payload_items,
                },
                "WFSerializationType": "WFDictionaryFieldValue",
            },
        },
    }
    actions.append(payload_dict_action)

    # Step 5: POST to the API
    post_action = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
        "WFWorkflowActionParameters": {
            "UUID": _uuid(),
            "WFURL": {
                "Value": {
                    "string": api_url,
                    "attachmentsByRange": {},
                },
                "WFSerializationType": "WFTextTokenString",
            },
            "WFHTTPMethod": "POST",
            "WFHTTPBodyType": "JSON",
            "WFJSONValues": {
                "Value": {
                    "WFDictionaryFieldValueItems": [],
                },
                "WFSerializationType": "WFDictionaryFieldValue",
            },
            "WFRequestVariable": _action_ref(payload_uuid, "Dictionary"),
            "ShowHeaders": False,
        },
    }
    actions.append(post_action)

    # Build the full shortcut structure
    shortcut = {
        "WFWorkflowName": "Baseline Health Sync",
        "WFWorkflowClientVersion": "1177.2",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor": 4282601983,  # Green
            "WFWorkflowIconGlyphNumber": 59764,  # Heart glyph
        },
        "WFWorkflowTypes": ["NCWidget", "Watch"],
        "WFWorkflowInputContentItemClasses": [
            "WFStringContentItem",
        ],
        "WFWorkflowImportQuestions": [],
        "WFWorkflowActions": actions,
    }

    return plistlib.dumps(shortcut, fmt=plistlib.FMT_BINARY)
