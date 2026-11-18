// Behavioral self-test behind the `switchback` capability (R33,
// cluster-defined switchbacks). A port of
// java-wrapper/.../SwitchbackSelfTest.java with the same probe payload and
// expected values: a sample of the platform golden vectors through the SDK's
// own assigner, then switchback serving through a real Context on an R33 JSON
// payload parsed with the SDK's own ContextData.fromMap (ordered
// switchbackClusters, switchbackClusterValues), on the clock-free grid the
// scenarios use. Each check compares the treatment and the full exposure
// record; the verdict always comes from observed treatments and exposures.
//
// Run once per wrapper process and cached (the Future itself, so concurrent
// /capabilities requests share one run). Fails closed: an SDK without
// switchbacks serves sticky unit assignment and fails the checks, and any
// exception (for example a missing SwitchbackAssigner) reports false.
import 'dart:async';
import 'dart:convert';

import 'package:absmartly_dart/absmartly_dart.dart';
import 'package:absmartly_dart/src/internal/switchback_assigner.dart';

const String _prefix = '[switchback probe]';

// Grid constants shared with generate_switchback_scenarios.py: with a
// 400,000 h period "now" stays in period 0 until 2069.
const int _periodMs = 400000 * 3600000;
const int _anchorPeriod0 = 1704067200000;

// hashUnit("serving-9"): base64url-no-pad MD5, as published in units.
const String _servingUnitHash = 'r5uxZRoi-JuM5yYJa0kmSQ';

// [clusterUnit, periodIndex, seed, armCount, expectedVariant], from the
// platform golden vectors, including Long.MIN_VALUE/MAX_VALUE periods and
// seeds.
const List<List<String>> _assignCases = [
  ['market_1', '0', '0', '2', '0'],
  ['market_1', '-9223372036854775808', '1', '2', '1'],
  ['market_1', '9223372036854775807', '1', '2', '1'],
  ['123456789', '0', '0', '2', '1'],
  ['market_1', '10', '0', '3', '2'],
  ['123456789', '-2', '-1', '3', '0'],
  ['market_1', '5', '1', '10', '0'],
  ['market_2', '-11', '-9223372036854775808', '10', '3'],
  ['market_2', '9223372036854775807', '-9223372036854775808', '10', '2'],
  ['market_1', '1', '9223372036854775807', '2', '1'],
];

// [clusterUnit, cycleIndex, seed, expectedPermutation].
const List<List<Object>> _permutationCases = [
  ['market_1', '0', '0', [0, 3, 7, 5, 8, 2, 6, 4, 1, 9]],
  ['market_1', '-9223372036854775808', '0', [1, 0]],
  ['market_1', '9223372036854775807', '1', [0, 1]],
  ['123456789', '0', '0', [2, 0, 1]],
];

/// assignSwitchback(clusterUnit, periodIndex, seed, armCount); 64-bit values
/// as decimal strings. Throws [ArgumentError] when armCount < 2.
int switchbackAssign(
        String clusterUnit, String periodIndex, String seed, int armCount) =>
    SwitchbackAssigner.assign(
        clusterUnit, _long(periodIndex), _long(seed), armCount);

/// fisherYatesPermutation(armCount, baseHash(clusterUnit, cycleIndex, seed)).
List<int> switchbackPermutation(
        String clusterUnit, String cycleIndex, String seed, int armCount) =>
    SwitchbackAssigner.fisherYatesPermutation(armCount,
        SwitchbackAssigner.baseHash(clusterUnit, _long(cycleIndex), _long(seed)));

final BigInt _longMin = -(BigInt.one << 63);
final BigInt _longMax = (BigInt.one << 63) - BigInt.one;

BigInt _long(String text) {
  if (!RegExp(r'^-?[0-9]+$').hasMatch(text)) {
    throw ArgumentError('not a decimal integer: $text');
  }
  final BigInt value = BigInt.parse(text);
  if (value < _longMin || value > _longMax) {
    throw ArgumentError('out of the signed 64-bit range: $text');
  }
  return value;
}

