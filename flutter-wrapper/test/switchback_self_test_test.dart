// Run with `dart run test_switchback.dart` (Flutter: `flutter test test/switchback_self_test_test.dart`).
import 'package:absmartly_sdk/absmartly_sdk.dart';
import 'package:flutter_test/flutter_test.dart';
import '../lib/switchback_self_test.dart' as probe;

void require(bool value, String label) {
  if (!value) throw StateError(label);
}

Exposure exposure(int id, String name) => Exposure(
    id: id, name: name, unit: 'unit_serving', variant: 1, exposedAt: 123,
    assigned: true, eligible: true, overridden: false, fullOn: false,
    custom: false, audienceMismatch: false, ruleOverride: false);

Future<void> checkProbe() async {
  for (final text in ['+1', '+9223372036854775807', '-9223372036854775808']) {
    final plain = text.startsWith('+') ? text.substring(1) : text;
    require(probe.switchbackAssign('market_1', text, text, 3) ==
        probe.switchbackAssign('market_1', plain, plain, 3), 'assign $text');
    require(probe.switchbackPermutation('market_1', text, text, 3).join(',') ==
        probe.switchbackPermutation('market_1', plain, plain, 3).join(','), 'permutation $text');
  }
  for (final text in ['', '+', '-', '++1', '+-1', '-+1', '--1', ' 1', '1.0',
      '+9223372036854775808', '-9223372036854775809']) {
    for (final permutation in [false, true]) {
      bool rejected = false;
      try {
        if (permutation) {
          probe.switchbackPermutation('market_1', text, '0', 3);
        } else {
          probe.switchbackAssign('market_1', '0', text, 3);
        }
      } on ArgumentError {
        rejected = true;
      }
      require(rejected, 'accepted invalid decimal $text');
    }
  }
  final baseline = exposure(514, 'sb_unit_baseline');
  final switchback = exposure(513, 'sb_payload');
  require(probe.payloadExposuresMatch([baseline, switchback]), 'valid exposures');
  for (final e in [baseline, switchback]) {
    e.variant = 2;
    require(!probe.payloadExposuresMatch([baseline, switchback]), 'wrong callback variant');
    e.variant = 1;
    e.audienceMismatch = true;
    require(!probe.payloadExposuresMatch([baseline, switchback]), 'wrong callback flag');
    e.audienceMismatch = false;
  }
  require(!probe.payloadExposuresMatch([baseline]), 'missing callback');
  require(await probe.runSwitchback(), 'linked SDK self-test');
  // 3.0 keys no cluster while 3 serves variant 5; a reader that keys 3.0
  // like 3, or one that keys no number at all, fails.
  require(await probe.checkIntegralFloat({'f': 3.0, 'i': 3}), 'integral float check');
  require(!await probe.checkIntegralFloat({'f': 3, 'i': 3}), 'float keyed like the integer');
  require(!await probe.checkIntegralFloat({'f': 3.0, 'i': 3.0}), 'integer as a float');
  require(await probe.runIntegralFloat(), 'linked SDK integral-float self-test');
  // The probe itself passes on this SDK (above), so only the gate can make it
  // false: a failing switchback battery must withhold integralFloatAttributes.
  int batteryRuns = 0;
  require(!await probe.runIntegralFloatAfter(() async {
    batteryRuns++;
    return false;
  }), 'integral float advertised despite a failing switchback battery');
  require(batteryRuns == 1, 'switchback battery not consulted');
}

void main() {
  test('switchback parsing and callback regressions', checkProbe);
}
