// Behavioral self-test for the `holdouts` / `holdout_arms` wrapper
// capabilities. A port of javascript-wrapper/holdoutSelfTest.js (same fixtures,
// same checks, mirroring scenarios 203-208, 214-216 and 221): build a real
// context payload as JSON, parse it with the SDK's own ContextData.fromMap,
// create a real Context, call getTreatment and require BOTH the returned
// variant and the exact ordered exposure sequence that call fires.
//
// Run once per wrapper process and cached (the Future itself, so concurrent
// /capabilities requests share one run). Fails closed: an SDK without
// holdouts ignores holdoutIds, so the checks fail and the capability reports
// false; any exception reports false too.
import 'dart:async';

import 'package:absmartly_dart/absmartly_dart.dart';

class _ExposureLog implements ContextEventLogger {
  final List<Exposure> exposures = [];

  @override
  void handleEvent(Context context, EventType type, dynamic data) {
    if (type == EventType.exposure && data is Exposure) {
      exposures.add(data);
    }
  }
}

class _NoPublisher implements ContextPublisher {
  @override
  Completer<void> publish(Context context, PublishEvent event) =>
      Completer<void>()..complete();
}

class _NoHttpClient implements HTTPClient {
  @override
  Future<Response> get(String url, Map<String, String>? query,
          Map<String, String>? headers) =>
      Future.error(UnsupportedError('self-test is offline'));

  @override
  Future<Response> post(String url, Map<String, String>? query,
          Map<String, String>? headers, List<int>? body) =>
      Future.error(UnsupportedError('self-test is offline'));

  @override
  Future<Response> put(String url, Map<String, String>? query,
          Map<String, String>? headers, List<int>? body) =>
      Future.error(UnsupportedError('self-test is offline'));

  @override
  void close() {}
}

class _E {
  const _E(this.id, this.name, this.variant);

  final int id;
  final String name;
  final int variant;

  @override
  bool operator ==(Object other) =>
      other is _E &&
      id == other.id &&
      name == other.name &&
      variant == other.variant;

  @override
  int get hashCode => Object.hash(id, name, variant);

  @override
  String toString() => '{id: $id, name: $name, variant: $variant}';
}

Map<String, dynamic> _experiment(int id, String name, String unitType,
    List<double> split, int fullOnVariant, List<String> variants,
    List<int>? holdoutIds) {
  return {
    'id': id,
    'name': name,
    'iteration': 1,
    'unitType': unitType,
    'seedHi': 100,
    'seedLo': 200,
    'split': split,
    'trafficSeedHi': 1,
    'trafficSeedLo': 2,
    'trafficSplit': [0, 1],
    'fullOnVariant': fullOnVariant,
    'applications': [
      {'name': 'website'}
    ],
    'variants': [
      for (final variant in variants) {'name': variant, 'config': null}
    ],
    'audience': null,
    'audienceStrict': false,
    'customFieldValues': null,
    'holdoutIds': holdoutIds,
  };
}

Map<String, dynamic> _holdout(int id, String name, String unitType, int seedHi,
    int seedLo, List<double> split, String holdoutType) {
  return {
    'id': id,
    'name': name,
    'iteration': 1,
    'unitType': unitType,
    'seedHi': seedHi,
    'seedLo': seedLo,
    'split': split,
    'trafficSeedHi': 0,
    'trafficSeedLo': 0,
    'trafficSplit': [0, 1],
    'fullOnVariant': 0,
    'applications': [],
    'variants': [
      for (int i = 0; i < split.length; i++)
        {'name': String.fromCharCode(65 + i), 'config': null}
    ],
    'audience': null,
    'audienceStrict': false,
    'customFieldValues': null,
    'holdoutType': holdoutType,
  };
}

Future<Context> _context(Map<String, dynamic> payload,
    Map<String, Object> units, _ExposureLog log) async {
  final client = Client.create(
      ClientConfig.create()
          .setEndpoint('http://dummy')
          .setAPIKey('dummy')
          .setApplication('test')
          .setEnvironment('test'),
      httpClient: _NoHttpClient());
  final sdk = ABSmartly(ABSmartlyConfig.create()
      .setClient(client)
      .setContextPublisher(_NoPublisher())
      .setContextEventLogger(log));
  final config = ContextConfig()
    ..setUnits(units)
    ..setPublishDelay(-1)
    ..setRefreshInterval(0);
  final context = sdk.createContextWith(config, ContextData.fromMap(payload));
  await context.waitUntilReady();
  return context;
}

