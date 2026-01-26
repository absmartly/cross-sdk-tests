import 'dart:async';
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

  testWidgets('Widget Test Server', (WidgetTester tester) async {
    final queue = WidgetTestQueue.instance;
    var isRunning = true;

    ProcessSignal.sigterm.watch().listen((_) {
      isRunning = false;
      queue.shutdown();
    });

    ProcessSignal.sigint.watch().listen((_) {
      isRunning = false;
      queue.shutdown();
    });

    while (isRunning) {
      final request = queue.dequeue();

      if (request != null) {
        try {
          if (request.type == WidgetTestRequestType.treatment) {
            final result = await _processTreatmentRequest(tester, request);
            if (!request.completer.isCompleted) {
              request.completer.complete(result);
            }
          } else if (request.type == WidgetTestRequestType.variableValue) {
            final result = await _processVariableValueRequest(tester, request);
            if (!request.completer.isCompleted) {
              request.completer.complete(result);
            }
          }
        } catch (e, stackTrace) {
          print('Error processing request: $e');
          print(stackTrace);
          if (!request.completer.isCompleted) {
            request.completer.completeError(e);
          }
        }
      } else {
        await Future.delayed(const Duration(milliseconds: 10));
      }
    }
  }, timeout: const Timeout(Duration(days: 365)));
}

Future<int> _processTreatmentRequest(
  WidgetTester tester,
  WidgetTestRequest request,
) async {
  int capturedVariant = 0;

  final widget = MaterialApp(
    home: Scaffold(
      body: Treatment(
        key: UniqueKey(),
        name: request.experimentName,
        context: request.context,
        variants: {
          for (int i = 0; i <= 100; i++)
            i: Builder(
              builder: (context) {
                capturedVariant = i;
                return Text('Variant $i');
              },
            ),
        },
      ),
    ),
  );

  await tester.pumpWidget(widget);
  await tester.pumpAndSettle();

  return capturedVariant;
}

Future<dynamic> _processVariableValueRequest(
  WidgetTester tester,
  WidgetTestRequest request,
) async {
  dynamic capturedValue;

  final widget = MaterialApp(
    home: Scaffold(
      body: VariableValue<dynamic>(
        key: UniqueKey(),
        name: request.variableKey!,
        defaultValue: request.defaultValue,
        context: request.context,
        builder: (value) {
          capturedValue = value;
          return Text('Variable: $value');
        },
      ),
    ),
  );

  await tester.pumpWidget(widget);
  await tester.pumpAndSettle();

  return capturedValue;
}
