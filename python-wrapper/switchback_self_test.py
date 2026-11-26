"""Behavioural self-test for the `switchback` capability (R33,
cluster-defined switchbacks).

Mirrors java-wrapper's SwitchbackSelfTest: a sample of the platform golden
vectors through the SDK's own switchback assigner and permutation, then
switchback serving through a real context on an R33 payload deserialized
from JSON exactly as scenario payloads are (so the order of the
switchbackClusters keys is exercised), on the same clock-free grid the
scenarios use. Every check compares the treatment and the full exposure
record. A python3-sdk without switchbacks lacks the assigner (ImportError) or
serves the sticky unit assignment, fails a check, and the capability reports
false.

Runs once per process (on the first /capabilities request) and fails closed.
"""
import json
import threading
from concurrent.futures import Future

import jsons

from sdk.absmartly import ABSmartly
from sdk.absmartly_config import ABSmartlyConfig
from sdk.client import Client
from sdk.client_config import ClientConfig
from sdk.context_config import ContextConfig
from sdk.context_event_logger import ContextEventLogger, EventType
from sdk.context_publisher import ContextPublisher
from sdk.default_context_event_serializer import DefaultContextEventSerializer
from sdk.default_http_client import DefaultHTTPClient
from sdk.default_http_client_config import DefaultHTTPClientConfig
from sdk.json.context_data import ContextData
from sdk.json.experiment import Experiment

PREFIX = '[switchback probe]'

# Grid shared with generate_switchback_scenarios.py: with a 400,000 h period
# "now" stays in period 0 until 2069.
PERIOD_MS = 400_000 * 3_600_000
ANCHOR_PERIOD_0 = 1_704_067_200_000
# hash_unit("serving-9"), as published in PublishEvent.units.
SERVING_UNIT_HASH = 'r5uxZRoi-JuM5yYJa0kmSQ'

# (clusterUnit, periodIndex, seed, armCount, expectedVariant) from the
# platform golden vectors, including the 64-bit bounds.
ASSIGN_CASES = [
    ('market_1', '0', '0', 2, 0),
    ('market_1', '-9223372036854775808', '1', 2, 1),
    ('market_1', '9223372036854775807', '1', 2, 1),
    ('123456789', '0', '0', 2, 1),
    ('market_1', '10', '0', 3, 2),
    ('123456789', '-2', '-1', 3, 0),
    ('market_1', '5', '1', 10, 0),
    ('market_2', '-11', '-9223372036854775808', 10, 3),
    ('market_2', '9223372036854775807', '-9223372036854775808', 10, 2),
    ('market_1', '1', '9223372036854775807', 2, 1),
]
PERMUTATION_CASES = [
    ('market_1', '0', '0', [0, 3, 7, 5, 8, 2, 6, 4, 1, 9]),
    ('market_1', '-9223372036854775808', '0', [1, 0]),
    ('market_1', '9223372036854775807', '1', [0, 1]),
    ('123456789', '0', '0', [2, 0, 1]),
]

R33_FIELDS = ('randomizationType', 'switchbackClusters',
              'switchbackClusterValues', 'switchbackStartAt',
              'switchbackPeriodMs')


def assigner():
    """The SDK's switchback assigner module, or None when it has none."""
    try:
        from sdk.internal import switchback_assigner
    except ImportError:
        return None
    return switchback_assigner


def assign(cluster_unit, period_index, seed, arm_count):
    return assigner().assign(cluster_unit, period_index, seed, arm_count)


def permutation(cluster_unit, cycle_index, seed, arm_count):
    module = assigner()
    return module.fisher_yates_permutation(
        arm_count, module.base_hash(cluster_unit, cycle_index, seed))


def check_golden_sample():
    if assigner() is None:
        print(f'{PREFIX} FAILED: python3-sdk has no switchback assigner',
              flush=True)
        return False
    for unit, period, seed, arms, want in ASSIGN_CASES:
        got = assign(unit, int(period), int(seed), arms)
        if got != want:
            print(f'{PREFIX} golden assign FAILED for {unit}/{period}/{seed}/'
                  f'{arms}: got {got}, expected {want}', flush=True)
            return False
    for unit, cycle, seed, want in PERMUTATION_CASES:
        got = permutation(unit, int(cycle), int(seed), len(want))
        if got != want:
            print(f'{PREFIX} golden permutation FAILED for {unit}/{cycle}/'
                  f'{seed}: got {got}, expected {want}', flush=True)
            return False
    return True


