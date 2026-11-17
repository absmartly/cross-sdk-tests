"""Behavioural self-test for the `holdouts` / `holdout_arms` capabilities.

Mirrors javascript-wrapper/holdoutSelfTest.js and the java-wrapper's
HoldoutSelfTest/HoldoutArmsSelfTest: build a real context from fixture values
ported from the scenario battery (203-208 and 221 for `holdouts`, 214-216 for
`holdout_arms`), call get_treatment and require both the returned variant and
the exact ordered id/name/variant sequence of exposure events that call
fired. A python3-sdk build without holdouts treats the fixture as plain
experiments, fails the checks, and the capability reports false.

Each battery runs once per process (on the first /capabilities request) and
fails closed: any exception makes the capability false instead of breaking
the endpoint.
"""
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
from sdk.default_http_client import DefaultHTTPClient
from sdk.default_http_client_config import DefaultHTTPClientConfig
from sdk.json.context_data import ContextData

HELD_OUT_UID = 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
NOT_HELD_OUT_UID = 'b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3'


def build_experiment(id, name, unit_type, full_on_variant=0, holdout_ids=None,
                     variant_names=('A', 'B'), split=(0.5, 0.5)):
    return {
        'id': id, 'name': name, 'iteration': 1, 'unitType': unit_type,
        'seedHi': 100, 'seedLo': 200, 'split': list(split),
        'trafficSeedHi': 1, 'trafficSeedLo': 2, 'trafficSplit': [0, 1],
        'fullOnVariant': full_on_variant,
        'applications': [{'name': 'website'}],
        'variants': [{'name': n, 'config': None} for n in variant_names],
        'audience': None, 'audienceStrict': False, 'customFieldValues': None,
        'holdoutIds': holdout_ids,
    }


def build_holdout(id, name, unit_type, seed_hi, seed_lo, split=(0.1, 0.9),
                  holdout_type='full'):
    return {
        'id': id, 'name': name, 'iteration': 1, 'unitType': unit_type,
        'seedHi': seed_hi, 'seedLo': seed_lo, 'split': list(split),
        'trafficSeedHi': 0, 'trafficSeedLo': 0, 'trafficSplit': [0, 1],
        'fullOnVariant': 0, 'applications': [],
        'variants': [{'name': n, 'config': None}
                     for n in 'ABC'[:len(split)]],
        'audience': None, 'audienceStrict': False, 'customFieldValues': None,
        'holdoutType': holdout_type,
    }


class _ExposureLog(ContextEventLogger):
    def __init__(self):
        self.exposures = []

    def handle_event(self, event_type, data):
        if event_type == EventType.EXPOSURE and data is not None:
            self.exposures.append((data.id, data.name, data.variant))


class _NoPublish(ContextPublisher):
    def publish(self, context, event):
        future = Future()
        future.set_result(None)
        return future


def _create_context(data, units):
    client_config = ClientConfig()
    client_config.endpoint = 'http://dummy'
    client_config.api_key = 'dummy'
    client_config.application = 'test'
    client_config.environment = 'test'
    client = Client(client_config, DefaultHTTPClient(DefaultHTTPClientConfig()))
    log = _ExposureLog()
    sdk_config = ABSmartlyConfig()
    sdk_config.client = client
    sdk_config.context_event_logger = log
    sdk_config.context_event_handler = _NoPublish()
    config = ContextConfig()
    config.units = dict(units)
    config.publish_delay = -1
    config.refresh_interval = 0
    context = ABSmartly(sdk_config).create_context_with(
        config, jsons.load(data, ContextData))
    if not context.is_ready() or context.is_failed():
        raise AssertionError('context not ready')
    return context, log


def _verify(prefix, label, context, log, name, treatment, expected):
    before = len(log.exposures)
    actual = context.get_treatment(name)
    fired = log.exposures[before:]
    if actual != treatment or fired != expected:
        print(f'{prefix} check {label} FAILED: expected treatment={treatment} '
              f'exposures={expected}, got treatment={actual} '
              f'exposures={fired}', flush=True)
        return False
    return True