bool _check(String prefix, String label, Context context, _ExposureLog log,
    String experiment, int expectedVariant, List<_E> expected) {
  final before = log.exposures.length;
  final variant = context.getTreatment(experiment);
  final fired = [
    for (final e in log.exposures.sublist(before)) _E(e.id, e.name, e.variant)
  ];
  var ok = variant == expectedVariant && fired.length == expected.length;
  for (int i = 0; ok && i < expected.length; i++) {
    ok = fired[i] == expected[i];
  }
  if (!ok) {
    print('$prefix check $label FAILED: expected treatment=$expectedVariant '
        'with exposures $expected, got treatment=$variant exposures=$fired');
  }
  return ok;
}

const _heldOutUid = 'e791e240fcd3df7d238cfc285f475e8152fcc0ec';
const _notHeldOutUid = 'b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3';

Future<bool> _holdoutsBattery() async {
  const prefix = '[holdouts probe]';
  const s = 'session_id';
  const b = 'session_id_b';
  const lateUnit = 'user_id';
  final payload = {
    'experiments': [
      _experiment(301, 'probe_a_covered', s, [0.5, 0.5], 0, ['A', 'B'], [401]),
      _experiment(302, 'probe_b_covered', b, [0.5, 0.5], 0, ['A', 'B'], [402]),
      _experiment(
          304, 'probe_c_union', s, [0.5, 0.5], 0, ['A', 'B'], [404, 405]),
      _experiment(305, 'probe_d_covered', s, [0.5, 0.5], 0, ['A', 'B'], [406]),
      _experiment(
          306, 'probe_d_uncovered_sibling', s, [0.5, 0.5], 0, ['A', 'B'], null),
      _experiment(307, 'probe_e_dangling_plus_valid', s, [0.5, 0.5], 0,
          ['A', 'B'], [999, 407]),
      _experiment(
          303, 'probe_f_fullon', s, [0.5, 0.5], 2, ['A', 'B', 'C'], [403]),
      _experiment(308, 'probe_g_late_unit', lateUnit, [0.5, 0.5, 0.0], 2,
          ['A', 'B', 'C'], [408]),
    ],
    'holdouts': [
      _holdout(401, 'probe_a_holdout', s, 13, 111, [0.1, 0.9], 'full'),
      _holdout(402, 'probe_b_holdout', b, 13, 111, [0.1, 0.9], 'full'),
      _holdout(404, 'probe_c_holdout_low', s, 1, 222, [0.1, 0.9], 'full'),
      _holdout(405, 'probe_c_holdout_high', s, 13, 111, [0.1, 0.9], 'full'),
      _holdout(406, 'probe_d_holdout', s, 13, 111, [0.1, 0.9], 'full'),
      _holdout(407, 'probe_e_holdout', s, 13, 111, [0.1, 0.9], 'full'),
      _holdout(403, 'probe_f_holdout', s, 13, 111, [0.1, 0.9], 'full'),
      _holdout(408, 'probe_g_holdout', lateUnit, 1, 222, [0.1, 0.9], 'full'),
    ],
  };
  final log = _ExposureLog();
  final context =
      await _context(payload, {s: _heldOutUid, b: _notHeldOutUid}, log);
  if (!context.isReady() || context.isFailed()) {
    print('$prefix FAILED: context not ready');
    return false;
  }

  final ok = _check(prefix, 'A (203)', context, log, 'probe_a_covered', 0,
          const [_E(401, 'probe_a_holdout', 0)]) &&
      _check(prefix, 'B (204)', context, log, 'probe_b_covered', 0, const [
        _E(302, 'probe_b_covered', 0),
        _E(402, 'probe_b_holdout', 1),
      ]) &&
      _check(prefix, 'C (205)', context, log, 'probe_c_union', 0, const [
        _E(404, 'probe_c_holdout_low', 1),
        _E(405, 'probe_c_holdout_high', 0),
      ]) &&
      _check(prefix, 'D (206)', context, log, 'probe_d_covered', 0,
          const [_E(406, 'probe_d_holdout', 0)]) &&
      _check(prefix, 'D (206, sibling)', context, log,
          'probe_d_uncovered_sibling', 1,
          const [_E(306, 'probe_d_uncovered_sibling', 1)]) &&
      _check(prefix, 'E (207)', context, log, 'probe_e_dangling_plus_valid', 0,
          const [_E(407, 'probe_e_holdout', 0)]) &&
      _check(prefix, 'F (208)', context, log, 'probe_f_fullon', 0,
          const [_E(403, 'probe_f_holdout', 0)]);
  if (!ok) {
    return false;
  }

  // G (221): resolved before its unit exists, the holdout exposure is still
  // published once the unit is set.
  final before = log.exposures.length;
  if (context.peekTreatment('probe_g_late_unit') != 2 ||
      log.exposures.length != before) {
    print('$prefix check G (221, pre-unit peek) FAILED');
    return false;
  }
  context.setUnit(lateUnit, _heldOutUid);
  if (!_check(prefix, 'G (221)', context, log, 'probe_g_late_unit', 2, const [
    _E(308, 'probe_g_late_unit', 2),
    _E(408, 'probe_g_holdout', 1),
  ])) {
    return false;
  }

  print('$prefix behavioral self-test PASSED');
  return true;
}