def _base(id, name, seed_hi, seed_lo, arms):
    return {
        'id': id, 'name': name, 'iteration': 1, 'unitType': 'unit_serving',
        'seedHi': seed_hi, 'seedLo': seed_lo, 'split': [1.0 / arms] * arms,
        'trafficSeedHi': 1, 'trafficSeedLo': 2, 'trafficSplit': [0, 1],
        'fullOnVariant': 0, 'applications': [{'name': 'website'}],
        'variants': [{'name': chr(ord('A') + i), 'config': None}
                     for i in range(arms)],
        'audienceStrict': False,
    }


def _switchback(id, name, seed_hi, seed_lo, arms, clusters, **fields):
    experiment = _base(id, name, seed_hi, seed_lo, arms)
    experiment['randomizationType'] = 'switchback'
    if clusters is not None:
        experiment['switchbackClusters'] = {t: p for t, p in clusters}
        experiment['unitType'] = clusters[0][0]
    experiment.update({
        'switchbackStartAt': ANCHOR_PERIOD_0,
        'switchbackPeriodMs': PERIOD_MS,
        'switchbackDesignType': 'balanced_block',
        'switchbackEstimand': 'global_average_treatment_effect',
    })
    experiment.update(fields)
    return experiment


def _unit(id, name, **fields):
    # Seeds whose 3-arm unit draw for serving-9 is 1.
    experiment = _base(id, name, -559038737, 1234567890, 3)
    experiment.update(fields)
    return experiment


def probe_payload():
    """The java-wrapper probe payload. The 10-arm expectations differ from
    every wrong model (hashed key, uid as key, hashed uid, Phase 1E bucket,
    sticky unit assignment) for both context uids:
    assign("market_1", 0, 42, 10) = 3 and assign("123456789", 0, 42, 10) = 6.
    """
    return json.dumps({'experiments': [
        _switchback(521, 'sb_agree', 0, 42, 10,
                    [('unit_serving', 'city'), ('unit_user', 'profile/city')]),
        _switchback(522, 'sb_declared_order', 0, 42, 10,
                    [('unit_user', 'profile/city'), ('unit_serving', 'city')]),
        _switchback(523, 'sb_disagree', 0, 42, 10,
                    [('unit_serving', 'city'), ('unit_user', 'profile/zone')]),
        _switchback(524, 'sb_whole_number', 0, 42, 10,
                    [('unit_serving', 'store_number')]),
        _switchback(529, 'sb_whole_float', 0, 42, 10,
                    [('unit_serving', 'store_float')]),
        _switchback(528, 'sb_allow_empty', 0, 42, 10,
                    [('unit_serving', 'city')], switchbackClusterValues=[]),
        _switchback(525, 'sb_value_filtered', 0, 1, 10,
                    [('unit_serving', 'city')],
                    switchbackClusterValues=['market_2']),
        _switchback(526, 'sb_phase1e_only', 0, 1, 10, None,
                    switchbackClusterUnitType='unit_serving',
                    switchbackClusterCount=10,
                    switchbackClusterAttribute='city'),
        _switchback(513, 'sb_payload', 0, 1, 3, [('unit_serving', 'city')]),
        _unit(514, 'sb_unit_baseline'),
        _unit(527, 'sb_unit_with_fields', randomizationType='unit',
              switchbackClusters={'unit_serving': 'city'},
              switchbackClusterValues=['market_1'],
              switchbackStartAt=ANCHOR_PERIOD_0,
              switchbackPeriodMs=PERIOD_MS,
              switchbackDesignType='balanced_block'),
    ]})


class _Exposures(ContextEventLogger):
    def __init__(self):
        self.exposures = []

    def handle_event(self, event_type, data):
        if event_type == EventType.EXPOSURE and data is not None:
            self.exposures.append(data)


class _Publisher(ContextPublisher):
    def __init__(self):
        self.events = []

    def publish(self, context, event):
        self.events.append(event)
        future = Future()
        future.set_result(None)
        return future


def _record(exposure):
    return (exposure.id, exposure.name, exposure.unit, exposure.variant,
            exposure.assigned, exposure.eligible, exposure.overridden,
            exposure.fullOn, exposure.custom, exposure.audienceMismatch,
            exposure.ruleOverride)


