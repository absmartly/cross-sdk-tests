#!/usr/bin/env python3
"""
Build extended test scenarios for cross-SDK testing.
This script generates comprehensive test scenarios not covered in the original 33 scenarios.

ANALYSIS SUMMARY:
- JavaScript SDK: 127 test cases in context.test.js
- Python SDK: 60+ test cases
- Ruby SDK: 65+ test cases
- Java SDK: 80+ test cases
- Go SDK: Multiple test files

CATEGORIES COVERED:
1. Error Handling & Validation (34-39)
2. Treatment/Exposure Edge Cases (40-42)
3. Audience Re-evaluation (43-47)
4. Variable Access Edge Cases (48-52)
5. Custom Fields Comprehensive (53-64)
6. Override & Custom Assignment Edge Cases (65-66)
7. Publish & Timing (67)

TOTAL: 34 additional scenarios (scenarios 34-67)
"""

import json
import copy
import sys

# Base experiment data structures
BASE_EXPERIMENT = {
    "id": 1,
    "name": "exp_test_ab",
    "iteration": 1,
    "unitType": "session_id",
    "seedHi": 3603515,
    "seedLo": 233373850,
    "split": [0.5, 0.5],
    "trafficSeedHi": 449867249,
    "trafficSeedLo": 455443629,
    "trafficSplit": [0, 1],
    "fullOnVariant": 0,
    "applications": [{"name": "website"}],
    "variants": [
        {"name": "A", "config": None},
        {"name": "B", "config": '{"banner.border":1,"banner.size":"large"}'}
    ],
    "audience": "",
    "audienceStrict": False,
    "customFieldValues": None
}

EXPERIMENT_WITH_VARIABLES = {
    "id": 2,
    "name": "exp_test_abc",
    "iteration": 1,
    "unitType": "session_id",
    "seedHi": 55006150,
    "seedLo": 47189152,
    "split": [0.34, 0.33, 0.33],
    "trafficSeedHi": 705671872,
    "trafficSeedLo": 212903484,
    "trafficSplit": [0, 1],
    "fullOnVariant": 0,
    "applications": [{"name": "website"}],
    "variants": [
        {"name": "A", "config": None},
        {"name": "B", "config": '{"button.color":"blue"}'},
        {"name": "C", "config": '{"button.color":"red"}'}
    ],
    "audience": "",
    "audienceStrict": False,
    "customFieldValues": [
        {"name": "country", "value": "US,PT,ES,DE,FR", "type": "string"},
        {"name": "json_object", "value": '{"123":1,"456":0}', "type": "json"},
        {"name": "json_array", "value": '["hello", "world"]', "type": "json"},
        {"name": "json_number", "value": "123", "type": "json"},
        {"name": "json_string", "value": '"hello"', "type": "json"},
        {"name": "json_boolean", "value": "true", "type": "json"},
        {"name": "json_null", "value": "null", "type": "json"},
        {"name": "json_invalid", "value": "invalid", "type": "json"}
    ]
}

EXPERIMENT_WITH_CUSTOM_FIELDS = {
    "id": 5,
    "name": "exp_test_custom_fields",
    "iteration": 1,
    "unitType": "session_id",
    "seedHi": 9372617,
    "seedLo": 121364805,
    "split": [0.5, 0.5],
    "trafficSeedHi": 318746944,
    "trafficSeedLo": 359812364,
    "trafficSplit": [0, 1],
    "fullOnVariant": 0,
    "applications": [{"name": "website"}],
    "variants": [
        {"name": "A", "config": None},
        {"name": "B", "config": '{"submit.size":"sm"}'}
    ],
    "audience": None,
    "audienceStrict": False,
    "customFieldValues": [
        {"name": "country", "value": "US,PT,ES", "type": "string"},
        {"name": "languages", "value": "en-US,en-GB,pt-PT,pt-BR,es-ES,es-MX", "type": "string"},
        {"name": "text_field", "value": "hello text", "type": "text"},
        {"name": "string_field", "value": "hello string", "type": "string"},
        {"name": "number_field", "value": "123", "type": "number"},
        {"name": "boolean_field", "value": "true", "type": "boolean"},
        {"name": "false_boolean_field", "value": "false", "type": "boolean"},
        {"name": "invalid_type_field", "value": "invalid", "type": "invalid"}
    ]
}

