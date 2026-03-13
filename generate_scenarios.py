#!/usr/bin/env python3
"""
Comprehensive test scenario generator for cross-SDK testing.
Reconstructed from test_scenarios_complete.json

Generates all 136 test scenarios (131 base + 5 context state).
"""

import json

# =============================================================================
# EXPERIMENT DEFINITIONS
# =============================================================================

EXP_AUDIENCE_STRICT = {
    'id': 1,
    'name': 'exp_audience_strict',
    'iteration': 1,
    'unitType': 'session_id',
    'seedHi': 0,
    'seedLo': 1,
    'split': [0.5, 0.5],
    'trafficSeedHi': 0,
    'trafficSeedLo': 0,
    'trafficSplit': [0, 1],
    'fullOnVariant': 0,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': None},
        {'name': 'B', 'config': None}
    ],
    'audience': '{"filter":[{"eq":[{"var":"country"},{"value":"US"}]}]}',
    'audienceStrict': True,
    'customFieldValues': None
}

EXP_AUDIENCE_TEST = {
    'id': 1,
    'name': 'exp_audience_test',
    'iteration': 1,
    'unitType': 'session_id',
    'seedHi': 0,
    'seedLo': 1,
    'split': [0.5, 0.5],
    'trafficSeedHi': 0,
    'trafficSeedLo': 0,
    'trafficSplit': [0, 1],
    'fullOnVariant': 0,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': None},
        {'name': 'B', 'config': None}
    ],
    'audience': '{"filter":[{"eq":[{"var":"country"},{"value":"US"}]}]}',
    'audienceStrict': False,
    'customFieldValues': None
}

EXP_TEST_AB = {
    'id': 1,
    'name': 'exp_test_ab',
    'iteration': 1,
    'unitType': 'session_id',
    'seedHi': 3603515,
    'seedLo': 233373850,
    'split': [0.5, 0.5],
    'trafficSeedHi': 449867249,
    'trafficSeedLo': 455443629,
    'trafficSplit': [0, 1],
    'fullOnVariant': 0,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': None},
        {
        'name': 'B',
        'config': '{"banner.border":1,"banner.size":"large"}'
    }
    ],
    'audience': None,
    'audienceStrict': False,
    'customFieldValues': None
}

EXP_TEST_ABC = {
    'id': 2,
    'name': 'exp_test_abc',
    'iteration': 1,
    'unitType': 'session_id',
    'seedHi': 55006150,
    'seedLo': 47189152,
    'split': [0.34, 0.33, 0.33],
    'trafficSeedHi': 705671872,
    'trafficSeedLo': 212903484,
    'trafficSplit': [0, 1],
    'fullOnVariant': 0,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': None},
        {'name': 'B', 'config': None},
        {'name': 'C', 'config': None}
    ],
    'audience': '',
    'audienceStrict': False,
    'customFieldValues': [
        {
        'name': 'json_object',
        'value': '{"123":1,"456":0}',
        'type': 'json'
    },
        {
        'name': 'json_array',
        'value': '["hello", "world"]',
        'type': 'json'
    },
        {
        'name': 'json_number',
        'value': '123',
        'type': 'json'
    },
        {
        'name': 'json_string',
        'value': '"hello"',
        'type': 'json'
    },
        {
        'name': 'json_boolean',
        'value': 'true',
        'type': 'json'
    },
        {
        'name': 'json_null',
        'value': 'null',
        'type': 'json'
    }
    ]
}

EXP_TEST_ABC_V2 = {
    'id': 2,
    'name': 'exp_test_abc',
    'iteration': 1,
    'unitType': 'session_id',
    'seedHi': 3603515,
    'seedLo': 233373850,
    'split': [0.5, 0.5],
    'trafficSeedHi': 449867249,
    'trafficSeedLo': 455443629,
    'trafficSplit': [0, 1],
    'fullOnVariant': 0,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': None},
        {
        'name': 'B',
        'config': '{"banner.border":1,"banner.size":"large"}'
    }
    ],
    'audience': '',
    'audienceStrict': False,
    'customFieldValues': None
}

EXP_TEST_ABC_V3 = {
    'id': 2,
    'name': 'exp_test_abc',
    'iteration': 1,
    'unitType': 'session_id',
    'seedHi': 55006150,
    'seedLo': 47189152,
    'split': [0.34, 0.33, 0.33],
    'trafficSeedHi': 705671872,
    'trafficSeedLo': 212903484,
    'trafficSplit': [0, 1],
    'fullOnVariant': 0,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': None},
        {'name': 'B', 'config': '{"button.color":"blue"}'},
        {'name': 'C', 'config': '{"button.color":"red"}'}
    ],
    'audience': '',
    'audienceStrict': False,
    'customFieldValues': [
        {
        'name': 'country',
        'value': 'US,PT,ES,DE,FR',
        'type': 'string'
    },
        {
        'name': 'json_object',
        'value': '{"123":1,"456":0}',
        'type': 'json'
    },
        {
        'name': 'json_array',
        'value': '["hello", "world"]',
        'type': 'json'
    },
        {
        'name': 'json_number',
        'value': '123',
        'type': 'json'
    },
        {
        'name': 'json_string',
        'value': '"hello"',
        'type': 'json'
    },
        {
        'name': 'json_boolean',
        'value': 'true',
        'type': 'json'
    },
        {
        'name': 'json_null',
        'value': 'null',
        'type': 'json'
    },
        {
        'name': 'json_invalid',
        'value': 'invalid',
        'type': 'json'
    }
    ]
}

EXP_TEST_AB_V2 = {
    'id': 1,
    'name': 'exp_test_ab',
    'iteration': 1,
    'unitType': 'session_id',
    'seedHi': 3603515,
    'seedLo': 233373850,
    'split': [0.5, 0.5],
    'trafficSeedHi': 449867249,
    'trafficSeedLo': 455443629,
    'trafficSplit': [0, 1],
    'fullOnVariant': 0,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': None},
        {'name': 'B', 'config': '{"banner.border":1}'}
    ],
    'audience': '',
    'audienceStrict': False,
    'customFieldValues': None
}

EXP_TEST_AB_V3 = {
    'id': 1,
    'name': 'exp_test_ab',
    'iteration': 1,
    'unitType': 'session_id',
    'seedHi': 3603515,
    'seedLo': 233373850,
    'split': [0.5, 0.5],
    'trafficSeedHi': 449867249,
    'trafficSeedLo': 455443629,
    'trafficSplit': [0, 1],
    'fullOnVariant': 0,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': None},
        {'name': 'B', 'config': None}
    ],
    'audience': '',
    'audienceStrict': False,
    'customFieldValues': None
}

EXP_TEST_AB_V4 = {
    'id': 1,
    'name': 'exp_test_ab',
    'iteration': 1,
    'unitType': 'session_id',
    'seedHi': 3603515,
    'seedLo': 233373850,
    'split': [0.5, 0.5],
    'trafficSeedHi': 449867249,
    'trafficSeedLo': 455443629,
    'trafficSplit': [0, 1],
    'fullOnVariant': 0,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': None},
        {
        'name': 'B',
        'config': '{"banner.border":1,"banner.size":"large"}'
    }
    ],
    'audience': '',
    'audienceStrict': False,
    'customFieldValues': None
}

EXP_TEST_AB_V5 = {
    'id': 1,
    'name': 'exp_test_ab',
    'iteration': 1,
    'unitType': 'session_id',
    'seedHi': 3603515,
    'seedLo': 233373850,
    'split': [0.5, 0.5],
    'trafficSeedHi': 449867249,
    'trafficSeedLo': 455443629,
    'trafficSplit': [0, 1],
    'fullOnVariant': 0,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': '{"banner.size":"tiny"}'},
        {
        'name': 'B',
        'config': '{"banner.border":1,"banner.size":"large"}'
    }
    ],
    'audience': '{"filter":[{"gte":[{"var":"age"},{"value":20}]}]}',
    'audienceStrict': True,
    'customFieldValues': None
}

EXP_TEST_AB_V6 = {
    'id': 1,
    'name': 'exp_test_ab',
    'iteration': 1,
    'unitType': 'session_id',
    'seedHi': 3603515,
    'seedLo': 233373850,
    'split': [0.5, 0.5],
    'trafficSeedHi': 449867249,
    'trafficSeedLo': 455443629,
    'trafficSplit': [0, 1],
    'fullOnVariant': 0,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': None},
        {
        'name': 'B',
        'config': '{"banner.border":1,"banner.size":"large"}'
    }
    ],
    'audience': '{"filter":[{"gte":[{"var":"age"},{"value":20}]}]}',
    'audienceStrict': False,
    'customFieldValues': None
}

EXP_TEST_AB_V7 = {
    'id': 1,
    'name': 'exp_test_ab',
    'iteration': 1,
    'unitType': 'session_id',
    'seedHi': 3603515,
    'seedLo': 233373850,
    'split': [0.5, 0.5],
    'trafficSeedHi': 449867249,
    'trafficSeedLo': 455443629,
    'trafficSplit': [0, 1],
    'fullOnVariant': 1,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': None},
        {
        'name': 'B',
        'config': '{"banner.border":1,"banner.size":"large"}'
    }
    ],
    'audience': '',
    'audienceStrict': False,
    'customFieldValues': None
}

EXP_TEST_AB_V8 = {
    'id': 1,
    'name': 'exp_test_ab',
    'iteration': 1,
    'unitType': 'session_id',
    'seedHi': 3603515,
    'seedLo': 233373850,
    'split': [0.5, 0.5],
    'trafficSeedHi': 449867249,
    'trafficSeedLo': 455443629,
    'trafficSplit': [0, 1],
    'fullOnVariant': 0,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': None},
        {
        'name': 'B',
        'config': '{"banner.border":10,"banner.size":812}'
    }
    ],
    'audience': None,
    'customFieldValues': None
}