def _served(id, name, unit, variant):
    return (id, name, unit, variant, True, True, False, False, False, False,
            False)


def _expect(context, log, check, name, treatment, *expected):
    before = len(log.exposures)
    got = context.get_treatment(name)
    emitted = log.exposures[before:]
    if got != treatment or [_record(e) for e in emitted] != list(expected) \
            or any(not isinstance(e.exposedAt, int) or e.exposedAt <= 0
                   for e in emitted):
        print(f'{PREFIX} check {check} FAILED: expected treatment='
              f'{treatment} exposures={list(expected)}, got treatment={got} '
              f'exposures={[_record(e) for e in emitted]}', flush=True)
        return False
    return True


def _publish_one(context, publisher):
    before = len(publisher.events)
    context.publish()
    if len(publisher.events) != before + 1:
        return None
    return publisher.events[-1]


def _wire(event):
    return json.loads(DefaultContextEventSerializer().serialize(event))


WIRE_EXPOSURE_FIELDS = ('id', 'name', 'unit', 'variant', 'assigned',
                        'eligible', 'overridden', 'fullOn', 'custom',
                        'audienceMismatch', 'ruleOverride')


def _wire_mismatch(event, expected):
    """None when the serialized event carries exactly the expected exposure,
    under the hashed serving unit, with a positive integer exposedAt."""
    if event.get('hashed') is not True:
        return f'publish.hashed is {event.get("hashed")!r}'
    if ['unit_serving', SERVING_UNIT_HASH] not in \
            [[u.get('type'), u.get('uid')] for u in event.get('units') or []]:
        return f'publish.units lacks the hashed serving unit: ' \
               f'{event.get("units")}'
    exposures = event.get('exposures')
    if not isinstance(exposures, list) or len(exposures) != 1:
        return f'publish.exposures is {exposures!r}'
    actual = tuple(exposures[0].get(field) for field in WIRE_EXPOSURE_FIELDS)
    if actual != expected or any(type(a) is not type(b)
                                 for a, b in zip(actual, expected)):
        return f'publish.exposures[0] is {actual}, expected {expected}'
    exposed_at = exposures[0].get('exposedAt')
    if type(exposed_at) is not int or exposed_at <= 0:
        return f'publish.exposures[0].exposedAt is {exposed_at!r}'
    return None


def _shape(value):
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_shape(item) for item in value]
    return type(value).__name__


def _check_publish(context, log, publisher):
    """A unit baseline and a switchback on the same unit, each published on
    its own, have the same fields at every depth; a unit experiment carrying
    every R33 field publishes the same bytes as the baseline."""
    context.publish()
    before = len(log.exposures)
    if context.get_treatment('sb_unit_baseline') != 1:
        print(f'{PREFIX} check unit baseline FAILED: variant', flush=True)
        return False
    baseline = _publish_one(context, publisher)
    if context.get_treatment('sb_payload') != 1:
        print(f'{PREFIX} check payload FAILED: variant', flush=True)
        return False
    switchback = _publish_one(context, publisher)
    emitted = [_record(e) for e in log.exposures[before:]]
    expected = [_served(514, 'sb_unit_baseline', 'unit_serving', 1),
                _served(513, 'sb_payload', 'unit_serving', 1)]
    if baseline is None or switchback is None or emitted != expected:
        print(f'{PREFIX} check payload shape FAILED: exposures {emitted}',
              flush=True)
        return False
    baseline_json, switchback_json = _wire(baseline), _wire(switchback)
    for event, record in ((baseline_json, expected[0]),
                          (switchback_json, expected[1])):
        mismatch = _wire_mismatch(event, record)
        if mismatch is not None:
            print(f'{PREFIX} check publish payload FAILED: {mismatch}',
                  flush=True)
            return False
    if _shape(baseline_json) != _shape(switchback_json):
        print(f'{PREFIX} check publish shape FAILED: {switchback_json} vs '
              f'{baseline_json}', flush=True)
        return False

    before = len(log.exposures)
    if context.get_treatment('sb_unit_with_fields') != 1:
        print(f'{PREFIX} check unit inertness FAILED: variant', flush=True)
        return False
    with_fields = _publish_one(context, publisher)
    emitted = [_record(e) for e in log.exposures[before:]]
    if with_fields is None or emitted != [
            _served(527, 'sb_unit_with_fields', 'unit_serving', 1)]:
        print(f'{PREFIX} check unit inertness FAILED: exposures {emitted}',
              flush=True)
        return False
    with_fields_json = _wire(with_fields)
    mismatch = _wire_mismatch(
        with_fields_json, _served(527, 'sb_unit_with_fields', 'unit_serving', 1))
    if mismatch is not None:
        print(f'{PREFIX} check unit inertness FAILED: {mismatch}', flush=True)
        return False
    aligned = []
    for event in (baseline_json, with_fields_json):
        event.pop('publishedAt', None)
        event['exposures'][0].update({'id': 0, 'name': '', 'exposedAt': 0})
        aligned.append(json.dumps(event, sort_keys=True))
    if aligned[0] != aligned[1]:
        print(f'{PREFIX} check unit inertness FAILED: {aligned[1]} differs '
              f'from {aligned[0]}', flush=True)
        return False
    return True


