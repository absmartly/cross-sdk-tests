import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import '../server.dart' as server;
import '../lib/widget_test_queue.dart';
import 'widget_driver.dart';

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
  //   * small variant map: control plus the experiment's own variants;
  //   * no fallback: a widget that throws or renders nothing fails the request
  //     (see widget_driver.dart);
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
            ? await renderTreatment(
                tester, request.context, request.experimentName)
            : await renderVariableValue(tester, request.context,
                request.variableKey!, request.defaultValue);
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