Future<bool> _holdoutArmsBattery() async {
  const prefix = '[holdout_arms probe]';
  const s = 'session_id';
  const threeArms = [0.3, 0.3, 0.4];
  final payload = {
    'experiments': [
      _experiment(
          601, 'arms_0_non_fullon', s, [0.5, 0.5], 0, ['A', 'B'], [621]),
      _experiment(
          602, 'arms_0_fullon', s, [0.5, 0.5], 2, ['A', 'B', 'C'], [621]),
      _experiment(
          603, 'arms_1_non_fullon', s, [0.5, 0.5], 0, ['A', 'B'], [622]),
      _experiment(
          604, 'arms_1_fullon', s, [0.5, 0.5], 2, ['A', 'B', 'C'], [622]),
      _experiment(
          605, 'arms_2_non_fullon', s, [0.5, 0.5], 0, ['A', 'B'], [623]),
      _experiment(
          606, 'arms_2_fullon', s, [0.5, 0.5], 2, ['A', 'B', 'C'], [623]),
    ],
    'holdouts': [
      _holdout(621, 'arms_holdout_0', s, 0, 1, threeArms, 'all_full_on'),
      _holdout(622, 'arms_holdout_1', s, 0, 3, threeArms, 'all_full_on'),
      _holdout(623, 'arms_holdout_2', s, 0, 0, threeArms, 'all_full_on'),
    ],
  };
  final log = _ExposureLog();
  final context = await _context(payload, {s: _heldOutUid}, log);
  if (!context.isReady() || context.isFailed()) {
    print('$prefix FAILED: context not ready');
    return false;
  }

  final ok = _check(prefix, '0 (214)', context, log, 'arms_0_non_fullon', 0,
          const [_E(621, 'arms_holdout_0', 0)]) &&
      _check(prefix, '0 full-on (214)', context, log, 'arms_0_fullon', 0,
          const []) &&
      _check(prefix, '1 (215)', context, log, 'arms_1_non_fullon', 0,
          const [_E(622, 'arms_holdout_1', 1)]) &&
      _check(prefix, '1 full-on (215)', context, log, 'arms_1_fullon', 2,
          const [_E(604, 'arms_1_fullon', 2)]) &&
      _check(prefix, '2 (216)', context, log, 'arms_2_non_fullon', 1, const [
        _E(605, 'arms_2_non_fullon', 1),
        _E(623, 'arms_holdout_2', 2),
      ]) &&
      _check(prefix, '2 full-on (216)', context, log, 'arms_2_fullon', 2,
          const [_E(606, 'arms_2_fullon', 2)]);
  if (ok) {
    print('$prefix behavioral self-test PASSED');
  }
  return ok;
}

Future<bool> _safely(String prefix, Future<bool> Function() battery) async {
  try {
    return await battery();
  } catch (error, stack) {
    print('$prefix behavioral self-test FAILED with error: $error\n$stack');
    return false;
  }
}

Future<bool>? _holdouts;
Future<bool>? _holdoutArms;

Future<bool> runHoldouts() =>
    _holdouts ??= _safely('[holdouts probe]', _holdoutsBattery);

// Three arms cannot work without basic holdout support, so it is a
// precondition, not a second independently passable check.
Future<bool> runHoldoutArms() => _holdoutArms ??= _runHoldoutArms();

Future<bool> _runHoldoutArms() async {
  if (!await runHoldouts()) {
    print('[holdout_arms probe] skipped: base holdouts battery failed');
    return false;
  }
  return _safely('[holdout_arms probe]', _holdoutArmsBattery);
}
