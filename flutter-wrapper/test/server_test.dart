import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:absmartly_sdk/absmartly_sdk.dart';

import '../server.dart' as server;
import '../lib/widget_test_queue.dart';

void main() {
  LiveTestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() async {
    await server.startServer();
  });

  // Single long-lived widget-test driver. It renders each request through the
  // REAL Treatment / VariableValue Flutter widgets (so widget behaviour is
  // genuinely exercised), but is optimized for throughput:
  //   * event-driven: awaits the queue's notifier instead of a 10ms poll loop,
  //     so a request is handled the instant it arrives;
  //   * minimal variant map: only the assigned variant (+ control) are built,
  //     not 0..100 widgets;
  //   * pump() with a bounded ready-check instead of pumpAndSettle(), which
  //     otherwise blocks on the Treatment widget's 30s timeout timer.
  testWidgets('Widget Test Server', (WidgetTester tester) async {
    final queue = WidgetTestQueue.instance;
    var isRunning = true;

    void stop() {
      isRunning = false;
      queue.shutdown();
    }

    ProcessSignal.sigterm.watch().listen((_) => stop());
    ProcessSignal.sigint.watch().listen((_) => stop());

    while (isRunning) {
      // Race-free, non-polling wait for the next request (returns null on
      // shutdown).
      final request = await queue.nextRequest();
      if (request == null) break;

      try {
        final dynamic result = request.type == WidgetTestRequestType.treatment
            ? await _processTreatmentRequest(tester, request)
            : await _processVariableValueRequest(tester, request);
        if (!request.completer.isCompleted) {
          request.completer.complete(result);
        }
      } catch (e, stackTrace) {
        print('Error processing request: $e');
        print(stackTrace);
        if (!request.completer.isCompleted) {
          request.completer.completeError(e);
        }
      }
    }
  }, timeout: const Timeout(Duration(days: 365)));
}

/// Pump until [isDone] returns true, or a bounded number of frames elapse.
/// Avoids pumpAndSettle() (which waits for the Treatment widget's long timeout
/// timer to fire). The widget resolves synchronously once the context is ready,
/// so this returns after one or two frames in practice.
Future<void> _pumpUntil(
  WidgetTester tester,
  bool Function() isDone, {
  int maxFrames = 60,
  Duration step = const Duration(milliseconds: 16),
}) async {
  for (var i = 0; i < maxFrames; i++) {
    await tester.pump(step);
    if (isDone()) return;
  }
}

Future<int> _processTreatmentRequest(
  WidgetTester tester,
  WidgetTestRequest request,
) async {
  // Resolve the assigned variant up front (non-exposing) so we only need to
  // build that variant plus the control in the map. The real Treatment widget
  // below independently resolves AND exposes; its build() indexes
  // variants[_variant], so the assigned key must be present (a missing key
  // silently falls back to variants[0], which would capture the wrong variant).
  final peeked = request.context.peekTreatment(request.experimentName);

  int? capturedVariant;
  Widget marker(int i) => Builder(
        builder: (_) {
          capturedVariant = i;
          return Text('Variant $i');
        },
      );

  final widget = MaterialApp(
    home: Scaffold(
      body: Treatment(
        key: UniqueKey(),
        name: request.experimentName,
        context: request.context,
        variants: {
          0: marker(0),
          if (peeked != 0) peeked: marker(peeked),
        },
      ),
    ),
  );

  await tester.pumpWidget(widget);
  await _pumpUntil(tester, () => capturedVariant != null);

  return capturedVariant ?? peeked;
}

Future<dynamic> _processVariableValueRequest(
  WidgetTester tester,
  WidgetTestRequest request,
) async {
  var captured = false;
  dynamic capturedValue;

  final widget = MaterialApp(
    home: Scaffold(
      body: VariableValue<dynamic>(
        key: UniqueKey(),
        name: request.variableKey!,
        defaultValue: request.defaultValue,
        context: request.context,
        builder: (value) {
          captured = true;
          capturedValue = value;
          return Text('Variable: $value');
        },
      ),
    ),
  );

  await tester.pumpWidget(widget);
  await _pumpUntil(tester, () => captured);

  return capturedValue;
}