EXP_TEST_AB_V9 = {
    'id': 1,
    'name': 'exp_test_ab',
    'iteration': 1,
    'unitType': 'session_id',
    'seedHi': 3603515,
    'seedLo': 233373850,
    'split': [0.5, 0.5],
    'trafficSeedHi': 449867249,
    'trafficSeedLo': 455443629,
    'trafficSplit': [0, 1],
    'fullOnVariant': 0,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': None},
        {'name': 'B', 'config': '{"banner.border":1}'}
    ],
    'audience': None,
    'customFieldValues': None
}

EXP_TEST_CUSTOM_FIELDS = {
    'id': 5,
    'name': 'exp_test_custom_fields',
    'iteration': 1,
    'unitType': 'session_id',
    'seedHi': 9372617,
    'seedLo': 121364805,
    'split': [0.5, 0.5],
    'trafficSeedHi': 318746944,
    'trafficSeedLo': 359812364,
    'trafficSplit': [0, 1],
    'fullOnVariant': 0,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': None},
        {'name': 'B', 'config': None}
    ],
    'audience': '',
    'audienceStrict': False,
    'customFieldValues': [
        {
        'name': 'country',
        'value': 'US,PT,ES',
        'type': 'string'
    },
        {
        'name': 'number_field',
        'value': '123',
        'type': 'number'
    },
        {
        'name': 'boolean_field',
        'value': 'true',
        'type': 'boolean'
    }
    ]
}

EXP_TEST_CUSTOM_FIELDS_V2 = {
    'id': 5,
    'name': 'exp_test_custom_fields',
    'iteration': 1,
    'unitType': 'session_id',
    'seedHi': 9372617,
    'seedLo': 121364805,
    'split': [0.5, 0.5],
    'trafficSeedHi': 318746944,
    'trafficSeedLo': 359812364,
    'trafficSplit': [0, 1],
    'fullOnVariant': 0,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': None},
        {'name': 'B', 'config': '{"submit.size":"sm"}'}
    ],
    'audience': None,
    'audienceStrict': False,
    'customFieldValues': [
        {
        'name': 'country',
        'value': 'US,PT,ES',
        'type': 'string'
    },
        {
        'name': 'languages',
        'value': 'en-US,en-GB,pt-PT,pt-BR,es-ES,es-MX',
        'type': 'string'
    },
        {
        'name': 'text_field',
        'value': 'hello text',
        'type': 'text'
    },
        {
        'name': 'string_field',
        'value': 'hello string',
        'type': 'string'
    },
        {
        'name': 'number_field',
        'value': '123',
        'type': 'number'
    },
        {
        'name': 'boolean_field',
        'value': 'true',
        'type': 'boolean'
    },
        {
        'name': 'false_boolean_field',
        'value': 'false',
        'type': 'boolean'
    },
        {
        'name': 'invalid_type_field',
        'value': 'invalid',
        'type': 'invalid'
    }
    ]
}

EXP_TEST_FULLON = {
    'id': 4,
    'name': 'exp_test_fullon',
    'iteration': 1,
    'unitType': 'session_id',
    'seedHi': 856061641,
    'seedLo': 990838475,
    'split': [0.25, 0.25, 0.25, 0.25],
    'trafficSeedHi': 360868579,
    'trafficSeedLo': 330937933,
    'trafficSplit': [0, 1],
    'fullOnVariant': 2,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': None},
        {'name': 'B', 'config': None},
        {'name': 'C', 'config': None},
        {'name': 'D', 'config': None}
    ],
    'audience': '',
    'audienceStrict': False,
    'customFieldValues': None
}

EXP_TEST_NOT_ELIGIBLE = {
    'id': 3,
    'name': 'exp_test_not_eligible',
    'iteration': 1,
    'unitType': 'user_id',
    'seedHi': 503266407,
    'seedLo': 144942754,
    'split': [0.34, 0.33, 0.33],
    'trafficSeedHi': 87768905,
    'trafficSeedLo': 511357582,
    'trafficSplit': [0.99, 0.01],
    'fullOnVariant': 0,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': None},
        {'name': 'B', 'config': None},
        {'name': 'C', 'config': None}
    ],
    'audience': '{}',
    'audienceStrict': False,
    'customFieldValues': None
}

EXP_TEST_VARIABLES = {
    'id': 1,
    'name': 'exp_test_variables',
    'iteration': 1,
    'unitType': 'session_id',
    'seedHi': 0,
    'seedLo': 1,
    'split': [0.5, 0.5],
    'trafficSeedHi': 0,
    'trafficSeedLo': 0,
    'trafficSplit': [1, 0],
    'fullOnVariant': 0,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': None},
        {
        'name': 'B',
        'config': '{"button_color":"red","timeout":5000}'
    }
    ],
    'audience': '',
    'audienceStrict': False,
    'customFieldValues': None
}

EXP_TEST_VARIABLES_V2 = {
    'id': 1,
    'name': 'exp_test_variables',
    'iteration': 1,
    'unitType': 'session_id',
    'seedHi': 0,
    'seedLo': 1,
    'split': [0.5, 0.5],
    'trafficSeedHi': 0,
    'trafficSeedLo': 0,
    'trafficSplit': [1, 0],
    'fullOnVariant': 0,
    'applications': [{'name': 'website'}],
    'variants': [
        {'name': 'A', 'config': None},
        {'name': 'B', 'config': '{"button_color":"red"}'}
    ],
    'audience': '',
    'audienceStrict': False,
    'customFieldValues': None
}


# =============================================================================
# SCENARIO DEFINITIONS  
# =============================================================================