AUDIENCE_EXPERIMENT = {
    "id": 1,
    "name": "exp_test_ab",
    "iteration": 1,
    "unitType": "session_id",
    "seedHi": 3603515,
    "seedLo": 233373850,
    "split": [0.5, 0.5],
    "trafficSeedHi": 449867249,
    "trafficSeedLo": 455443629,
    "trafficSplit": [0, 1],
    "fullOnVariant": 0,
    "applications": [{"name": "website"}],
    "variants": [
        {"name": "A", "config": None},
        {"name": "B", "config": '{"banner.border":1,"banner.size":"large"}'}
    ],
    "audience": '{"filter":[{"gte":[{"var":"age"},{"value":20}]}]}',
    "audienceStrict": False,
    "customFieldValues": None
}

AUDIENCE_STRICT_EXPERIMENT = copy.deepcopy(AUDIENCE_EXPERIMENT)
AUDIENCE_STRICT_EXPERIMENT["audienceStrict"] = True
AUDIENCE_STRICT_EXPERIMENT["variants"] = [
    {"name": "A", "config": '{"banner.size":"tiny"}'},
    {"name": "B", "config": '{"banner.border":1,"banner.size":"large"}'}
]


def generate_extended_scenarios():
    """Generate all extended test scenarios"""
    scenarios = []
    start_num = 34

    # === ERROR HANDLING & VALIDATION ===

    # 34 - Invalid Unit Empty String
    scenarios.append({
        "name": f"{start_num:02d} - Error Handling - Invalid Unit Empty String",
        "description": "Should throw error when setting unit with empty string",
        "contextData": {"experiments": []},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": []}}]
                }
            },
            {
                "action": "setUnit",
                "params": {"unitType": "user_id", "uid": ""},
                "expect": {"error": "Unit 'user_id' UID must not be blank"}
            }
        ]
    })
    start_num += 1

    # 35 - Duplicate Unit Different Value
    scenarios.append({
        "name": f"{start_num:02d} - Error Handling - Duplicate Unit Different Value",
        "description": "Should throw error when setting same unit type twice with different values",
        "contextData": {"experiments": []},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "original_value"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": []}}]
                }
            },
            {
                "action": "setUnit",
                "params": {"unitType": "session_id", "uid": "different_value"},
                "expect": {"error": "Unit 'session_id' already set"}
            }
        ]
    })
    start_num += 1

    # 36 - Duplicate Unit Same Value (should NOT error)
    scenarios.append({
        "name": f"{start_num:02d} - Unit Management - Duplicate Unit Same Value OK",
        "description": "Should NOT throw when setting same unit type with same value",
        "contextData": {"experiments": []},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "same_value"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": []}}]
                }
            },
            {
                "action": "setUnit",
                "params": {"unitType": "session_id", "uid": "same_value"},
                "expect": {"result": None, "events": []}
            }
        ]
    })
    start_num += 1

    # 37 - Invalid Goal Properties - Number
    scenarios.append({
        "name": f"{start_num:02d} - Error Handling - Invalid Goal Properties Number",
        "description": "Should throw when goal properties is a number instead of object",
        "contextData": {"experiments": []},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": []}}]
                }
            },
            {
                "action": "track",
                "params": {"goalName": "purchase", "properties": 125.0},
                "expect": {"error": "Goal 'purchase' properties must be of type object"}
            }
        ]
    })
    start_num += 1

    # 38 - Invalid Goal Properties - String
    scenarios.append({
        "name": f"{start_num:02d} - Error Handling - Invalid Goal Properties String",
        "description": "Should throw when goal properties is a string instead of object",
        "contextData": {"experiments": []},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": []}}]
                }
            },
            {
                "action": "track",
                "params": {"goalName": "purchase", "properties": "invalid"},
                "expect": {"error": "Goal 'purchase' properties must be of type object"}
            }
        ]
    })
    start_num += 1

    # 39 - Invalid Goal Properties - Array
    scenarios.append({
        "name": f"{start_num:02d} - Error Handling - Invalid Goal Properties Array",
        "description": "Should throw when goal properties is an array instead of object",
        "contextData": {"experiments": []},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": []}}]
                }
            },
            {
                "action": "track",
                "params": {"goalName": "purchase", "properties": []},
                "expect": {"error": "Goal 'purchase' properties must be of type object"}
            }
        ]
    })
    start_num += 1

    # === TREATMENT/EXPOSURE EDGE CASES ===

    # 40 - Treatment After Peek
    scenarios.append({
        "name": f"{start_num:02d} - Treatment - Queue Exposure After Peek",
        "description": "treatment() should queue exposure even after peek() was called",
        "contextData": {"experiments": [BASE_EXPERIMENT]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "e791e240fcd3df7d238cfc285f475e8152fcc0ec"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 1, "name": "exp_test_ab"}]}}]
                }
            },
            {
                "action": "peek",
                "params": {"experimentName": "exp_test_ab"},
                "expect": {"result": 1, "events": []}
            },
            {
                "action": "treatment",
                "params": {"experimentName": "exp_test_ab"},
                "expect": {
                    "result": 1,
                    "events": [{
                        "type": "exposure",
                        "data": {
                            "id": 1,
                            "name": "exp_test_ab",
                            "unit": "session_id",
                            "variant": 1,
                            "assigned": True,
                            "eligible": True,
                            "overridden": False,
                            "fullOn": False,
                            "custom": False,
                            "audienceMismatch": False
                        }
                    }]
                }
            }
        ]
    })
    start_num += 1

    # 41 - Treatment Unknown Experiment
    scenarios.append({
        "name": f"{start_num:02d} - Treatment - Unknown Experiment Returns Zero",
        "description": "treatment() should return 0 and queue exposure for unknown experiment",
        "contextData": {"experiments": []},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": []}}]
                }
            },
            {
                "action": "treatment",
                "params": {"experimentName": "unknown_exp"},
                "expect": {
                    "result": 0,
                    "events": [{
                        "type": "exposure",
                        "data": {
                            "id": 0,
                            "name": "unknown_exp",
                            "unit": None,
                            "variant": 0,
                            "assigned": False,
                            "eligible": False,
                            "overridden": False,
                            "fullOn": False,
                            "custom": False,
                            "audienceMismatch": False
                        }
                    }]
                }
            }
        ]
    })
    start_num += 1

    # 42 - Treatment No Re-queue Unknown
    scenarios.append({
        "name": f"{start_num:02d} - Treatment - No Re-queue Unknown Experiment",
        "description": "treatment() should not re-queue exposure for unknown experiment",
        "contextData": {"experiments": []},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": []}}]
                }
            },
            {
                "action": "treatment",
                "params": {"experimentName": "unknown_exp"},
                "expect": {
                    "result": 0,
                    "events": [{
                        "type": "exposure",
                        "data": {
                            "id": 0,
                            "name": "unknown_exp",
                            "unit": None,
                            "variant": 0,
                            "assigned": False,
                            "eligible": False,
                            "overridden": False,
                            "fullOn": False,
                            "custom": False,
                            "audienceMismatch": False
                        }
                    }]
                }
            },
            {
                "action": "treatment",
                "params": {"experimentName": "unknown_exp"},
                "expect": {"result": 0, "events": []}
            }
        ]
    })
    start_num += 1

    # === AUDIENCE RE-EVALUATION ===

    # 43 - Audience Re-evaluation Strict Mode
    scenarios.append({
        "name": f"{start_num:02d} - Audience - Re-evaluation Strict Mode",
        "description": "Should re-evaluate audience and queue new exposure when attribute changes in strict mode",
        "contextData": {"experiments": [AUDIENCE_STRICT_EXPERIMENT]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "e791e240fcd3df7d238cfc285f475e8152fcc0ec"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 1, "name": "exp_test_ab"}]}}]
                }
            },
            {
                "action": "treatment",
                "params": {"experimentName": "exp_test_ab"},
                "expect": {
                    "result": 0,
                    "events": [{
                        "type": "exposure",
                        "data": {
                            "id": 1,
                            "name": "exp_test_ab",
                            "unit": "session_id",
                            "variant": 0,
                            "assigned": False,
                            "eligible": True,
                            "overridden": False,
                            "fullOn": False,
                            "custom": False,
                            "audienceMismatch": True
                        }
                    }]
                }
            },
            {
                "action": "attribute",
                "params": {"name": "age", "value": 25},
                "expect": {"result": None, "events": []}
            },
            {
                "action": "treatment",
                "params": {"experimentName": "exp_test_ab"},
                "expect": {
                    "result": 1,
                    "events": [{
                        "type": "exposure",
                        "data": {
                            "id": 1,
                            "name": "exp_test_ab",
                            "unit": "session_id",
                            "variant": 1,
                            "assigned": True,
                            "eligible": True,
                            "overridden": False,
                            "fullOn": False,
                            "custom": False,
                            "audienceMismatch": False
                        }
                    }]
                }
            }
        ]
    })
    start_num += 1

    # 44 - Audience Re-evaluation Non-Strict Mode
    scenarios.append({
        "name": f"{start_num:02d} - Audience - Re-evaluation Non-Strict Mode",
        "description": "Should re-evaluate audience and queue new exposure when attribute changes in non-strict mode",
        "contextData": {"experiments": [AUDIENCE_EXPERIMENT]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "e791e240fcd3df7d238cfc285f475e8152fcc0ec"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 1, "name": "exp_test_ab"}]}}]
                }
            },
            {
                "action": "treatment",
                "params": {"experimentName": "exp_test_ab"},
                "expect": {
                    "result": 1,
                    "events": [{
                        "type": "exposure",
                        "data": {
                            "id": 1,
                            "name": "exp_test_ab",
                            "unit": "session_id",
                            "variant": 1,
                            "assigned": True,
                            "eligible": True,
                            "overridden": False,
                            "fullOn": False,
                            "custom": False,
                            "audienceMismatch": True
                        }
                    }]
                }
            },
            {
                "action": "attribute",
                "params": {"name": "age", "value": 25},
                "expect": {"result": None, "events": []}
            },
            {
                "action": "treatment",
                "params": {"experimentName": "exp_test_ab"},
                "expect": {
                    "result": 1,
                    "events": [{
                        "type": "exposure",
                        "data": {
                            "id": 1,
                            "name": "exp_test_ab",
                            "unit": "session_id",
                            "variant": 1,
                            "assigned": True,
                            "eligible": True,
                            "overridden": False,
                            "fullOn": False,
                            "custom": False,
                            "audienceMismatch": False
                        }
                    }]
                }
            }
        ]
    })
    start_num += 1

    # 45 - Audience No Re-evaluation Without New Attributes
    scenarios.append({
        "name": f"{start_num:02d} - Audience - No Re-evaluation Without New Attributes",
        "description": "Should use cached assignment when no new attributes set",
        "contextData": {"experiments": [AUDIENCE_STRICT_EXPERIMENT]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "e791e240fcd3df7d238cfc285f475e8152fcc0ec"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 1, "name": "exp_test_ab"}]}}]
                }
            },
            {
                "action": "attribute",
                "params": {"name": "age", "value": 15},
                "expect": {"result": None, "events": []}
            },
            {
                "action": "treatment",
                "params": {"experimentName": "exp_test_ab"},
                "expect": {
                    "result": 0,
                    "events": [{
                        "type": "exposure",
                        "data": {
                            "id": 1,
                            "name": "exp_test_ab",
                            "unit": "session_id",
                            "variant": 0,
                            "assigned": False,
                            "eligible": True,
                            "overridden": False,
                            "fullOn": False,
                            "custom": False,
                            "audienceMismatch": True
                        }
                    }]
                }
            },
            {
                "action": "treatment",
                "params": {"experimentName": "exp_test_ab"},
                "expect": {"result": 0, "events": []}
            }
        ]
    })
    start_num += 1

    # 46 - Audience No Re-evaluation Without Audience Filter
    exp_no_audience = copy.deepcopy(BASE_EXPERIMENT)
    exp_no_audience["id"] = 2
    exp_no_audience["name"] = "exp_test_abc"
    exp_no_audience["audience"] = ""
    scenarios.append({
        "name": f"{start_num:02d} - Audience - No Re-evaluation Without Filter",
        "description": "Should not re-evaluate for experiments without audience filter",
        "contextData": {"experiments": [exp_no_audience]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "e791e240fcd3df7d238cfc285f475e8152fcc0ec"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 2, "name": "exp_test_abc"}]}}]
                }
            },
            {
                "action": "treatment",
                "params": {"experimentName": "exp_test_abc"},
                "expect": {
                    "result": 1,
                    "events": [{
                        "type": "exposure",
                        "data": {
                            "id": 2,
                            "name": "exp_test_abc",
                            "unit": "session_id",
                            "variant": 1,
                            "assigned": True,
                            "eligible": True,
                            "overridden": False,
                            "fullOn": False,
                            "custom": False,
                            "audienceMismatch": False
                        }
                    }]
                }
            },
            {
                "action": "attribute",
                "params": {"name": "age", "value": 25},
                "expect": {"result": None, "events": []}
            },
            {
                "action": "treatment",
                "params": {"experimentName": "exp_test_abc"},
                "expect": {"result": 1, "events": []}
            }
        ]
    })
    start_num += 1

    # 47 - Attribute Set Before Assignment
    scenarios.append({
        "name": f"{start_num:02d} - Audience - Attribute Set Before Assignment",
        "description": "Should not trigger re-evaluation when attribute set before first treatment call",
        "contextData": {"experiments": [AUDIENCE_STRICT_EXPERIMENT]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "e791e240fcd3df7d238cfc285f475e8152fcc0ec"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 1, "name": "exp_test_ab"}]}}]
                }
            },
            {
                "action": "attribute",
                "params": {"name": "age", "value": 25},
                "expect": {"result": None, "events": []}
            },
            {
                "action": "treatment",
                "params": {"experimentName": "exp_test_ab"},
                "expect": {
                    "result": 1,
                    "events": [{
                        "type": "exposure",
                        "data": {
                            "id": 1,
                            "name": "exp_test_ab",
                            "unit": "session_id",
                            "variant": 1,
                            "assigned": True,
                            "eligible": True,
                            "overridden": False,
                            "fullOn": False,
                            "custom": False,
                            "audienceMismatch": False
                        }
                    }]
                }
            },
            {
                "action": "treatment",
                "params": {"experimentName": "exp_test_ab"},
                "expect": {"result": 1, "events": []}
            }
        ]
    })
    start_num += 1

    # === VARIABLE ACCESS EDGE CASES ===

    # 48 - Variable Not Assigned
    scenarios.append({
        "name": f"{start_num:02d} - Variables - Not Assigned Returns Default",
        "description": "Should return default value when experiment not assigned",
        "contextData": {"experiments": []},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": []}}]
                }
            },
            {
                "action": "variableValue",
                "params": {"key": "button_color", "defaultValue": "blue"},
                "expect": {"result": "blue", "events": []}
            }
        ]
    })
    start_num += 1

    # 49 - Variable After PeekVariable
    scenarios.append({
        "name": f"{start_num:02d} - Variables - Queue Exposure After Peek",
        "description": "variableValue() should queue exposure after peekVariableValue()",
        "contextData": {"experiments": [EXPERIMENT_WITH_VARIABLES]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "e791e240fcd3df7d238cfc285f475e8152fcc0ec"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 2, "name": "exp_test_abc"}]}}]
                }
            },
            {
                "action": "peekVariableValue",
                "params": {"key": "button.color", "defaultValue": "green"},
                "expect": {"result": "red", "events": []}
            },
            {
                "action": "variableValue",
                "params": {"key": "button.color", "defaultValue": "green"},
                "expect": {
                    "result": "red",
                    "events": [{
                        "type": "exposure",
                        "data": {
                            "id": 2,
                            "name": "exp_test_abc",
                            "unit": "session_id",
                            "variant": 2,
                            "assigned": True,
                            "eligible": True,
                            "overridden": False,
                            "fullOn": False,
                            "custom": False,
                            "audienceMismatch": False
                        }
                    }]
                }
            }
        ]
    })
    start_num += 1

    # 50 - Variable Only Queue Once
    scenarios.append({
        "name": f"{start_num:02d} - Variables - Only Queue Exposure Once",
        "description": "variableValue() should only queue exposure once per experiment",
        "contextData": {"experiments": [EXPERIMENT_WITH_VARIABLES]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "e791e240fcd3df7d238cfc285f475e8152fcc0ec"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 2, "name": "exp_test_abc"}]}}]
                }
            },
            {
                "action": "variableValue",
                "params": {"key": "button.color", "defaultValue": "green"},
                "expect": {
                    "result": "red",
                    "events": [{
                        "type": "exposure",
                        "data": {
                            "id": 2,
                            "name": "exp_test_abc",
                            "unit": "session_id",
                            "variant": 2,
                            "assigned": True,
                            "eligible": True,
                            "overridden": False,
                            "fullOn": False,
                            "custom": False,
                            "audienceMismatch": False
                        }
                    }]
                }
            },
            {
                "action": "variableValue",
                "params": {"key": "button.color", "defaultValue": "green"},
                "expect": {"result": "red", "events": []}
            }
        ]
    })
    start_num += 1

    # 51 - PeekVariable On Override
    scenarios.append({
        "name": f"{start_num:02d} - Variables - Peek Override Variant",
        "description": "peekVariableValue() should return value for override variant",
        "contextData": {"experiments": [EXPERIMENT_WITH_VARIABLES]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "e791e240fcd3df7d238cfc285f475e8152fcc0ec"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 2, "name": "exp_test_abc"}]}}]
                }
            },
            {
                "action": "setOverride",
                "params": {"experimentName": "exp_test_abc", "variant": 1},
                "expect": {"result": None, "events": []}
            },
            {
                "action": "peekVariableValue",
                "params": {"key": "button.color", "defaultValue": "green"},
                "expect": {"result": "blue", "events": []}
            }
        ]
    })
    start_num += 1

    # 52 - Variable Keys
    scenarios.append({
        "name": f"{start_num:02d} - Variables - Get All Keys",
        "description": "variableKeys() should return all active variable keys",
        "contextData": {"experiments": [EXPERIMENT_WITH_VARIABLES]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "e791e240fcd3df7d238cfc285f475e8152fcc0ec"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 2, "name": "exp_test_abc"}]}}]
                }
            },
            {
                "action": "treatment",
                "params": {"experimentName": "exp_test_abc"},
                "expect": {
                    "result": 2,
                    "events": [{
                        "type": "exposure",
                        "data": {
                            "id": 2,
                            "name": "exp_test_abc",
                            "unit": "session_id",
                            "variant": 2,
                            "assigned": True,
                            "eligible": True,
                            "overridden": False,
                            "fullOn": False,
                            "custom": False,
                            "audienceMismatch": False
                        }
                    }]
                }
            },
            {
                "action": "variableKeys",
                "params": {},
                "expect": {"result": ["button.color"], "events": []}
            }
        ]
    })
    start_num += 1

    # === CUSTOM FIELDS COMPREHENSIVE ===

    # 53 - Custom Field JSON Object
    scenarios.append({
        "name": f"{start_num:02d} - Custom Fields - JSON Object",
        "description": "Should parse JSON object custom field correctly",
        "contextData": {"experiments": [EXPERIMENT_WITH_VARIABLES]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 2, "name": "exp_test_abc"}]}}]
                }
            },
            {
                "action": "customFieldValue",
                "params": {"experimentName": "exp_test_abc", "fieldName": "json_object"},
                "expect": {"result": {"123": 1, "456": 0}, "events": []}
            }
        ]
    })
    start_num += 1

    # 54 - Custom Field JSON Array
    scenarios.append({
        "name": f"{start_num:02d} - Custom Fields - JSON Array",
        "description": "Should parse JSON array custom field correctly",
        "contextData": {"experiments": [EXPERIMENT_WITH_VARIABLES]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 2, "name": "exp_test_abc"}]}}]
                }
            },
            {
                "action": "customFieldValue",
                "params": {"experimentName": "exp_test_abc", "fieldName": "json_array"},
                "expect": {"result": ["hello", "world"], "events": []}
            }
        ]
    })
    start_num += 1

    # 55 - Custom Field JSON Number
    scenarios.append({
        "name": f"{start_num:02d} - Custom Fields - JSON Number",
        "description": "Should parse JSON number custom field correctly",
        "contextData": {"experiments": [EXPERIMENT_WITH_VARIABLES]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 2, "name": "exp_test_abc"}]}}]
                }
            },
            {
                "action": "customFieldValue",
                "params": {"experimentName": "exp_test_abc", "fieldName": "json_number"},
                "expect": {"result": 123, "events": []}
            }
        ]
    })
    start_num += 1

    # 56 - Custom Field JSON String
    scenarios.append({
        "name": f"{start_num:02d} - Custom Fields - JSON String",
        "description": "Should parse JSON string custom field correctly",
        "contextData": {"experiments": [EXPERIMENT_WITH_VARIABLES]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 2, "name": "exp_test_abc"}]}}]
                }
            },
            {
                "action": "customFieldValue",
                "params": {"experimentName": "exp_test_abc", "fieldName": "json_string"},
                "expect": {"result": "hello", "events": []}
            }
        ]
    })
    start_num += 1

    # 57 - Custom Field JSON Boolean
    scenarios.append({
        "name": f"{start_num:02d} - Custom Fields - JSON Boolean",
        "description": "Should parse JSON boolean custom field correctly",
        "contextData": {"experiments": [EXPERIMENT_WITH_VARIABLES]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 2, "name": "exp_test_abc"}]}}]
                }
            },
            {
                "action": "customFieldValue",
                "params": {"experimentName": "exp_test_abc", "fieldName": "json_boolean"},
                "expect": {"result": True, "events": []}
            }
        ]
    })
    start_num += 1

    # 58 - Custom Field JSON Null
    scenarios.append({
        "name": f"{start_num:02d} - Custom Fields - JSON Null",
        "description": "Should parse JSON null custom field correctly",
        "contextData": {"experiments": [EXPERIMENT_WITH_VARIABLES]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 2, "name": "exp_test_abc"}]}}]
                }
            },
            {
                "action": "customFieldValue",
                "params": {"experimentName": "exp_test_abc", "fieldName": "json_null"},
                "expect": {"result": None, "events": []}
            }
        ]
    })
    start_num += 1

    # 59 - Custom Field Number Type
    scenarios.append({
        "name": f"{start_num:02d} - Custom Fields - Number Type",
        "description": "Should parse number custom field type correctly",
        "contextData": {"experiments": [EXPERIMENT_WITH_CUSTOM_FIELDS]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 5, "name": "exp_test_custom_fields"}]}}]
                }
            },
            {
                "action": "customFieldValue",
                "params": {"experimentName": "exp_test_custom_fields", "fieldName": "number_field"},
                "expect": {"result": 123, "events": []}
            }
        ]
    })
    start_num += 1

    # 60 - Custom Field Boolean Type True
    scenarios.append({
        "name": f"{start_num:02d} - Custom Fields - Boolean Type True",
        "description": "Should parse boolean custom field type (true) correctly",
        "contextData": {"experiments": [EXPERIMENT_WITH_CUSTOM_FIELDS]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 5, "name": "exp_test_custom_fields"}]}}]
                }
            },
            {
                "action": "customFieldValue",
                "params": {"experimentName": "exp_test_custom_fields", "fieldName": "boolean_field"},
                "expect": {"result": True, "events": []}
            }
        ]
    })
    start_num += 1

    # 61 - Custom Field Boolean Type False
    scenarios.append({
        "name": f"{start_num:02d} - Custom Fields - Boolean Type False",
        "description": "Should parse boolean custom field type (false) correctly",
        "contextData": {"experiments": [EXPERIMENT_WITH_CUSTOM_FIELDS]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 5, "name": "exp_test_custom_fields"}]}}]
                }
            },
            {
                "action": "customFieldValue",
                "params": {"experimentName": "exp_test_custom_fields", "fieldName": "false_boolean_field"},
                "expect": {"result": False, "events": []}
            }
        ]
    })
    start_num += 1

    # 62 - Custom Field Text Type
    scenarios.append({
        "name": f"{start_num:02d} - Custom Fields - Text Type",
        "description": "Should return text custom field as string",
        "contextData": {"experiments": [EXPERIMENT_WITH_CUSTOM_FIELDS]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 5, "name": "exp_test_custom_fields"}]}}]
                }
            },
            {
                "action": "customFieldValue",
                "params": {"experimentName": "exp_test_custom_fields", "fieldName": "text_field"},
                "expect": {"result": "hello text", "events": []}
            }
        ]
    })
    start_num += 1

    # 63 - Custom Field Keys
    scenarios.append({
        "name": f"{start_num:02d} - Custom Fields - Get All Keys",
        "description": "customFieldKeys() should return all field keys for experiment",
        "contextData": {"experiments": [EXPERIMENT_WITH_CUSTOM_FIELDS]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 5, "name": "exp_test_custom_fields"}]}}]
                }
            },
            {
                "action": "customFieldKeys",
                "params": {"experimentName": "exp_test_custom_fields"},
                "expect": {
                    "result": ["country", "languages", "text_field", "string_field", "number_field",
                              "boolean_field", "false_boolean_field", "invalid_type_field"],
                    "events": []
                }
            }
        ]
    })
    start_num += 1

    # 64 - Custom Field Value Type
    scenarios.append({
        "name": f"{start_num:02d} - Custom Fields - Get Value Type",
        "description": "customFieldValueType() should return field type",
        "contextData": {"experiments": [EXPERIMENT_WITH_CUSTOM_FIELDS]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 5, "name": "exp_test_custom_fields"}]}}]
                }
            },
            {
                "action": "customFieldValueType",
                "params": {"experimentName": "exp_test_custom_fields", "fieldName": "boolean_field"},
                "expect": {"result": "boolean", "events": []}
            }
        ]
    })
    start_num += 1

    # === OVERRIDE & CUSTOM ASSIGNMENT ===

    # 65 - Override Clear Cache
    scenarios.append({
        "name": f"{start_num:02d} - Override - Clear Assignment Cache",
        "description": "setOverride() should clear assignment cache for that experiment",
        "contextData": {"experiments": [BASE_EXPERIMENT]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "e791e240fcd3df7d238cfc285f475e8152fcc0ec"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 1, "name": "exp_test_ab"}]}}]
                }
            },
            {
                "action": "treatment",
                "params": {"experimentName": "exp_test_ab"},
                "expect": {
                    "result": 1,
                    "events": [{
                        "type": "exposure",
                        "data": {
                            "id": 1,
                            "name": "exp_test_ab",
                            "unit": "session_id",
                            "variant": 1,
                            "assigned": True,
                            "eligible": True,
                            "overridden": False,
                            "fullOn": False,
                            "custom": False,
                            "audienceMismatch": False
                        }
                    }]
                }
            },
            {
                "action": "setOverride",
                "params": {"experimentName": "exp_test_ab", "variant": 0},
                "expect": {"result": None, "events": []}
            },
            {
                "action": "treatment",
                "params": {"experimentName": "exp_test_ab"},
                "expect": {
                    "result": 0,
                    "events": [{
                        "type": "exposure",
                        "data": {
                            "id": 1,
                            "name": "exp_test_ab",
                            "unit": "session_id",
                            "variant": 0,
                            "assigned": True,
                            "eligible": True,
                            "overridden": True,
                            "fullOn": False,
                            "custom": False,
                            "audienceMismatch": False
                        }
                    }]
                }
            }
        ]
    })
    start_num += 1

    # 66 - Custom Assignment Clear Cache
    exp_fullon = copy.deepcopy(BASE_EXPERIMENT)
    exp_fullon["fullOnVariant"] = 1
    scenarios.append({
        "name": f"{start_num:02d} - Custom Assignment - Clear Cache",
        "description": "setCustomAssignment() should clear assignment cache",
        "contextData": {"experiments": [exp_fullon]},
        "steps": [
            {
                "action": "createContext",
                "params": {
                    "units": {"session_id": "e791e240fcd3df7d238cfc285f475e8152fcc0ec"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": True, "failed": False, "finalized": False},
                    "events": [{"type": "ready", "data": {"experiments": [{"id": 1, "name": "exp_test_ab"}]}}]
                }
            },
            {
                "action": "treatment",
                "params": {"experimentName": "exp_test_ab"},
                "expect": {
                    "result": 1,
                    "events": [{
                        "type": "exposure",
                        "data": {
                            "id": 1,
                            "name": "exp_test_ab",
                            "unit": "session_id",
                            "variant": 1,
                            "assigned": True,
                            "eligible": True,
                            "overridden": False,
                            "fullOn": True,
                            "custom": False,
                            "audienceMismatch": False
                        }
                    }]
                }
            },
            {
                "action": "setCustomAssignment",
                "params": {"experimentName": "exp_test_ab", "variant": 0},
                "expect": {"result": None, "events": []}
            },
            {
                "action": "treatment",
                "params": {"experimentName": "exp_test_ab"},
                "expect": {
                    "result": 1,
                    "events": []
                }
            }
        ]
    })
    start_num += 1

    # === PUBLISH & TIMING ===

    # 67 - Track Before Ready
    scenarios.append({
        "name": f"{start_num:02d} - Goal Tracking - Before Ready",
        "description": "track() should queue goals before context is ready",
        "contextData": {"experiments": []},
        "steps": [
            {
                "action": "createContextAsync",
                "params": {
                    "units": {"session_id": "test123"},
                    "options": {"publishDelay": -1}
                },
                "expect": {
                    "result": {"ready": False, "failed": False, "finalized": False},
                    "events": []
                }
            },
            {
                "action": "track",
                "params": {"goalName": "purchase", "properties": {"amount": 125.5}},
                "expect": {"result": None, "events": [
                    {
                        "type": "goal",
                        "data": {
                            "name": "purchase",
                            "properties": {"amount": 125.5}
                        }
                    }
                ]}
            },
            {
                "action": "pending",
                "params": {},
                "expect": {"result": 1, "events": []}
            }
        ]
    })
    start_num += 1

    return scenarios, start_num


def main():
    scenarios, next_num = generate_extended_scenarios()

    print(f"Generated {len(scenarios)} extended test scenarios (34-{next_num-1})")
    print("\nWriting to test_scenarios_extended.json...")

    with open('test_scenarios_extended.json', 'w') as f:
        json.dump(scenarios, f, indent=2)

    print(f"Done! Created {len(scenarios)} new test scenarios.")
    print(f"Next scenario number: {next_num}")


if __name__ == "__main__":
    main()