def run_holdouts_battery():
    """Checks A-G mirror scenarios 203-208 and 221 (synthetic ids so they
    share one context; every value that drives assignment is the
    scenario's)."""
    held, not_held, late = 'session_id', 'session_id_b', 'user_id'
    data = {
        'experiments': [
            build_experiment(301, 'probe_a_covered', held, holdout_ids=[401]),
            build_experiment(302, 'probe_b_covered', not_held,
                             holdout_ids=[402]),
            build_experiment(304, 'probe_c_union', held,
                             holdout_ids=[404, 405]),
            build_experiment(305, 'probe_d_covered', held, holdout_ids=[406]),
            build_experiment(306, 'probe_d_uncovered_sibling', held),
            build_experiment(307, 'probe_e_dangling_plus_valid', held,
                             holdout_ids=[999, 407]),
            build_experiment(303, 'probe_f_fullon', held, full_on_variant=2,
                             holdout_ids=[403], variant_names=('A', 'B', 'C')),
            build_experiment(308, 'probe_g_late_unit', late,
                             full_on_variant=2, holdout_ids=[408],
                             variant_names=('A', 'B', 'C'),
                             split=(0.5, 0.5, 0.0)),
        ],
        'holdouts': [
            build_holdout(401, 'probe_a_holdout', held, 13, 111),
            build_holdout(402, 'probe_b_holdout', not_held, 13, 111),
            build_holdout(404, 'probe_c_holdout_low', held, 1, 222),
            build_holdout(405, 'probe_c_holdout_high', held, 13, 111),
            build_holdout(406, 'probe_d_holdout', held, 13, 111),
            build_holdout(407, 'probe_e_holdout', held, 13, 111),
            build_holdout(403, 'probe_f_holdout', held, 13, 111),
            build_holdout(408, 'probe_g_holdout', late, 1, 222),
        ],
    }
    context, log = _create_context(
        data, {held: HELD_OUT_UID, not_held: NOT_HELD_OUT_UID})
    prefix = '[holdouts probe]'
    checks = [
        ('A (mirrors 203)', 'probe_a_covered', 0,
         [(401, 'probe_a_holdout', 0)]),
        ('B (mirrors 204)', 'probe_b_covered', 0,
         [(302, 'probe_b_covered', 0), (402, 'probe_b_holdout', 1)]),
        ('C (mirrors 205)', 'probe_c_union', 0,
         [(404, 'probe_c_holdout_low', 1), (405, 'probe_c_holdout_high', 0)]),
        ('D (mirrors 206, covered)', 'probe_d_covered', 0,
         [(406, 'probe_d_holdout', 0)]),
        ('D (mirrors 206, uncovered sibling)', 'probe_d_uncovered_sibling', 1,
         [(306, 'probe_d_uncovered_sibling', 1)]),
        ('E (mirrors 207)', 'probe_e_dangling_plus_valid', 0,
         [(407, 'probe_e_holdout', 0)]),
        ('F (mirrors 208)', 'probe_f_fullon', 0,
         [(403, 'probe_f_holdout', 0)]),
    ]
    for label, name, treatment, expected in checks:
        if not _verify(prefix, label, context, log, name, treatment, expected):
            return False

    # G (mirrors 221): the full-on experiment resolves before its unit is
    # set, without exposing; once the unit is set, the holdout exposure must
    # still be published.
    before = len(log.exposures)
    if context.peek_treatment('probe_g_late_unit') != 2 or \
            log.exposures[before:]:
        print(f'{prefix} check G (mirrors 221, pre-unit peek) FAILED',
              flush=True)
        return False
    context.set_unit(late, HELD_OUT_UID)
    if not _verify(prefix, 'G (mirrors 221, post-unit treatment)', context,
                   log, 'probe_g_late_unit', 2,
                   [(308, 'probe_g_late_unit', 2),
                    (408, 'probe_g_holdout', 1)]):
        return False
    print(f'{prefix} behavioral self-test PASSED: all 7 checks mirroring '
          f'scenarios 203-208 and 221 passed', flush=True)
    return True


def run_holdout_arms_battery():
    """Checks 0-2 mirror scenarios 214-216 (same ids as the scenarios)."""
    unit = 'session_id'
    split = (0.3, 0.3, 0.4)
    experiments, holdouts = [], []
    for arm, (exp_id, holdout_id, seed_lo) in enumerate(
            ((601, 621, 1), (603, 622, 3), (605, 623, 0))):
        experiments.append(build_experiment(
            exp_id, f'arms_{arm}_non_fullon', unit, holdout_ids=[holdout_id]))
        experiments.append(build_experiment(
            exp_id + 1, f'arms_{arm}_fullon', unit, full_on_variant=2,
            holdout_ids=[holdout_id], variant_names=('A', 'B', 'C')))
        holdouts.append(build_holdout(holdout_id, f'arms_holdout_{arm}', unit,
                                      0, seed_lo, split=split,
                                      holdout_type='all_full_on'))
    context, log = _create_context(
        {'experiments': experiments, 'holdouts': holdouts},
        {unit: HELD_OUT_UID})
    prefix = '[holdout_arms probe]'
    checks = [
        ('0 non-full-on (mirrors 214)', 'arms_0_non_fullon', 0,
         [(621, 'arms_holdout_0', 0)]),
        ('0 full-on (mirrors 214)', 'arms_0_fullon', 0, []),
        ('1 non-full-on (mirrors 215)', 'arms_1_non_fullon', 0,
         [(622, 'arms_holdout_1', 1)]),
        ('1 full-on (mirrors 215)', 'arms_1_fullon', 2,
         [(604, 'arms_1_fullon', 2)]),
        ('2 non-full-on (mirrors 216)', 'arms_2_non_fullon', 1,
         [(605, 'arms_2_non_fullon', 1), (623, 'arms_holdout_2', 2)]),
        ('2 full-on (mirrors 216)', 'arms_2_fullon', 2,
         [(606, 'arms_2_fullon', 2)]),
    ]
    for label, name, treatment, expected in checks:
        if not _verify(prefix, label, context, log, name, treatment, expected):
            return False
    print(f'{prefix} behavioral self-test PASSED: all 3 arm checks mirroring '
          f'scenarios 214-216 passed', flush=True)
    return True


_lock = threading.Lock()
_results = {}


def _run_once(key, battery):
    with _lock:
        if key not in _results:
            try:
                _results[key] = bool(battery())
            except Exception as exc:
                print(f'[{key} probe] behavioral self-test FAILED with '
                      f'error: {exc!r}', flush=True)
                _results[key] = False
        return _results[key]


def run_holdouts():
    return _run_once('holdouts', run_holdouts_battery)


def run_holdout_arms():
    # Three-arm support cannot exist without the two-arm foundation.
    if not run_holdouts():
        print('[holdout_arms probe] skipped: base holdouts battery did not '
              'pass', flush=True)
        return False
    return _run_once('holdout_arms', run_holdout_arms_battery)
