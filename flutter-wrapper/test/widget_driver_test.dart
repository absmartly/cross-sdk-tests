import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:absmartly_sdk/absmartly_sdk.dart';

import 'widget_driver.dart';

class _NoopPublisher implements ContextPublisher {
  @override
  Completer<void> publish(Context context, PublishEvent event) =>
      Completer<void>()..complete();
}

// Full-on variant 1, so the assignment does not depend on unit hashing.
Map<String, dynamic> _payload() => {
      'experiments': [
        {
          'id': 1,
          'name': 'exp_full_on',
          'iteration': 1,
          'unitType': 'session_id',
          'seedHi': 3603515,
          'seedLo': 233373850,
          'split': [0.5, 0.5],
          'trafficSeedHi': 449867249,
          'trafficSeedLo': 455443629,
          'trafficSplit': [0.0, 1.0],
          'fullOnVariant': 1,
          'applications': [
            {'name': 'website'}
          ],
          'variants': [
            {'name': 'A', 'config': null},
            {'name': 'B', 'config': '{"banner.color":"red"}'},
          ],
          'audience': null,
          'audienceStrict': false,
        }
      ],
    };

Future<Context> _readyContext(WidgetTester tester) async {
  final client = Client.create(ClientConfig.create()
      .setEndpoint('http://localhost:1')
      .setAPIKey('test')
      .setApplication('website')
      .setEnvironment('test'));
  final sdk = ABSmartly(ABSmartlyConfig.create()
      .setClient(client)
      .setContextPublisher(_NoopPublisher()));
  final config = ContextConfig()
    ..setUnits({'session_id': 'driver-test'})
    ..setPublishDelay(999999999)
    ..setRefreshInterval(0);
  final context =
      sdk.createContextWith(config, ContextData.fromMap(_payload()));
  await tester.runAsync(() => context.waitUntilReady());
  expect(context.isReady(), isTrue);
  return context;
}

// Close the context so the SDK's publish timer is cancelled before the
// widget binding checks for pending timers.
Future<void> _close(WidgetTester tester, Context context) async {
  await tester.runAsync(() => context.close());
}

void main() {
  testWidgets('renders the variant the SDK assigned and exposes it',
      (tester) async {
    final context = await _readyContext(tester);

    final variant = await renderTreatment(tester, context, 'exp_full_on');

    expect(variant, 1);
    expect(context.getPendingCount(), 1);
    await _close(tester, context);
  });

  testWidgets(
      'fails when the assigned variant has no entry in the variants map',
      (tester) async {
    final context = await _readyContext(tester);

    // Only control is mapped. The widget falls back to variants[0]; the
    // driver must report that rather than return 0.
    Object? error;
    try {
      await renderTreatment(tester, context, 'exp_full_on', variantKeys: {0});
    } catch (e) {
      error = e;
    }
    expect(
        error,
        isA<StateError>().having((e) => e.message, 'message',
            contains('rendered variant 0 but the SDK assigned variant 1')));
    await _close(tester, context);
  });

  testWidgets('maps an override to a variant the payload does not define',
      (tester) async {
    final context = await _readyContext(tester);
    context.setOverride('exp_full_on', 5);

    expect(await renderTreatment(tester, context, 'exp_full_on'), 5);
    await _close(tester, context);
  });

  testWidgets('renders the variable value the SDK resolved', (tester) async {
    final context = await _readyContext(tester);

    expect(
        await renderVariableValue(tester, context, 'banner.color', 'blue'),
        'red');
    expect(
        await renderVariableValue(tester, context, 'missing.key', 'blue'),
        'blue');
    await _close(tester, context);
  });
}