class _ExposureLog implements ContextEventLogger {
  final List<Exposure> exposures = [];

  @override
  void handleEvent(Context context, EventType type, dynamic data) {
    if (type == EventType.exposure && data is Exposure) {
      exposures.add(data);
    }
  }
}

class _Publisher implements ContextPublisher {
  final List<PublishEvent> events = [];

  @override
  Completer<void> publish(Context context, PublishEvent event) {
    events.add(event);
    return Completer<void>()..complete();
  }
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

Future<bool>? _switchback;

Future<bool> runSwitchback() => _switchback ??= _runSafely();

Future<bool> _runSafely() async {
  try {
    if (!_checkGoldenSample() || !await _checkContextServing()) {
      return false;
    }
    print('$_prefix behavioral self-test PASSED');
    return true;
  } catch (error) {
    print('$_prefix behavioral self-test FAILED with exception: $error');
    return false;
  }
}

bool _checkGoldenSample() {
  for (final c in _assignCases) {
    final int actual = switchbackAssign(c[0], c[1], c[2], int.parse(c[3]));
    if (actual != int.parse(c[4])) {
      print('$_prefix golden assign FAILED for $c: got $actual');
      return false;
    }
  }
  for (final c in _permutationCases) {
    final List<int> expected = c[3] as List<int>;
    final List<int> actual = switchbackPermutation(
        c[0] as String, c[1] as String, c[2] as String, expected.length);
    if (actual.join(',') != expected.join(',')) {
      print('$_prefix golden permutation FAILED for $c: got $actual');
      return false;
    }
  }
  return true;
}

Map<String, dynamic> _base(
    int id, String name, int seedHi, int seedLo, int arms) {
  return {
    'id': id,
    'name': name,
    'iteration': 1,
    'unitType': 'unit_serving',
    'seedHi': seedHi,
    'seedLo': seedLo,
    'split': [for (int i = 0; i < arms; i++) 1.0 / arms],
    'trafficSeedHi': 1,
    'trafficSeedLo': 2,
    'trafficSplit': [0, 1],
    'fullOnVariant': 0,
    'applications': [
      {'name': 'website'}
    ],
    'variants': [
      for (int i = 0; i < arms; i++)
        {'name': String.fromCharCode(0x41 + i), 'config': null}
    ],
    'audienceStrict': false,
  };
}

Map<String, dynamic> _switchbackExperiment(int id, String name, int seedHi,
    int seedLo, int arms, List<List<String>>? clusters) {
  final experiment = _base(id, name, seedHi, seedLo, arms);
  experiment['randomizationType'] = 'switchback';
  if (clusters != null) {
    experiment['switchbackClusters'] = {for (final c in clusters) c[0]: c[1]};
    experiment['unitType'] = clusters[0][0];
  }
  experiment['switchbackStartAt'] = _anchorPeriod0;
  experiment['switchbackPeriodMs'] = _periodMs;
  experiment['switchbackDesignType'] = 'balanced_block';
  experiment['switchbackEstimand'] = 'global_average_treatment_effect';
  return experiment;
}

// The unit baseline: seeds whose 3-arm unit draw for serving-9 is 1.
Map<String, dynamic> _unitExperiment(int id, String name) =>
    _base(id, name, -559038737, 1234567890, 3);

// The probe payload of java-wrapper SwitchbackSelfTest.probePayload, as JSON
// text, so the cluster map's key order goes through the SDK's own parsing.
// Every expected variant is a platform golden vector; the 10-arm ones differ
// from every wrong model (hashed key, uid as key, hashed uid, a Phase 1E
// bucket, the sticky unit assignment) for both context uids.
String probePayload() {
  return jsonEncode({
    'experiments': [
      _switchbackExperiment(521, 'sb_agree', 0, 42, 10, [
        ['unit_serving', 'city'],
        ['unit_user', 'profile/city']
      ]),
      _switchbackExperiment(522, 'sb_declared_order', 0, 42, 10, [
        ['unit_user', 'profile/city'],
        ['unit_serving', 'city']
      ]),
      _switchbackExperiment(523, 'sb_disagree', 0, 42, 10, [
        ['unit_serving', 'city'],
        ['unit_user', 'profile/zone']
      ]),
      _switchbackExperiment(524, 'sb_whole_number', 0, 42, 10, [
        ['unit_serving', 'store_number']
      ]),
      // switchbackClusterValues [] admits every value, like null.
      _switchbackExperiment(528, 'sb_allow_empty', 0, 42, 10, [
        ['unit_serving', 'city']
      ])
        ..['switchbackClusterValues'] = [],
      _switchbackExperiment(525, 'sb_value_filtered', 0, 1, 10, [
        ['unit_serving', 'city']
      ])
        ..['switchbackClusterValues'] = ['market_2'],
      // Only the abandoned Phase 1E fields: no cluster map, so the legacy
      // ineligible control.
      _switchbackExperiment(526, 'sb_phase1e_only', 0, 1, 10, null)
        ..['switchbackClusterUnitType'] = 'unit_serving'
        ..['switchbackClusterCount'] = 10
        ..['switchbackClusterAttribute'] = 'city',
      _switchbackExperiment(513, 'sb_payload', 0, 1, 3, [
        ['unit_serving', 'city']
      ]),
      _unitExperiment(514, 'sb_unit_baseline'),
      _unitExperiment(527, 'sb_unit_with_fields')
        ..['randomizationType'] = 'unit'
        ..['switchbackClusters'] = {'unit_serving': 'city'}
        ..['switchbackClusterValues'] = ['market_1']
        ..['switchbackStartAt'] = _anchorPeriod0
        ..['switchbackPeriodMs'] = _periodMs
        ..['switchbackCarryoverDurationMs'] = 0
        ..['switchbackBurnInDurationMs'] = 0
        ..['switchbackBurnOutDurationMs'] = 0
        ..['switchbackDesignType'] = 'balanced_block'
        ..['switchbackEstimand'] = 'global_average_treatment_effect',
    ]
  });
}

class _E {
  const _E(this.id, this.name, this.unit, this.variant);