def check_context_serving():
    missing = [f for f in R33_FIELDS if not hasattr(Experiment, f)]
    if missing or hasattr(Experiment, 'switchbackClusterCount'):
        print(f'{PREFIX} FAILED: Experiment lacks the R33 wire model '
              f'(missing {missing})', flush=True)
        return False
    data = jsons.load(json.loads(probe_payload()), ContextData)
    declared = next(e for e in data.experiments
                    if e.name == 'sb_declared_order')
    if list(declared.switchbackClusters) != ['unit_user', 'unit_serving']:
        print(f'{PREFIX} FAILED: switchbackClusters lost its declared order: '
              f'{list(declared.switchbackClusters)}', flush=True)
        return False

    client_config = ClientConfig()
    client_config.endpoint = 'http://dummy'
    client_config.api_key = 'dummy'
    client_config.application = 'test'
    client_config.environment = 'test'
    log = _Exposures()
    publisher = _Publisher()
    sdk_config = ABSmartlyConfig()
    sdk_config.client = Client(client_config,
                               DefaultHTTPClient(DefaultHTTPClientConfig()))
    sdk_config.context_event_logger = log
    sdk_config.context_event_handler = publisher
    config = ContextConfig()
    # unit_user is set first, so the declared order, not the context's,
    # must pick the serving unit.
    config.units = {'unit_user': 'probe-user', 'unit_serving': 'serving-9'}
    config.publish_delay = -1
    config.refresh_interval = 0
    context = ABSmartly(sdk_config).create_context_with(config, data)
    try:
        if not context.is_ready() or context.is_failed():
            print(f'{PREFIX} FAILED: context not ready', flush=True)
            return False
        context.set_attribute('city', 'market_1')
        context.set_attribute('profile', {'city': 'market_1',
                                          'zone': 'market_2'})
        context.set_attribute('store_number', 123456789)
        context.set_attribute('store_float', 123456789.0)
        return _expect(context, log, 'agreement', 'sb_agree', 3,
                       _served(521, 'sb_agree', 'unit_serving', 3)) and \
            _expect(context, log, 'agreement repeat', 'sb_agree', 3) and \
            _expect(context, log, 'declared order', 'sb_declared_order', 3,
                    _served(522, 'sb_declared_order', 'unit_user', 3)) and \
            _expect(context, log, 'disagreement', 'sb_disagree', 0) and \
            _expect(context, log, 'whole number', 'sb_whole_number', 6,
                    _served(524, 'sb_whole_number', 'unit_serving', 6)) and \
            _expect(context, log, 'float never keys', 'sb_whole_float', 0) \
            and _expect(context, log, 'value filtered out',
                        'sb_value_filtered', 0) and \
            _expect(context, log, 'empty allow-list admits all',
                    'sb_allow_empty', 3,
                    _served(528, 'sb_allow_empty', 'unit_serving', 3)) and \
            _expect(context, log, 'Phase 1E fields only', 'sb_phase1e_only',
                    0) and \
            _check_publish(context, log, publisher)
    finally:
        context.close()


_lock = threading.Lock()
_result = None


def run():
    global _result
    with _lock:
        if _result is None:
            try:
                _result = check_golden_sample() and check_context_serving()
                if _result:
                    print(f'{PREFIX} behavioral self-test PASSED', flush=True)
            except Exception as exc:
                print(f'{PREFIX} behavioral self-test FAILED with error: '
                      f'{exc!r}', flush=True)
                _result = False
        return _result