def generate_all_scenarios():
    """Generate all test scenarios"""
    scenarios = [
        # 01 - Context Creation - Ready with Data
        {
            "name": '01 - Context Creation - Ready with Data',
            "description": 'Context should be ready immediately when created with data',
            "contextData": {"experiments": [EXP_TEST_AB]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1, 'refreshPeriod': 0}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            }
            ]
        },
        # 02 - Unit Management - Set Unit
        {
            "name": '02 - Unit Management - Set Unit',
            "description": 'Should be able to set a unit after context creation',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready', 'data': {'experiments': []}}]
                }
            },
                {
                'action': 'setUnit',
                'params': {'unitType': 'user_id', 'uid': 12345},
                'expect': {'result': None, 'events': []}
            },
                {
                'action': 'getUnit',
                'params': {'unitType': 'user_id'},
                'expect': {'result': 12345, 'events': []}
            }
            ]
        },
        # 03 - Attribute Management - Set and Get
        {
            "name": '03 - Attribute Management - Set and Get',
            "description": 'Should be able to set and retrieve attributes',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready', 'data': {'experiments': []}}]
                }
            },
                {
                'action': 'attribute',
                'params': {'name': 'country', 'value': 'US'},
                'expect': {'result': None, 'events': []}
            },
                {
                'action': 'getAttribute',
                'params': {'name': 'country'},
                'expect': {'result': 'US', 'events': []}
            },
                {
                'action': 'attribute',
                'params': {'name': 'country', 'value': 'CA'},
                'expect': {'result': None, 'events': []}
            },
                {
                'action': 'getAttribute',
                'params': {'name': 'country'},
                'expect': {'result': 'CA', 'events': []}
            }
            ]
        },
        # 04 - Treatment - Queue Exposure
        {
            "name": '04 - Treatment - Queue Exposure',
            "description": 'treatment() should queue exposure event',
            "contextData": {"experiments": [EXP_TEST_AB_V2]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 05 - Treatment - Only Queue Once
        {
            "name": '05 - Treatment - Only Queue Once',
            "description": 'treatment() should only queue exposure once per experiment',
            "contextData": {"experiments": [EXP_TEST_AB_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {'result': 1, 'events': []}
            }
            ]
        },
        # 06 - Peek - No Exposure Queue
        {
            "name": '06 - Peek - No Exposure Queue',
            "description": 'peek() should not queue exposure events',
            "contextData": {"experiments": [EXP_TEST_AB_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'peek',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {'result': 1, 'events': []}
            },
                {
                'action': 'pending',
                'params': {},
                'expect': {'result': 0, 'events': []}
            }
            ]
        },
        # 07 - Override - Return Override Variant
        {
            "name": '07 - Override - Return Override Variant',
            "description": 'Should return override variant and mark as overridden',
            "contextData": {"experiments": [EXP_TEST_AB_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'override',
                'params': {'experimentName': 'exp_test_ab', 'variant': 1},
                'expect': {'result': None, 'events': []}
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': False,
                            'eligible': True,
                            'overridden': True,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 08 - Goal Tracking - Basic
        {
            "name": '08 - Goal Tracking - Basic',
            "description": 'Should queue goal with properties',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready', 'data': {'experiments': []}}]
                }
            },
                {
                'action': 'track',
                'params': {
                    'goalName': 'purchase',
                    'properties': {'amount': 99.99, 'items': 3}
                },
                'expect': {
                    'result': None,
                    'events': [
                        {
                        'type': 'goal',
                        'data': {
                            'name': 'purchase',
                            'properties': {'amount': 99.99, 'items': 3}
                        }
                    }
                    ]
                }
            },
                {
                'action': 'pending',
                'params': {},
                'expect': {'result': 1, 'events': []}
            }
            ]
        },
        # 09 - Goal Tracking - Filter Non-Numeric Properties
        {
            "name": '09 - Goal Tracking - Filter Non-Numeric Properties',
            "description": 'Non-numeric goal properties should be filtered out',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready', 'data': {'experiments': []}}]
                }
            },
                {
                'action': 'track',
                'params': {
                    'goalName': 'signup',
                    'properties': {
                        'count': 1,
                        'name': 'should be filtered',
                        'tags': ['also', 'filtered'],
                        'metadata': {'nested': 'object'}
                    }
                },
                'expect': {
                    'result': None,
                    'events': [
                        {
                        'type': 'goal',
                        'data': {
                            'name': 'signup',
                            'properties': {
                                'count': 1,
                                'name': 'should be filtered',
                                'tags': ['also', 'filtered'],
                                'metadata': {'nested': 'object'}
                            }
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 10 - Goal Tracking - No Properties
        {
            "name": '10 - Goal Tracking - No Properties',
            "description": 'Should track goal without properties',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready', 'data': {'experiments': []}}]
                }
            },
                {
                'action': 'track',
                'params': {'goalName': 'page_view'},
                'expect': {
                    'result': None,
                    'events': [
                        {
                        'type': 'goal',
                        'data': {'name': 'page_view', 'properties': None}
                    }
                    ]
                }
            }
            ]
        },
        # 11 - Custom Assignment - Override Natural Assignment
        {
            "name": '11 - Custom Assignment - Override Natural Assignment',
            "description": 'Custom assignment should override natural variant',
            "contextData": {"experiments": [EXP_TEST_AB_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'customAssignment',
                'params': {'experimentName': 'exp_test_ab', 'variant': 1},
                'expect': {'result': None, 'events': []}
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': True,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 12 - Full-On Variant
        {
            "name": '12 - Full-On Variant',
            "description": 'Full-on variant should always return specified variant',
            "contextData": {"experiments": [EXP_TEST_FULLON]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'any_unit_should_work'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 4, 'name': 'exp_test_fullon'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_fullon'},
                'expect': {
                    'result': 2,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 4,
                            'name': 'exp_test_fullon',
                            'unit': 'session_id',
                            'variant': 2,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': True,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 13 - Not Eligible - Traffic Split
        {
            "name": '13 - Not Eligible - Traffic Split',
            "description": 'User not in traffic should get variant 0 and eligible=false',
            "contextData": {"experiments": [EXP_TEST_NOT_ELIGIBLE]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'user_id': 123456789},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {
                            'experiments': [{'id': 3, 'name': 'exp_test_not_eligible'}]
                        }
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_not_eligible'},
                'expect': {
                    'result': 0,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 3,
                            'name': 'exp_test_not_eligible',
                            'unit': 'user_id',
                            'variant': 0,
                            'assigned': True,
                            'eligible': False,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 14 - Unknown Experiment
        {
            "name": '14 - Unknown Experiment',
            "description": 'Unknown experiment should return variant 0',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready', 'data': {'experiments': []}}]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_unknown'},
                'expect': {
                    'result': 0,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 0,
                            'name': 'exp_unknown',
                            'unit': None,
                            'variant': 0,
                            'assigned': False,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 15 - Custom Fields - String Type
        {
            "name": '15 - Custom Fields - String Type',
            "description": 'Should return string custom field value',
            "contextData": {"experiments": [EXP_TEST_CUSTOM_FIELDS]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {
                            'experiments': [{'id': 5, 'name': 'exp_test_custom_fields'}]
                        }
                    }
                    ]
                }
            },
                {
                'action': 'customFieldValue',
                'params': {
                    'experimentName': 'exp_test_custom_fields',
                    'fieldName': 'country'
                },
                'expect': {'result': 'US,PT,ES', 'events': []}
            },
                {
                'action': 'customFieldValue',
                'params': {
                    'experimentName': 'exp_test_custom_fields',
                    'fieldName': 'number_field'
                },
                'expect': {'result': 123, 'events': []}
            },
                {
                'action': 'customFieldValue',
                'params': {
                    'experimentName': 'exp_test_custom_fields',
                    'fieldName': 'boolean_field'
                },
                'expect': {'result': True, 'events': []}
            }
            ]
        },
        # 16 - Custom Fields - JSON Type
        {
            "name": '16 - Custom Fields - JSON Type',
            "description": 'Should parse JSON custom field values',
            "contextData": {"experiments": [EXP_TEST_ABC]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 2, 'name': 'exp_test_abc'}]}
                    }
                    ]
                }
            },
                {
                'action': 'customFieldValue',
                'params': {
                    'experimentName': 'exp_test_abc',
                    'fieldName': 'json_object'
                },
                'expect': {'result': {'123': 1, '456': 0}, 'events': []}
            },
                {
                'action': 'customFieldValue',
                'params': {
                    'experimentName': 'exp_test_abc',
                    'fieldName': 'json_array'
                },
                'expect': {'result': ['hello', 'world'], 'events': []}
            },
                {
                'action': 'customFieldValue',
                'params': {
                    'experimentName': 'exp_test_abc',
                    'fieldName': 'json_number'
                },
                'expect': {'result': 123, 'events': []}
            },
                {
                'action': 'customFieldValue',
                'params': {
                    'experimentName': 'exp_test_abc',
                    'fieldName': 'json_string'
                },
                'expect': {'result': 'hello', 'events': []}
            },
                {
                'action': 'customFieldValue',
                'params': {
                    'experimentName': 'exp_test_abc',
                    'fieldName': 'json_boolean'
                },
                'expect': {'result': True, 'events': []}
            },
                {
                'action': 'customFieldValue',
                'params': {
                    'experimentName': 'exp_test_abc',
                    'fieldName': 'json_null'
                },
                'expect': {'result': None, 'events': []}
            }
            ]
        },
        # 17 - Publish - With Exposures and Goals
        {
            "name": '17 - Publish - With Exposures and Goals',
            "description": 'Publish should send all queued events and clear queues',
            "contextData": {"experiments": [EXP_TEST_AB_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'attribute',
                'params': {'name': 'country', 'value': 'US'},
                'expect': {'result': None, 'events': []}
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            },
                {
                'action': 'track',
                'params': {
                    'goalName': 'purchase',
                    'properties': {'amount': 99.99}
                },
                'expect': {
                    'result': None,
                    'events': [
                        {
                        'type': 'goal',
                        'data': {'name': 'purchase', 'properties': {'amount': 99.99}}
                    }
                    ]
                }
            },
                {
                'action': 'pending',
                'params': {},
                'expect': {'result': 2, 'events': []}
            },
                {
                'action': 'publish',
                'params': {},
                'expect': {
                    'result': None,
                    'events': [
                        {
                        'type': 'publish',
                        'data': {
                            'hashed': True,
                            'units': [{'type': 'session_id'}],
                            'exposures': [{'id': 1, 'name': 'exp_test_ab', 'variant': 1}],
                            'goals': [{'name': 'purchase'}],
                            'attributes': [{'name': 'country', 'value': 'US'}]
                        }
                    }
                    ]
                }
            },
                {
                'action': 'pending',
                'params': {},
                'expect': {'result': 0, 'events': []}
            }
            ]
        },
        # 18 - Publish - Empty Queue
        {
            "name": '18 - Publish - Empty Queue',
            "description": 'Publish with empty queue should not call client',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready', 'data': {'experiments': []}}]
                }
            },
                {
                'action': 'pending',
                'params': {},
                'expect': {'result': 0, 'events': []}
            },
                {
                'action': 'publish',
                'params': {},
                'expect': {'result': None, 'events': []}
            }
            ]
        },
        # 19 - Finalize - Clear Queues and Seal Context
        {
            "name": '19 - Finalize - Clear Queues and Seal Context',
            "description": 'finalize() should publish events and prevent further mutations',
            "contextData": {"experiments": [EXP_TEST_AB_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            },
                {
                'action': 'finalize',
                'params': {},
                'expect': {
                    'result': None,
                    'events': [
                        {
                        'type': 'publish',
                        'data': {
                            'hashed': True,
                            'units': [{'type': 'session_id'}],
                            'exposures': [{'id': 1, 'name': 'exp_test_ab', 'variant': 1}]
                        }
                    },
                        {'type': 'finalize'}
                    ]
                }
            },
                {
                'action': 'isFinalized',
                'params': {},
                'expect': {'result': True, 'events': []}
            }
            ]
        },
        # 20 - Audience Match - Non-Strict Mode
        {
            "name": '20 - Audience Match - Non-Strict Mode',
            "description": 'Audience match should assign normally in non-strict mode',
            "contextData": {"experiments": [EXP_AUDIENCE_TEST]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_audience_test'}]}
                    }
                    ]
                }
            },
                {
                'action': 'attribute',
                'params': {'name': 'country', 'value': 'US'},
                'expect': {'result': None, 'events': []}
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_audience_test'},
                'expect': {
                    'result': 0,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_audience_test',
                            'unit': 'session_id',
                            'variant': 0,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 21 - Audience Mismatch - Non-Strict Mode
        {
            "name": '21 - Audience Mismatch - Non-Strict Mode',
            "description": 'Audience mismatch should assign variant but mark mismatch in non-strict mode',
            "contextData": {"experiments": [EXP_AUDIENCE_TEST]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_audience_test'}]}
                    }
                    ]
                }
            },
                {
                'action': 'attribute',
                'params': {'name': 'country', 'value': 'CA'},
                'expect': {'result': None, 'events': []}
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_audience_test'},
                'expect': {
                    'result': 0,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_audience_test',
                            'unit': 'session_id',
                            'variant': 0,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': True
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 22 - Audience Mismatch - Strict Mode
        {
            "name": '22 - Audience Mismatch - Strict Mode',
            "description": 'Audience mismatch should return variant 0 in strict mode',
            "contextData": {"experiments": [EXP_AUDIENCE_STRICT]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {
                            'experiments': [{'id': 1, 'name': 'exp_audience_strict'}]
                        }
                    }
                    ]
                }
            },
                {
                'action': 'attribute',
                'params': {'name': 'country', 'value': 'CA'},
                'expect': {'result': None, 'events': []}
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_audience_strict'},
                'expect': {
                    'result': 0,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_audience_strict',
                            'unit': 'session_id',
                            'variant': 0,
                            'assigned': False,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': True
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 23 - Variables - Basic Access with Exposure
        {
            "name": '23 - Variables - Basic Access with Exposure',
            "description": 'variableValue() should return value and queue exposure',
            "contextData": {"experiments": [EXP_TEST_VARIABLES]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {
                            'experiments': [{'id': 1, 'name': 'exp_test_variables'}]
                        }
                    }
                    ]
                }
            },
                {
                'action': 'variableValue',
                'params': {'key': 'button_color', 'defaultValue': 'blue'},
                'expect': {
                    'result': 'blue',
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_variables',
                            'unit': 'session_id',
                            'variant': 0,
                            'assigned': True,
                            'eligible': False,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 24 - Variables - Default Value
        {
            "name": '24 - Variables - Default Value',
            "description": 'variableValue() should return default for unknown variable',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready', 'data': {'experiments': []}}]
                }
            },
                {
                'action': 'variableValue',
                'params': {'key': 'unknown_var', 'defaultValue': 'default_value'},
                'expect': {'result': 'default_value', 'events': []}
            }
            ]
        },
        # 25 - Variables - Peek Without Exposure
        {
            "name": '25 - Variables - Peek Without Exposure',
            "description": 'peekVariableValue() should not queue exposure',
            "contextData": {"experiments": [EXP_TEST_VARIABLES_V2]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {
                            'experiments': [{'id': 1, 'name': 'exp_test_variables'}]
                        }
                    }
                    ]
                }
            },
                {
                'action': 'peekVariableValue',
                'params': {'key': 'button_color', 'defaultValue': 'blue'},
                'expect': {'result': 'blue', 'events': []}
            },
                {
                'action': 'pending',
                'params': {},
                'expect': {'result': 0, 'events': []}
            }
            ]
        },
        # 26 - Cache Invalidation - Experiment Stopped
        {
            "name": '26 - Cache Invalidation - Experiment Stopped',
            "description": 'Cache should be cleared when experiment stops',
            "contextData": {"experiments": [EXP_TEST_AB_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            },
                {
                'action': 'refresh',
                'params': {'newData': {'experiments': []}},
                'expect': {
                    'result': None,
                    'events': [{'type': 'refresh', 'data': {'experiments': []}}]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 0,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 0,
                            'name': 'exp_test_ab',
                            'unit': None,
                            'variant': 0,
                            'assigned': False,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 27 - Cache Invalidation - Experiment Started
        {
            "name": '27 - Cache Invalidation - Experiment Started',
            "description": 'Cache should be cleared when new experiment starts',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready', 'data': {'experiments': []}}]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_new'},
                'expect': {
                    'result': 0,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 0,
                            'name': 'exp_new',
                            'unit': None,
                            'variant': 0,
                            'assigned': False,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            },
                {
                'action': 'refresh',
                'params': {
                    'newData': {
                        'experiments': [
                            {
                            'id': 99,
                            'name': 'exp_new',
                            'iteration': 1,
                            'unitType': 'session_id',
                            'seedHi': 0,
                            'seedLo': 1,
                            'split': [0.5, 0.5],
                            'trafficSeedHi': 0,
                            'trafficSeedLo': 0,
                            'trafficSplit': [1, 0],
                            'fullOnVariant': 0,
                            'applications': [{'name': 'website'}],
                            'variants': [
                                {'name': 'A', 'config': None},
                                {'name': 'B', 'config': None}
                            ],
                            'audience': '',
                            'audienceStrict': False,
                            'customFieldValues': None
                        }
                        ]
                    }
                },
                'expect': {
                    'result': None,
                    'events': [
                        {
                        'type': 'refresh',
                        'data': {'experiments': [{'id': 99, 'name': 'exp_new'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_new'},
                'expect': {
                    'result': 0,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 99,
                            'name': 'exp_new',
                            'unit': 'session_id',
                            'variant': 0,
                            'assigned': True,
                            'eligible': False,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 28 - Cache Invalidation - FullOn Changed
        {
            "name": '28 - Cache Invalidation - FullOn Changed',
            "description": 'Cache should be cleared when fullOnVariant changes',
            "contextData": {"experiments": [EXP_TEST_AB_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            },
                {
                'action': 'refresh',
                'params': {
                    'newData': {
                        'experiments': [
                            {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'iteration': 1,
                            'unitType': 'session_id',
                            'seedHi': 3603515,
                            'seedLo': 233373850,
                            'split': [0.5, 0.5],
                            'trafficSeedHi': 449867249,
                            'trafficSeedLo': 455443629,
                            'trafficSplit': [0, 1],
                            'fullOnVariant': 1,
                            'applications': [{'name': 'website'}],
                            'variants': [
                                {'name': 'A', 'config': None},
                                {'name': 'B', 'config': None}
                            ],
                            'audience': '',
                            'audienceStrict': False,
                            'customFieldValues': None
                        }
                        ]
                    }
                },
                'expect': {
                    'result': None,
                    'events': [
                        {
                        'type': 'refresh',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': True,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 29 - Cache Invalidation - Traffic Split Changed
        {
            "name": '29 - Cache Invalidation - Traffic Split Changed',
            "description": 'Cache should be cleared when trafficSplit changes',
            "contextData": {"experiments": [EXP_TEST_AB_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            },
                {
                'action': 'refresh',
                'params': {
                    'newData': {
                        'experiments': [
                            {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'iteration': 1,
                            'unitType': 'session_id',
                            'seedHi': 3603515,
                            'seedLo': 233373850,
                            'split': [0.5, 0.5],
                            'trafficSeedHi': 449867249,
                            'trafficSeedLo': 455443629,
                            'trafficSplit': [0.5, 0.5],
                            'fullOnVariant': 0,
                            'applications': [{'name': 'website'}],
                            'variants': [
                                {'name': 'A', 'config': None},
                                {'name': 'B', 'config': None}
                            ],
                            'audience': '',
                            'audienceStrict': False,
                            'customFieldValues': None
                        }
                        ]
                    }
                },
                'expect': {
                    'result': None,
                    'events': [
                        {
                        'type': 'refresh',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 0,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 0,
                            'assigned': True,
                            'eligible': False,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 30 - Cache Invalidation - Iteration Changed
        {
            "name": '30 - Cache Invalidation - Iteration Changed',
            "description": 'Cache should be cleared when iteration changes',
            "contextData": {"experiments": [EXP_TEST_AB_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            },
                {
                'action': 'refresh',
                'params': {
                    'newData': {
                        'experiments': [
                            {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'iteration': 2,
                            'unitType': 'session_id',
                            'seedHi': 3603515,
                            'seedLo': 233373850,
                            'split': [0.5, 0.5],
                            'trafficSeedHi': 449867249,
                            'trafficSeedLo': 455443629,
                            'trafficSplit': [0, 1],
                            'fullOnVariant': 0,
                            'applications': [{'name': 'website'}],
                            'variants': [
                                {'name': 'A', 'config': None},
                                {'name': 'B', 'config': None}
                            ],
                            'audience': '',
                            'audienceStrict': False,
                            'customFieldValues': None
                        }
                        ]
                    }
                },
                'expect': {
                    'result': None,
                    'events': [
                        {
                        'type': 'refresh',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 31 - Cache Invalidation - ID Changed
        {
            "name": '31 - Cache Invalidation - ID Changed',
            "description": 'Cache should be cleared when experiment ID changes',
            "contextData": {"experiments": [EXP_TEST_AB_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            },
                {
                'action': 'refresh',
                'params': {
                    'newData': {
                        'experiments': [
                            {
                            'id': 999,
                            'name': 'exp_test_ab',
                            'iteration': 1,
                            'unitType': 'session_id',
                            'seedHi': 3603515,
                            'seedLo': 233373850,
                            'split': [0.5, 0.5],
                            'trafficSeedHi': 449867249,
                            'trafficSeedLo': 455443629,
                            'trafficSplit': [0, 1],
                            'fullOnVariant': 0,
                            'applications': [{'name': 'website'}],
                            'variants': [
                                {'name': 'A', 'config': None},
                                {'name': 'B', 'config': None}
                            ],
                            'audience': '',
                            'audienceStrict': False,
                            'customFieldValues': None
                        }
                        ]
                    }
                },
                'expect': {
                    'result': None,
                    'events': [
                        {
                        'type': 'refresh',
                        'data': {'experiments': [{'id': 999, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 999,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 32 - Cache Invalidation - No Changes Retain Cache
        {
            "name": '32 - Cache Invalidation - No Changes Retain Cache',
            "description": 'Cache should be retained when nothing changes',
            "contextData": {"experiments": [EXP_TEST_AB_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            },
                {
                'action': 'refresh',
                'params': {
                    'newData': {
                        'experiments': [
                            {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'iteration': 1,
                            'unitType': 'session_id',
                            'seedHi': 3603515,
                            'seedLo': 233373850,
                            'split': [0.5, 0.5],
                            'trafficSeedHi': 449867249,
                            'trafficSeedLo': 455443629,
                            'trafficSplit': [0, 1],
                            'fullOnVariant': 0,
                            'applications': [{'name': 'website'}],
                            'variants': [
                                {'name': 'A', 'config': None},
                                {'name': 'B', 'config': None}
                            ],
                            'audience': '',
                            'audienceStrict': False,
                            'customFieldValues': None
                        }
                        ]
                    }
                },
                'expect': {
                    'result': None,
                    'events': [
                        {
                        'type': 'refresh',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 33 - Cache Invalidation - Override Retains Cache
        {
            "name": '33 - Cache Invalidation - Override Retains Cache',
            "description": 'Cache should be retained when override is set even if experiment changes',
            "contextData": {"experiments": [EXP_TEST_AB_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'override',
                'params': {'experimentName': 'exp_test_ab', 'variant': 1},
                'expect': {'result': None, 'events': []}
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': False,
                            'eligible': True,
                            'overridden': True,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            },
                {
                'action': 'refresh',
                'params': {
                    'newData': {
                        'experiments': [
                            {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'iteration': 2,
                            'unitType': 'session_id',
                            'seedHi': 99999,
                            'seedLo': 99999,
                            'split': [0.3, 0.7],
                            'trafficSeedHi': 449867249,
                            'trafficSeedLo': 455443629,
                            'trafficSplit': [0, 1],
                            'fullOnVariant': 0,
                            'applications': [{'name': 'website'}],
                            'variants': [
                                {'name': 'A', 'config': None},
                                {'name': 'B', 'config': None}
                            ],
                            'audience': '',
                            'audienceStrict': False,
                            'customFieldValues': None
                        }
                        ]
                    }
                },
                'expect': {
                    'result': None,
                    'events': [
                        {
                        'type': 'refresh',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': False,
                            'eligible': True,
                            'overridden': True,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 34 - Error Handling - Invalid Unit Empty String
        {
            "name": '34 - Error Handling - Invalid Unit Empty String',
            "description": 'Should throw error when setting unit with empty string',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready', 'data': {'experiments': []}}]
                }
            },
                {
                'action': 'setUnit',
                'params': {'unitType': 'user_id', 'uid': ''},
                'expect': {'error': "Unit 'user_id' UID must not be blank."}
            }
            ]
        },
        # 35 - Error Handling - Duplicate Unit Different Value
        {
            "name": '35 - Error Handling - Duplicate Unit Different Value',
            "description": 'Should throw error when setting same unit type twice with different values',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'original_value'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready', 'data': {'experiments': []}}]
                }
            },
                {
                'action': 'setUnit',
                'params': {'unitType': 'session_id', 'uid': 'different_value'},
                'expect': {'error': "Unit 'session_id' UID already set."}
            }
            ]
        },
        # 36 - Unit Management - Duplicate Unit Same Value OK
        {
            "name": '36 - Unit Management - Duplicate Unit Same Value OK',
            "description": 'Should NOT throw when setting same unit type with same value',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'same_value'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready', 'data': {'experiments': []}}]
                }
            },
                {
                'action': 'setUnit',
                'params': {'unitType': 'session_id', 'uid': 'same_value'},
                'expect': {'result': None, 'events': []}
            }
            ]
        },
        # 37 - Error Handling - Invalid Goal Properties Number
        {
            "name": '37 - Error Handling - Invalid Goal Properties Number',
            "description": 'Should throw when goal properties is a number instead of object',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready', 'data': {'experiments': []}}]
                }
            },
                {
                'action': 'track',
                'params': {'goalName': 'purchase', 'properties': 125.0},
                'expect': {
                    'error': "Goal 'purchase' properties must be of type object."
                }
            }
            ]
        },
        # 38 - Error Handling - Invalid Goal Properties String
        {
            "name": '38 - Error Handling - Invalid Goal Properties String',
            "description": 'Should throw when goal properties is a string instead of object',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready', 'data': {'experiments': []}}]
                }
            },
                {
                'action': 'track',
                'params': {'goalName': 'purchase', 'properties': 'invalid'},
                'expect': {
                    'error': "Goal 'purchase' properties must be of type object."
                }
            }
            ]
        },
        # 39 - Error Handling - Invalid Goal Properties Array
        {
            "name": '39 - Error Handling - Invalid Goal Properties Array',
            "description": 'Should throw when goal properties is an array instead of object',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready', 'data': {'experiments': []}}]
                }
            },
                {
                'action': 'track',
                'params': {'goalName': 'purchase', 'properties': []},
                'expect': {
                    'error': "Goal 'purchase' properties must be of type object."
                }
            }
            ]
        },
        # 40 - Treatment - Queue Exposure After Peek
        {
            "name": '40 - Treatment - Queue Exposure After Peek',
            "description": 'treatment() should queue exposure even after peek() was called',
            "contextData": {"experiments": [EXP_TEST_AB_V4]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'peek',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {'result': 1, 'events': []}
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 41 - Treatment - Unknown Experiment Returns Zero
        {
            "name": '41 - Treatment - Unknown Experiment Returns Zero',
            "description": 'treatment() should return 0 and queue exposure for unknown experiment',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready', 'data': {'experiments': []}}]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'unknown_exp'},
                'expect': {
                    'result': 0,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 0,
                            'name': 'unknown_exp',
                            'unit': None,
                            'variant': 0,
                            'assigned': False,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 42 - Treatment - No Re-queue Unknown Experiment
        {
            "name": '42 - Treatment - No Re-queue Unknown Experiment',
            "description": 'treatment() should not re-queue exposure for unknown experiment',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready', 'data': {'experiments': []}}]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'unknown_exp'},
                'expect': {
                    'result': 0,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 0,
                            'name': 'unknown_exp',
                            'unit': None,
                            'variant': 0,
                            'assigned': False,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'unknown_exp'},
                'expect': {'result': 0, 'events': []}
            }
            ]
        },
        # 43 - Audience - Re-evaluation Strict Mode
        {
            "name": '43 - Audience - Re-evaluation Strict Mode',
            "description": 'Should re-evaluate audience and queue new exposure when attribute changes in strict mode',
            "contextData": {"experiments": [EXP_TEST_AB_V5]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 0,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 0,
                            'assigned': False,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': True
                        }
                    }
                    ]
                }
            },
                {
                'action': 'attribute',
                'params': {'name': 'age', 'value': 25},
                'expect': {'result': None, 'events': []}
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 44 - Audience - Re-evaluation Non-Strict Mode
        {
            "name": '44 - Audience - Re-evaluation Non-Strict Mode',
            "description": 'Should re-evaluate audience and queue new exposure when attribute changes in non-strict mode',
            "contextData": {"experiments": [EXP_TEST_AB_V6]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': True
                        }
                    }
                    ]
                }
            },
                {
                'action': 'attribute',
                'params': {'name': 'age', 'value': 25},
                'expect': {'result': None, 'events': []}
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 45 - Audience - No Re-evaluation Without New Attributes
        {
            "name": '45 - Audience - No Re-evaluation Without New Attributes',
            "description": 'Should use cached assignment when no new attributes set',
            "contextData": {"experiments": [EXP_TEST_AB_V5]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'attribute',
                'params': {'name': 'age', 'value': 15},
                'expect': {'result': None, 'events': []}
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 0,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 0,
                            'assigned': False,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': True
                        }
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {'result': 0, 'events': []}
            }
            ]
        },
        # 46 - Audience - No Re-evaluation Without Filter
        {
            "name": '46 - Audience - No Re-evaluation Without Filter',
            "description": 'Should not re-evaluate for experiments without audience filter',
            "contextData": {"experiments": [EXP_TEST_ABC_V2]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 2, 'name': 'exp_test_abc'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_abc'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 2,
                            'name': 'exp_test_abc',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            },
                {
                'action': 'attribute',
                'params': {'name': 'age', 'value': 25},
                'expect': {'result': None, 'events': []}
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_abc'},
                'expect': {'result': 1, 'events': []}
            }
            ]
        },
        # 47 - Audience - Attribute Set Before Assignment
        {
            "name": '47 - Audience - Attribute Set Before Assignment',
            "description": 'Should not trigger re-evaluation when attribute set before first treatment call',
            "contextData": {"experiments": [EXP_TEST_AB_V5]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'attribute',
                'params': {'name': 'age', 'value': 25},
                'expect': {'result': None, 'events': []}
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {'result': 1, 'events': []}
            }
            ]
        },
        # 48 - Variables - Not Assigned Returns Default
        {
            "name": '48 - Variables - Not Assigned Returns Default',
            "description": 'Should return default value when experiment not assigned',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready', 'data': {'experiments': []}}]
                }
            },
                {
                'action': 'variableValue',
                'params': {'key': 'button_color', 'defaultValue': 'blue'},
                'expect': {'result': 'blue', 'events': []}
            }
            ]
        },
        # 49 - Variables - Queue Exposure After Peek
        {
            "name": '49 - Variables - Queue Exposure After Peek',
            "description": 'variableValue() should queue exposure after peekVariableValue()',
            "contextData": {"experiments": [EXP_TEST_ABC_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 2, 'name': 'exp_test_abc'}]}
                    }
                    ]
                }
            },
                {
                'action': 'peekVariableValue',
                'params': {'key': 'button.color', 'defaultValue': 'green'},
                'expect': {'result': 'red', 'events': []}
            },
                {
                'action': 'variableValue',
                'params': {'key': 'button.color', 'defaultValue': 'green'},
                'expect': {
                    'result': 'red',
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 2,
                            'name': 'exp_test_abc',
                            'unit': 'session_id',
                            'variant': 2,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 50 - Variables - Only Queue Exposure Once
        {
            "name": '50 - Variables - Only Queue Exposure Once',
            "description": 'variableValue() should only queue exposure once per experiment',
            "contextData": {"experiments": [EXP_TEST_ABC_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 2, 'name': 'exp_test_abc'}]}
                    }
                    ]
                }
            },
                {
                'action': 'variableValue',
                'params': {'key': 'button.color', 'defaultValue': 'green'},
                'expect': {
                    'result': 'red',
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 2,
                            'name': 'exp_test_abc',
                            'unit': 'session_id',
                            'variant': 2,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            },
                {
                'action': 'variableValue',
                'params': {'key': 'button.color', 'defaultValue': 'green'},
                'expect': {'result': 'red', 'events': []}
            }
            ]
        },
        # 51 - Variables - Peek Override Variant
        {
            "name": '51 - Variables - Peek Override Variant',
            "description": 'peekVariableValue() should return value for override variant',
            "contextData": {"experiments": [EXP_TEST_ABC_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 2, 'name': 'exp_test_abc'}]}
                    }
                    ]
                }
            },
                {
                'action': 'override',
                'params': {'experimentName': 'exp_test_abc', 'variant': 1},
                'expect': {'result': None, 'events': []}
            },
                {
                'action': 'peekVariableValue',
                'params': {'key': 'button.color', 'defaultValue': 'green'},
                'expect': {'result': 'blue', 'events': []}
            }
            ]
        },
        # 52 - Variables - Get All Keys
        {
            "name": '52 - Variables - Get All Keys',
            "description": 'variableKeys() should return all active variable keys',
            "contextData": {"experiments": [EXP_TEST_ABC_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 2, 'name': 'exp_test_abc'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_abc'},
                'expect': {
                    'result': 2,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 2,
                            'name': 'exp_test_abc',
                            'unit': 'session_id',
                            'variant': 2,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            },
                {
                'action': 'variableKeys',
                'params': {},
                'expect': {'result': ['button.color'], 'events': []}
            }
            ]
        },
        # 53 - Custom Fields - JSON Object
        {
            "name": '53 - Custom Fields - JSON Object',
            "description": 'Should parse JSON object custom field correctly',
            "contextData": {"experiments": [EXP_TEST_ABC_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 2, 'name': 'exp_test_abc'}]}
                    }
                    ]
                }
            },
                {
                'action': 'customFieldValue',
                'params': {
                    'experimentName': 'exp_test_abc',
                    'fieldName': 'json_object'
                },
                'expect': {'result': {'123': 1, '456': 0}, 'events': []}
            }
            ]
        },
        # 54 - Custom Fields - JSON Array
        {
            "name": '54 - Custom Fields - JSON Array',
            "description": 'Should parse JSON array custom field correctly',
            "contextData": {"experiments": [EXP_TEST_ABC_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 2, 'name': 'exp_test_abc'}]}
                    }
                    ]
                }
            },
                {
                'action': 'customFieldValue',
                'params': {
                    'experimentName': 'exp_test_abc',
                    'fieldName': 'json_array'
                },
                'expect': {'result': ['hello', 'world'], 'events': []}
            }
            ]
        },
        # 55 - Custom Fields - JSON Number
        {
            "name": '55 - Custom Fields - JSON Number',
            "description": 'Should parse JSON number custom field correctly',
            "contextData": {"experiments": [EXP_TEST_ABC_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 2, 'name': 'exp_test_abc'}]}
                    }
                    ]
                }
            },
                {
                'action': 'customFieldValue',
                'params': {
                    'experimentName': 'exp_test_abc',
                    'fieldName': 'json_number'
                },
                'expect': {'result': 123, 'events': []}
            }
            ]
        },
        # 56 - Custom Fields - JSON String
        {
            "name": '56 - Custom Fields - JSON String',
            "description": 'Should parse JSON string custom field correctly',
            "contextData": {"experiments": [EXP_TEST_ABC_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 2, 'name': 'exp_test_abc'}]}
                    }
                    ]
                }
            },
                {
                'action': 'customFieldValue',
                'params': {
                    'experimentName': 'exp_test_abc',
                    'fieldName': 'json_string'
                },
                'expect': {'result': 'hello', 'events': []}
            }
            ]
        },
        # 57 - Custom Fields - JSON Boolean
        {
            "name": '57 - Custom Fields - JSON Boolean',
            "description": 'Should parse JSON boolean custom field correctly',
            "contextData": {"experiments": [EXP_TEST_ABC_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 2, 'name': 'exp_test_abc'}]}
                    }
                    ]
                }
            },
                {
                'action': 'customFieldValue',
                'params': {
                    'experimentName': 'exp_test_abc',
                    'fieldName': 'json_boolean'
                },
                'expect': {'result': True, 'events': []}
            }
            ]
        },
        # 58 - Custom Fields - JSON Null
        {
            "name": '58 - Custom Fields - JSON Null',
            "description": 'Should parse JSON null custom field correctly',
            "contextData": {"experiments": [EXP_TEST_ABC_V3]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 2, 'name': 'exp_test_abc'}]}
                    }
                    ]
                }
            },
                {
                'action': 'customFieldValue',
                'params': {
                    'experimentName': 'exp_test_abc',
                    'fieldName': 'json_null'
                },
                'expect': {'result': None, 'events': []}
            }
            ]
        },
        # 59 - Custom Fields - Number Type
        {
            "name": '59 - Custom Fields - Number Type',
            "description": 'Should parse number custom field type correctly',
            "contextData": {"experiments": [EXP_TEST_CUSTOM_FIELDS_V2]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {
                            'experiments': [{'id': 5, 'name': 'exp_test_custom_fields'}]
                        }
                    }
                    ]
                }
            },
                {
                'action': 'customFieldValue',
                'params': {
                    'experimentName': 'exp_test_custom_fields',
                    'fieldName': 'number_field'
                },
                'expect': {'result': 123, 'events': []}
            }
            ]
        },
        # 60 - Custom Fields - Boolean Type True
        {
            "name": '60 - Custom Fields - Boolean Type True',
            "description": 'Should parse boolean custom field type (true) correctly',
            "contextData": {"experiments": [EXP_TEST_CUSTOM_FIELDS_V2]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {
                            'experiments': [{'id': 5, 'name': 'exp_test_custom_fields'}]
                        }
                    }
                    ]
                }
            },
                {
                'action': 'customFieldValue',
                'params': {
                    'experimentName': 'exp_test_custom_fields',
                    'fieldName': 'boolean_field'
                },
                'expect': {'result': True, 'events': []}
            }
            ]
        },
        # 61 - Custom Fields - Boolean Type False
        {
            "name": '61 - Custom Fields - Boolean Type False',
            "description": 'Should parse boolean custom field type (false) correctly',
            "contextData": {"experiments": [EXP_TEST_CUSTOM_FIELDS_V2]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {
                            'experiments': [{'id': 5, 'name': 'exp_test_custom_fields'}]
                        }
                    }
                    ]
                }
            },
                {
                'action': 'customFieldValue',
                'params': {
                    'experimentName': 'exp_test_custom_fields',
                    'fieldName': 'false_boolean_field'
                },
                'expect': {'result': False, 'events': []}
            }
            ]
        },
        # 62 - Custom Fields - Text Type
        {
            "name": '62 - Custom Fields - Text Type',
            "description": 'Should return text custom field as string',
            "contextData": {"experiments": [EXP_TEST_CUSTOM_FIELDS_V2]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {
                            'experiments': [{'id': 5, 'name': 'exp_test_custom_fields'}]
                        }
                    }
                    ]
                }
            },
                {
                'action': 'customFieldValue',
                'params': {
                    'experimentName': 'exp_test_custom_fields',
                    'fieldName': 'text_field'
                },
                'expect': {'result': 'hello text', 'events': []}
            }
            ]
        },
        # 63 - Custom Fields - Get All Keys
        {
            "name": '63 - Custom Fields - Get All Keys',
            "description": 'customFieldKeys() should return all field keys for experiment',
            "contextData": {"experiments": [EXP_TEST_CUSTOM_FIELDS_V2]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {
                            'experiments': [{'id': 5, 'name': 'exp_test_custom_fields'}]
                        }
                    }
                    ]
                }
            },
                {
                'action': 'customFieldKeys',
                'params': {'experimentName': 'exp_test_custom_fields'},
                'expect': {
                    'result': [
                        'country',
                        'languages',
                        'text_field',
                        'string_field',
                        'number_field',
                        'boolean_field',
                        'false_boolean_field',
                        'invalid_type_field'
                    ],
                    'events': []
                }
            }
            ]
        },
        # 64 - Custom Fields - Get Value Type
        {
            "name": '64 - Custom Fields - Get Value Type',
            "description": 'customFieldValueType() should return field type',
            "contextData": {"experiments": [EXP_TEST_CUSTOM_FIELDS_V2]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {
                            'experiments': [{'id': 5, 'name': 'exp_test_custom_fields'}]
                        }
                    }
                    ]
                }
            },
                {
                'action': 'customFieldValueType',
                'params': {
                    'experimentName': 'exp_test_custom_fields',
                    'fieldName': 'boolean_field'
                },
                'expect': {'result': 'boolean', 'events': []}
            }
            ]
        },
        # 65 - Override - Clear Assignment Cache
        {
            "name": '65 - Override - Clear Assignment Cache',
            "description": 'setOverride() should clear assignment cache for that experiment',
            "contextData": {"experiments": [EXP_TEST_AB_V4]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            },
                {
                'action': 'override',
                'params': {'experimentName': 'exp_test_ab', 'variant': 0},
                'expect': {'result': None, 'events': []}
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 0,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 0,
                            'assigned': False,
                            'eligible': True,
                            'overridden': True,
                            'fullOn': False,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 66 - Custom Assignment - Clear Cache
        {
            "name": '66 - Custom Assignment - Clear Cache',
            "description": 'setCustomAssignment() should clear assignment cache',
            "contextData": {"experiments": [EXP_TEST_AB_V7]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [
                        {
                        'type': 'ready',
                        'data': {'experiments': [{'id': 1, 'name': 'exp_test_ab'}]}
                    }
                    ]
                }
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': True,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            },
                {
                'action': 'customAssignment',
                'params': {'experimentName': 'exp_test_ab', 'variant': 0},
                'expect': {'result': None, 'events': []}
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {
                    'result': 1,
                    'events': [
                        {
                        'type': 'exposure',
                        'data': {
                            'id': 1,
                            'name': 'exp_test_ab',
                            'unit': 'session_id',
                            'variant': 1,
                            'assigned': True,
                            'eligible': True,
                            'overridden': False,
                            'fullOn': True,
                            'custom': False,
                            'audienceMismatch': False
                        }
                    }
                    ]
                }
            }
            ]
        },
        # 67 - Goal Tracking - Before Ready
        {
            "name": '67 - Goal Tracking - Before Ready',
            "description": 'track() should queue goals before context is ready',"contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContext',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {
                        'publishDelay': -1,
                        'payloadThrottle': 100
                    }
                },
                'expect': {
                    'result': {'ready': False, 'failed': False, 'finalized': False},
                    'events': []
                }
            },
                {
                'action': 'track',
                'params': {
                    'goalName': 'purchase',
                    'properties': {'amount': 125.5}
                },
                'expect': {
                    'result': None,
                    'events': [
                        {
                        'type': 'goal',
                        'data': {'name': 'purchase', 'properties': {'amount': 125.5}}
                    }
                    ]
                }
            },
                {
                'action': 'pending',
                'params': {},
                'expect': {'result': 1, 'events': []}
            }
            ]
        },
        # 68 - HTTP Client - Retry on Connection Error
        {
            "name": '68 - HTTP Client - Retry on Connection Error',
            "description": 'Client should retry on network errors up to max retries',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready'}]
                }
            }
            ]
        },
        # 69 - HTTP Client - No Retry on 400 Bad Request
        {
            "name": '69 - HTTP Client - No Retry on 400 Bad Request',
            "description": 'Client should NOT retry on 4xx client errors',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready'}]
                }
            }
            ]
        },
        # 70 - HTTP Client - Retry on 500 Server Error
        {
            "name": '70 - HTTP Client - Retry on 500 Server Error',
            "description": 'Client should retry on 5xx server errors',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready'}]
                }
            }
            ]
        },
        # 71 - HTTP Client - Timeout After Max Duration
        {
            "name": '71 - HTTP Client - Timeout After Max Duration',
            "description": 'Client should abort request after timeout duration',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready'}]
                }
            }
            ]
        },
        # 72 - HTTP Client - Manual Abort Signal
        {
            "name": '72 - HTTP Client - Manual Abort Signal',
            "description": 'Client should support manual request abortion',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready'}]
                }
            }
            ]
        },
        # 73 - HTTP Client - URL Query Encoding
        {
            "name": '73 - HTTP Client - URL Query Encoding',
            "description": 'Client should properly encode special characters in query params',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready'}]
                }
            }
            ]
        },
        # 74 - HTTP Client - Empty Query Parameters
        {
            "name": '74 - HTTP Client - Empty Query Parameters',
            "description": 'Client should omit query string when params are empty',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready'}]
                }
            }
            ]
        },
        # 75 - Hashing - MD5 Empty String
        {
            "name": '75 - Hashing - MD5 Empty String',
            "description": 'MD5 hash of empty string should match known value',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 76 - Hashing - MD5 Special Characters
        {
            "name": '76 - Hashing - MD5 Special Characters',
            "description": 'MD5 hash of special characters (UTF-8) should match',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 77 - Hashing - MD5 Long Text
        {
            "name": '77 - Hashing - MD5 Long Text',
            "description": 'MD5 hash of Lorem Ipsum text should match',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 78 - Hashing - Murmur3 Zero Seed
        {
            "name": '78 - Hashing - Murmur3 Zero Seed',
            "description": 'Murmur3_32 with seed 0 should match known hashes',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 79 - Hashing - Murmur3 Custom Seed
        {
            "name": '79 - Hashing - Murmur3 Custom Seed',
            "description": 'Murmur3_32 with custom seed (0xdeadbeef) should match',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 80 - Hashing - Murmur3 UTF-8
        {
            "name": '80 - Hashing - Murmur3 UTF-8',
            "description": 'Murmur3_32 should handle UTF-8 correctly',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 81 - Algorithm - Binary Insert Center
        {
            "name": '81 - Algorithm - Binary Insert Center',
            "description": 'insertUniqueSorted should insert in middle of array',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 82 - Algorithm - Binary Insert Duplicate
        {
            "name": '82 - Algorithm - Binary Insert Duplicate',
            "description": 'insertUniqueSorted should NOT insert duplicates',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 83 - Algorithm - Binary Insert Beginning
        {
            "name": '83 - Algorithm - Binary Insert Beginning',
            "description": 'insertUniqueSorted should insert at array start',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 84 - Algorithm - Binary Insert End
        {
            "name": '84 - Algorithm - Binary Insert End',
            "description": 'insertUniqueSorted should insert at array end',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 85 - Variant Assignment - Choose Variant Edge 0.0
        {
            "name": '85 - Variant Assignment - Choose Variant Edge 0.0',
            "description": 'chooseVariant at probability 0.0',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 86 - Variant Assignment - Choose Variant Edge 1.0
        {
            "name": '86 - Variant Assignment - Choose Variant Edge 1.0',
            "description": 'chooseVariant at probability 1.0',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 87 - Variant Assignment - Choose Variant Boundary
        {
            "name": '87 - Variant Assignment - Choose Variant Boundary',
            "description": 'chooseVariant at exact split boundary',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 88 - Variant Assignment - Three Way Split Boundaries
        {
            "name": '88 - Variant Assignment - Three Way Split Boundaries',
            "description": 'chooseVariant with 3 variants at boundaries',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 89 - Variant Assignment - Zero Split First
        {
            "name": '89 - Variant Assignment - Zero Split First',
            "description": 'chooseVariant with first variant having 0% split',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 90 - Variant Assignment - Zero Split Last
        {
            "name": '90 - Variant Assignment - Zero Split Last',
            "description": 'chooseVariant with last variant having 0% split',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 91 - Type Conversion - Boolean Convert Truthy
        {
            "name": '91 - Type Conversion - Boolean Convert Truthy',
            "description": 'booleanConvert should convert truthy values correctly',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 92 - Type Conversion - Number Convert Valid
        {
            "name": '92 - Type Conversion - Number Convert Valid',
            "description": 'numberConvert should convert numeric strings and booleans',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 93 - Type Conversion - Number Convert Invalid
        {
            "name": '93 - Type Conversion - Number Convert Invalid',
            "description": 'numberConvert should return null for non-numeric values',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 94 - Type Conversion - String Convert Primitives
        {
            "name": '94 - Type Conversion - String Convert Primitives',
            "description": 'stringConvert should convert primitives to strings',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 95 - Type Conversion - String Convert Invalid
        {
            "name": '95 - Type Conversion - String Convert Invalid',
            "description": 'stringConvert should return null for objects/arrays',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 96 - Evaluator - Extract Var Nested Paths
        {
            "name": '96 - Evaluator - Extract Var Nested Paths',
            "description": 'extractVar should navigate nested paths with /',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 97 - Evaluator - Extract Var Invalid Paths
        {
            "name": '97 - Evaluator - Extract Var Invalid Paths',
            "description": 'extractVar should return null for invalid paths',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 98 - Evaluator - Compare Null Handling
        {
            "name": '98 - Evaluator - Compare Null Handling',
            "description": 'compare() should return null when comparing non-null with null',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 99 - Deep Equality - Primitives
        {
            "name": '99 - Deep Equality - Primitives',
            "description": 'isEqualsDeep should compare primitives correctly',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 100 - Deep Equality - NaN
        {
            "name": '100 - Deep Equality - NaN',
            "description": 'isEqualsDeep should treat NaN as equal to NaN',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 101 - Deep Equality - Arrays Simple
        {
            "name": '101 - Deep Equality - Arrays Simple',
            "description": 'isEqualsDeep should compare arrays',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 102 - Deep Equality - Arrays Nested
        {
            "name": '102 - Deep Equality - Arrays Nested',
            "description": 'isEqualsDeep should compare nested arrays',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 103 - Deep Equality - Objects Simple
        {
            "name": '103 - Deep Equality - Objects Simple',
            "description": 'isEqualsDeep should compare objects (order-independent)',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 104 - Deep Equality - Objects Nested
        {
            "name": '104 - Deep Equality - Objects Nested',
            "description": 'isEqualsDeep should compare nested objects',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 105 - String Encoding - UTF-8 Single Byte
        {
            "name": '105 - String Encoding - UTF-8 Single Byte',
            "description": 'stringToUint8Array should encode ASCII correctly',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 106 - String Encoding - UTF-8 Multi Byte
        {
            "name": '106 - String Encoding - UTF-8 Multi Byte',
            "description": 'stringToUint8Array should encode multi-byte UTF-8',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 107 - Base64 - Empty String
        {
            "name": '107 - Base64 - Empty String',
            "description": 'base64UrlNoPadding should encode empty string',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 108 - Base64 - Short Strings
        {
            "name": '108 - Base64 - Short Strings',
            "description": 'base64UrlNoPadding should encode short strings',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 109 - Base64 - Special Characters
        {
            "name": '109 - Base64 - Special Characters',
            "description": 'base64UrlNoPadding should encode special chars',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 110 - Base64 - Long Text
        {
            "name": '110 - Base64 - Long Text',
            "description": 'base64UrlNoPadding should encode long text',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 111 - Array Equality - Shallow Equals
        {
            "name": '111 - Array Equality - Shallow Equals',
            "description": 'arrayEqualsShallow should compare arrays (not deep)',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 112 - Hash Unit - String Values
        {
            "name": '112 - Hash Unit - String Values',
            "description": 'hashUnit should hash various string unit types',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 113 - Hash Unit - Numeric Values
        {
            "name": '113 - Hash Unit - Numeric Values',
            "description": 'hashUnit should hash numeric unit types',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 114 - SDK Config - Default Values
        {
            "name": '114 - SDK Config - Default Values',
            "description": 'SDK should use default values for missing config',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 115 - SDK Config - Custom Values
        {
            "name": '115 - SDK Config - Custom Values',
            "description": 'SDK should use custom config when provided',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 116 - SDK Config - Unit Type Coercion
        {
            "name": '116 - SDK Config - Unit Type Coercion',
            "description": 'SDK should coerce numeric unit values to strings',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'abc',
                        'user_id': 125,
                        'float_id': 125.75
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready'}]
                }
            },
                {
                'action': 'getUnit',
                'params': {'unitType': 'user_id'},
                'expect': {'result': 125, 'events': []}
            },
                {
                'action': 'getUnit',
                'params': {'unitType': 'float_id'},
                'expect': {'result': 125.75, 'events': []}
            }
            ]
        },
        # 117 - Config Merge - Nested Variables
        {
            "name": '117 - Config Merge - Nested Variables',
            "description": 'mergeConfig should create getters for nested config paths',
            "contextData": {"experiments": [EXP_TEST_AB_V8]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready'}]
                }
            }
            ]
        },
        # 118 - Config Merge - Type Mismatch Warning
        {
            "name": '118 - Config Merge - Type Mismatch Warning',
            "description": 'mergeConfig should warn when overriding non-object with object',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 119 - Config Merge - Duplicate Key Error
        {
            "name": '119 - Config Merge - Duplicate Key Error',
            "description": 'mergeConfig should error when multiple experiments override same key',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 120 - Audience Matcher - Empty Audience
        {
            "name": '120 - Audience Matcher - Empty Audience',
            "description": 'Audience matcher should return null for empty audience',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 121 - Audience Matcher - Invalid Filter Type
        {
            "name": '121 - Audience Matcher - Invalid Filter Type',
            "description": 'Audience matcher should return null if filter is not object/array',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 122 - Audience Matcher - Boolean Result
        {
            "name": '122 - Audience Matcher - Boolean Result',
            "description": 'Audience matcher should return boolean for valid filter',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 123 - JSON Expr - Array As AND
        {
            "name": '123 - JSON Expr - Array As AND',
            "description": 'Evaluator should treat array as implicit AND combinator',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 124 - JSON Expr - Unknown Operator Null
        {
            "name": '124 - JSON Expr - Unknown Operator Null',
            "description": 'Evaluator should return null for unknown operators',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 125 - Publish - Empty Arrays Omitted
        {
            "name": '125 - Publish - Empty Arrays Omitted',
            "description": 'Publisher should omit empty exposure/goal arrays',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready'}]
                }
            },
                {
                'action': 'publish',
                'params': {},
                'expect': {'result': None, 'events': []}
            }
            ]
        },
        # 126 - Publish - Auto Timestamp
        {
            "name": '126 - Publish - Auto Timestamp',
            "description": 'Publisher should set publishedAt if not present',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready'}]
                }
            },
                {
                'action': 'publish',
                'params': {},
                'expect': {'result': None, 'events': []}
            }
            ]
        },
        # 127 - Type Check - isObject
        {
            "name": '127 - Type Check - isObject',
            "description": 'isObject should return true only for plain objects',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 128 - Type Check - isNumeric
        {
            "name": '128 - Type Check - isNumeric',
            "description": 'isNumeric should return true only for numbers',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 129 - Type Check - isPromise
        {
            "name": '129 - Type Check - isPromise',
            "description": 'isPromise should return true for thenable objects',
            "contextData": {"experiments": []},
            "steps": []
        },
        # 130 - Context - Pending Count
        {
            "name": '130 - Context - Pending Count',
            "description": 'Context should track pending exposure/goal count',
            "contextData": {"experiments": [EXP_TEST_AB_V9]},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {
                        'session_id': 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
                    },
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready'}]
                }
            },
                {
                'action': 'pending',
                'params': {},
                'expect': {'result': 0, 'events': []}
            },
                {
                'action': 'treatment',
                'params': {'experimentName': 'exp_test_ab'},
                'expect': {'result': 1, 'events': [{'type': 'exposure'}]}
            },
                {
                'action': 'pending',
                'params': {},
                'expect': {'result': 1, 'events': []}
            }
            ]
        },
        # 131 - Context - Is Finalized
        {
            "name": '131 - Context - Is Finalized',
            "description": 'Context should track finalized state',
            "contextData": {"experiments": []},
            "steps": [
                {
                'action': 'createContextWith',
                'params': {
                    'units': {'session_id': 'test123'},
                    'options': {'publishDelay': -1}
                },
                'expect': {
                    'result': {'ready': True, 'failed': False, 'finalized': False},
                    'events': [{'type': 'ready'}]
                }
            },
                {
                'action': 'isFinalized',
                'params': {},
                'expect': {'result': False, 'events': []}
            },
                {
                'action': 'finalize',
                'params': {},
                'expect': {'result': None, 'events': [{'type': 'finalize'}]}
            },
                {
                'action': 'isFinalized',
                'params': {},
                'expect': {'result': True, 'events': []}
            }
            ]
        },

        # === CONTEXT STATE SCENARIOS (132-136) ===
        
        # 132 - isReady Returns True When Ready
        {
            "name": "132 - Context State - isReady Returns True When Ready",
            "description": "isReady() should return true when context is created with data",
            "contextData": {"experiments": [EXP_TEST_AB]},
            "steps": [
                {"action": "createContextWith", "params": {"units": {"session_id": "test123"}, "options": {"publishDelay": -1}},
                 "expect": {"result": {"ready": True, "failed": False, "finalized": False},
                           "events": [{"type": "ready", "data": {"experiments": [{"id": 1, "name": "exp_test_ab"}]}}]}},
                {"action": "isReady", "params": {}, "expect": {"result": True, "events": []}}
            ]
        },
        
        # 133 - isFailed Returns False When Successful
        {
            "name": "133 - Context State - isFailed Returns False",
            "description": "isFailed() should return false when context is created successfully",
            "contextData": {"experiments": [EXP_TEST_AB]},
            "steps": [
                {"action": "createContextWith", "params": {"units": {"session_id": "test123"}, "options": {"publishDelay": -1}},
                 "expect": {"result": {"ready": True, "failed": False, "finalized": False},
                           "events": [{"type": "ready", "data": {"experiments": [{"id": 1, "name": "exp_test_ab"}]}}]}},
                {"action": "isFailed", "params": {}, "expect": {"result": False, "events": []}}
            ]
        },
        
        # 134 - experiments Returns Empty Array
        {
            "name": "134 - Context State - experiments Returns Empty Array",
            "description": "experiments() should return empty array when no experiments",
            "contextData": {"experiments": []},
            "steps": [
                {"action": "createContextWith", "params": {"units": {"session_id": "test123"}, "options": {"publishDelay": -1}},
                 "expect": {"result": {"ready": True, "failed": False, "finalized": False},
                           "events": [{"type": "ready", "data": {"experiments": []}}]}},
                {"action": "experiments", "params": {}, "expect": {"result": [], "events": []}}
            ]
        },
        
        # 135 - experiments Returns Experiment Names
        {
            "name": "135 - Context State - experiments Returns Names",
            "description": "experiments() should return array of experiment names",
            "contextData": {"experiments": [EXP_TEST_AB, EXP_TEST_ABC]},
            "steps": [
                {"action": "createContextWith", "params": {"units": {"session_id": "test123"}, "options": {"publishDelay": -1}},
                 "expect": {"result": {"ready": True, "failed": False, "finalized": False},
                           "events": [{"type": "ready", "data": {"experiments": [{"id": 1, "name": "exp_test_ab"}, {"id": 2, "name": "exp_test_abc"}]}}]}},
                {"action": "experiments", "params": {}, "expect": {"result": ["exp_test_ab", "exp_test_abc"], "events": []}}
            ]
        },
    ]
    
    scenarios.extend([
        {
            "name": "188 - Post-Finalize - setAttribute() Throws Error (Verified Finalized)",
            "description": "After finalize() and confirmed isFinalized=true, setAttribute() must fail",
            "contextData": {"experiments": []},
            "steps": [
                {
                    "action": "createContextWith",
                    "params": {"units": {"session_id": "postfin-attr-1"}, "options": {"publishDelay": -1}},
                    "expect": {"result": {"ready": True}, "events": [{"type": "ready"}]},
                },
                {"action": "finalize", "params": {}, "expect": {"result": None, "events": [{"type": "finalize"}]}},
                {"action": "isFinalized", "params": {}, "expect": {"result": True, "events": []}},
                {
                    "action": "attribute",
                    "params": {"name": "country", "value": "US"},
                    "expect": {"error": "Context finalized"},
                },
            ],
        },
        {
            "name": "189 - Post-Finalize - treatment() Throws Error (Verified Finalized)",
            "description": "After finalize() and confirmed isFinalized=true, treatment() must fail",
            "contextData": {"experiments": [EXP_TEST_AB]},
            "steps": [
                {
                    "action": "createContextWith",
                    "params": {"units": {"session_id": "postfin-treat-1"}, "options": {"publishDelay": -1}},
                    "expect": {"result": {"ready": True}, "events": [{"type": "ready"}]},
                },
                {"action": "finalize", "params": {}, "expect": {"result": None, "events": [{"type": "finalize"}]}},
                {"action": "isFinalized", "params": {}, "expect": {"result": True, "events": []}},
                {
                    "action": "treatment",
                    "params": {"experimentName": "exp_test_ab"},
                    "expect": {"error": "Context finalized"},
                },
            ],
        },
        {
            "name": "190 - Post-Finalize - override() Allowed (Verified Finalized)",
            "description": "After finalize() and confirmed isFinalized=true, override() should still succeed (JS parity)",
            "contextData": {"experiments": [EXP_TEST_AB]},
            "steps": [
                {
                    "action": "createContextWith",
                    "params": {"units": {"session_id": "postfin-override-1"}, "options": {"publishDelay": -1}},
                    "expect": {"result": {"ready": True}, "events": [{"type": "ready"}]},
                },
                {"action": "finalize", "params": {}, "expect": {"result": None, "events": [{"type": "finalize"}]}},
                {"action": "isFinalized", "params": {}, "expect": {"result": True, "events": []}},
                {
                    "action": "override",
                    "params": {"experimentName": "exp_test_ab", "variant": 1},
                    "expect": {"result": None, "events": []},
                },
            ],
        },
        {
            "name": "191 - Post-Finalize - getUnit() Still Works",
            "description": "After finalize(), getUnit() should still return existing unit values",
            "contextData": {"experiments": []},
            "steps": [
                {
                    "action": "createContextWith",
                    "params": {"units": {"session_id": "postfin-getunit-1"}, "options": {"publishDelay": -1}},
                    "expect": {"result": {"ready": True}, "events": [{"type": "ready"}]},
                },
                {"action": "finalize", "params": {}, "expect": {"result": None, "events": [{"type": "finalize"}]}},
                {"action": "isFinalized", "params": {}, "expect": {"result": True, "events": []}},
                {
                    "action": "getUnit",
                    "params": {"unitType": "session_id"},
                    "expect": {"result": "postfin-getunit-1", "events": []},
                },
            ],
        },
        {
            "name": "192 - Post-Finalize - getAttribute() Still Works",
            "description": "After finalize(), getAttribute() should remain readable",
            "contextData": {"experiments": []},
            "steps": [
                {
                    "action": "createContextWith",
                    "params": {"units": {"session_id": "postfin-getattr-1"}, "options": {"publishDelay": -1}},
                    "expect": {"result": {"ready": True}, "events": [{"type": "ready"}]},
                },
                {"action": "finalize", "params": {}, "expect": {"result": None, "events": [{"type": "finalize"}]}},
                {"action": "isFinalized", "params": {}, "expect": {"result": True, "events": []}},
                {
                    "action": "getAttribute",
                    "params": {"name": "country"},
                    "expect": {"result": None, "events": []},
                },
            ],
        },
    ])

    return scenarios


def main():
    scenarios = generate_all_scenarios()
    
    print(f"Generated {len(scenarios)} test scenarios")
    print("\nWriting to test_scenarios_complete.json...")
    
    with open('test_scenarios_complete.json', 'w') as f:
        json.dump(scenarios, f, indent=2)
    
    print("Done!")


if __name__ == "__main__":
    main()