  final int id;
  final String name;
  final String unit;
  final int variant;

  bool matches(Exposure e) =>
      e.id == id &&
      e.name == name &&
      e.unit == unit &&
      e.variant == variant &&
      e.assigned &&
      e.eligible &&
      !e.overridden &&
      !e.fullOn &&
      !e.custom &&
      !e.audienceMismatch &&
      !e.ruleOverride &&
      e.exposedAt > 0;

  @override
  String toString() => '{id: $id, name: $name, unit: $unit, variant: $variant}';
}

Future<bool> _checkContextServing() async {
  final data = ContextData.fromMap(
      jsonDecode(probePayload()) as Map<String, dynamic>);
  final order = data.experiments
      .firstWhere((e) => e.name == 'sb_declared_order')
      .switchbackClusters
      ?.keys
      .toList();
  if (order == null || order.join(',') != 'unit_user,unit_serving') {
    print('$_prefix FAILED: switchbackClusters lost its declared order: $order');
    return false;
  }

  final log = _ExposureLog();
  final publisher = _Publisher();
  final client = Client.create(
      ClientConfig.create()
          .setEndpoint('http://dummy')
          .setAPIKey('dummy')
          .setApplication('test')
          .setEnvironment('test'),
      httpClient: _NoHttpClient());
  final sdk = ABSmartly(ABSmartlyConfig.create()
      .setClient(client)
      .setContextPublisher(publisher)
      .setContextEventLogger(log));
  // unit_user is set first, so declared order, not context order, must pick
  // the serving unit.
  final config = ContextConfig()
    ..setUnit('unit_user', 'probe-user')
    ..setUnit('unit_serving', 'serving-9')
    ..setPublishDelay(-1)
    ..setRefreshInterval(0);
  final context = sdk.createContextWith(config, data);
  await context.waitUntilReady();
  if (context.isFailed()) {
    print('$_prefix FAILED: context failed to load');
    return false;
  }
  context.setAttribute('city', 'market_1');
  context.setAttribute('profile', {'city': 'market_1', 'zone': 'market_2'});
  context.setAttribute('store_number', 123456789);

  bool expectServed(String check, String name, int treatment,
      [List<_E> expected = const []]) {
    final int before = log.exposures.length;
    final int actual = context.getTreatment(name);
    final emitted = log.exposures.sublist(before);
    bool ok = actual == treatment && emitted.length == expected.length;
    for (int i = 0; ok && i < expected.length; i++) {
      ok = expected[i].matches(emitted[i]);
    }
    if (!ok) {
      print('$_prefix check $check FAILED: expected treatment=$treatment '
          'exposures=$expected, got treatment=$actual exposures='
          '${emitted.map((e) => e.toMap()).toList()}');
    }
    return ok;
  }

  try {
    final served = expectServed('agreement', 'sb_agree', 3,
            [const _E(521, 'sb_agree', 'unit_serving', 3)]) &&
        expectServed('agreement repeat', 'sb_agree', 3) &&
        expectServed('declared order', 'sb_declared_order', 3,
            [const _E(522, 'sb_declared_order', 'unit_user', 3)]) &&
        expectServed('disagreement', 'sb_disagree', 0) &&
        expectServed('whole number', 'sb_whole_number', 6,
            [const _E(524, 'sb_whole_number', 'unit_serving', 6)]) &&
        expectServed('value filtered out', 'sb_value_filtered', 0) &&
        expectServed('empty allow-list admits all', 'sb_allow_empty', 3,
            [const _E(528, 'sb_allow_empty', 'unit_serving', 3)]) &&
        expectServed('Phase 1E fields only', 'sb_phase1e_only', 0);
    if (!served) {
      return false;
    }
    final baseline = await _checkPayloadShape(context, log, publisher);
    return baseline != null &&
        await _checkUnitExperimentInert(context, log, publisher, baseline);
  } finally {
    await context.close();
  }
}

Future<PublishEvent?> _publishOne(
    Context context, _Publisher publisher) async {
  final int before = publisher.events.length;
  await context.publish();
  return publisher.events.length == before + 1
      ? publisher.events[before]
      : null;
}

Map<String, dynamic> _serialized(PublishEvent event) =>
    jsonDecode(utf8.decode(DefaultContextEventSerializer().serialize(event)!))
        as Map<String, dynamic>;

String? _shapeMismatch(dynamic baseline, dynamic actual, String path) {
  if ((baseline is Map) != (actual is Map) ||
      (baseline is List) != (actual is List)) {
    return '$path: baseline is ${baseline.runtimeType}, got ${actual.runtimeType}';
  }
  if (baseline is Map) {
    final a = (baseline.keys.map((k) => '$k').toList()..sort()).join(',');
    final b = ((actual as Map).keys.map((k) => '$k').toList()..sort()).join(',');
    if (a != b) {
      return '$path: baseline fields [$a], got [$b]';
    }
    for (final key in baseline.keys) {
      final m = _shapeMismatch(baseline[key], actual[key], '$path.$key');
      if (m != null) {
        return m;
      }
    }
  } else if (baseline is List) {
    if (baseline.length != (actual as List).length) {
      return '$path: baseline length ${baseline.length}, got ${actual.length}';
    }
    for (int i = 0; i < baseline.length; i++) {
      final m = _shapeMismatch(baseline[i], actual[i], '$path[$i]');
      if (m != null) {
        return m;
      }
    }
  }
  return null;
}

String? _serializedMismatch(Map<String, dynamic> event, _E expected) {
  if (event['hashed'] != true) {
    return 'publish.hashed is ${event['hashed']}';
  }
  final exposures = event['exposures'];
  if (exposures is! List || exposures.length != 1) {
    return 'publish.exposures is $exposures';
  }
  final e = Exposure(
      id: exposures[0]['id'],
      name: exposures[0]['name'],
      unit: exposures[0]['unit'],
      variant: exposures[0]['variant'],
      exposedAt: exposures[0]['exposedAt'],
      assigned: exposures[0]['assigned'],
      eligible: exposures[0]['eligible'],
      overridden: exposures[0]['overridden'],
      fullOn: exposures[0]['fullOn'],
      custom: exposures[0]['custom'],
      audienceMismatch: exposures[0]['audienceMismatch'],
      ruleOverride: exposures[0]['ruleOverride']);
  if (!expected.matches(e)) {
    return 'publish.exposures[0] is ${exposures[0]}, expected $expected';
  }
  for (final unit in event['units'] as List) {
    if (unit['type'] == 'unit_serving' && unit['uid'] == _servingUnitHash) {
      return null;
    }
  }
  return 'publish.units lacks the hashed serving unit: ${event['units']}';
}

// A unit baseline and a switchback on the same unit, each published on its
// own, have the same fields at every depth. Returns the baseline event, or
// null when a check fails.
Future<PublishEvent?> _checkPayloadShape(
    Context context, _ExposureLog log, _Publisher publisher) async {
  await context.publish();

  final int before = log.exposures.length;
  final int baselineTreatment = context.getTreatment('sb_unit_baseline');
  final baseline = await _publishOne(context, publisher);
  final int switchbackTreatment = context.getTreatment('sb_payload');
  final switchback = await _publishOne(context, publisher);
  if (baselineTreatment != 1 || switchbackTreatment != 1) {
    print('$_prefix check payload shape FAILED: treatments '
        '$baselineTreatment and $switchbackTreatment, expected 1 and 1');
    return null;
  }
  final emitted = log.exposures.sublist(before);
  if (emitted.length != 2 || baseline == null || switchback == null) {
    print('$_prefix check payload shape FAILED: expected 2 exposures and 2 '
        'publish events, got ${emitted.length}');
    return null;
  }
  final exposureMismatch =
      _shapeMismatch(emitted[0].toMap(), emitted[1].toMap(), 'exposure');
  if (exposureMismatch != null) {
    print('$_prefix check payload shape FAILED: $exposureMismatch');
    return null;
  }
  final baselineJson = _serialized(baseline);
  final switchbackJson = _serialized(switchback);
  final mismatch = _shapeMismatch(baselineJson, switchbackJson, 'publish') ??
      _serializedMismatch(
          baselineJson, const _E(514, 'sb_unit_baseline', 'unit_serving', 1)) ??
      // assignSwitchback("market_1", 0, 1, 3) = 1.
      _serializedMismatch(
          switchbackJson, const _E(513, 'sb_payload', 'unit_serving', 1));
  if (mismatch != null) {
    print('$_prefix check publish payload FAILED: $mismatch');
    return null;
  }
  return baseline;
}

// A unit experiment carrying every R33 field (randomizationType "unit") is
// served, exposed and published exactly like the plain baseline: the
// serialized publish bytes are identical once id, name and exposedAt are
// aligned.
Future<bool> _checkUnitExperimentInert(Context context, _ExposureLog log,
    _Publisher publisher, PublishEvent baseline) async {
  final int before = log.exposures.length;
  if (context.getTreatment('sb_unit_with_fields') != 1) {
    print('$_prefix check unit inertness FAILED: sb_unit_with_fields did not '
        'serve 1');
    return false;
  }
  final event = await _publishOne(context, publisher);
  if (event == null ||
      log.exposures.length != before + 1 ||
      !const _E(527, 'sb_unit_with_fields', 'unit_serving', 1)
          .matches(log.exposures[before])) {
    print('$_prefix check unit inertness FAILED: exposures '
        '${log.exposures.sublist(before).map((e) => e.toMap()).toList()}');
    return false;
  }
  String normalized(PublishEvent e) {
    final json = _serialized(e)..remove('publishedAt');
    final exposure = (json['exposures'] as List)[0] as Map<String, dynamic>;
    exposure['id'] = 0;
    exposure['name'] = '';
    exposure['exposedAt'] = 0;
    return jsonEncode(json);
  }

  if (normalized(baseline) != normalized(event)) {
    print('$_prefix check unit inertness FAILED: publish ${normalized(event)} '
        'differs from ${normalized(baseline)}');
    return false;
  }
  return true;
}
