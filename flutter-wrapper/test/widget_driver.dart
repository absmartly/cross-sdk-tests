import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:absmartly_sdk/absmartly_sdk.dart';

/// Renders requests through the REAL Treatment / VariableValue Flutter widgets.
///
/// The result is whatever the widget actually rendered. If the widget throws,
/// or never renders a marker, the request fails: there is no fallback to a
/// peeked or default value, which would hide a broken widget.

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
    _rethrowWidgetException(tester);
    if (isDone()) return;
  }
}

/// flutter_test catches exceptions thrown while building and parks them on the
/// binding. Take them and rethrow so the request fails instead of the driver
/// carrying on with whatever was (or was not) rendered.
void _rethrowWidgetException(WidgetTester tester) {
  final error = tester.takeException();
  if (error != null) {
    throw StateError('Widget threw while rendering: $error');
  }
}

bool _isUsable(Context context) =>
    context.isReady() &&
    !context.isFailed() &&
    !context.isClosed() &&
    !context.isClosing();

/// The variant keys the Treatment widget is given, as an app would map them:
/// control, every variant the experiment defines, and the assigned variant
/// (an override can name a variant the payload does not define). The
/// rendered variant is still checked against the SDK after the render.
Set<int> treatmentVariantKeys(Context context, String experimentName) {
  final keys = <int>{0};
  if (!_isUsable(context)) return keys;
  keys.add(context.peekTreatment(experimentName));
  for (final experiment in context.getData().experiments) {
    if (experiment.name == experimentName) {
      for (var i = 0; i < experiment.variants.length; i++) {
        keys.add(i);
      }
    }
  }
  return keys;
}

/// Renders [experimentName] through the Treatment widget and returns the
/// variant whose marker the widget built.
///
/// [variantKeys] overrides the keys in the variants map (tests use it to
/// reproduce a missing key). When the context is ready, the rendered variant is
/// checked against a non-exposing peek: a mismatch means the widget fell back
/// to another entry and the request fails.
Future<int> renderTreatment(
  WidgetTester tester,
  Context context,
  String experimentName, {
  Set<int>? variantKeys,
}) async {
  final keys = variantKeys ?? treatmentVariantKeys(context, experimentName);

  int? capturedVariant;
  Widget marker(int i) => Builder(
        builder: (_) {
          capturedVariant = i;
          return Text('Variant $i');
        },
      );

  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: Treatment(
          key: UniqueKey(),
          name: experimentName,
          context: context,
          variants: {for (final i in keys) i: marker(i)},
        ),
      ),
    ),
  );
  _rethrowWidgetException(tester);
  await _pumpUntil(tester, () => capturedVariant != null);

  final rendered = capturedVariant;
  if (rendered == null) {
    throw StateError(
        'Treatment widget for "$experimentName" rendered no variant');
  }

  if (_isUsable(context)) {
    // Peek after the render so the widget's own getTreatment() call is the one
    // that queues the exposure.
    final assigned = context.peekTreatment(experimentName);
    if (assigned != rendered) {
      throw StateError('Treatment widget for "$experimentName" rendered '
          'variant $rendered but the SDK assigned variant $assigned');
    }
  }

  return rendered;
}

/// Renders [variableKey] through the VariableValue widget and returns the
/// value its builder received.
Future<dynamic> renderVariableValue(
  WidgetTester tester,
  Context context,
  String variableKey,
  dynamic defaultValue,
) async {
  var captured = false;
  dynamic capturedValue;

  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: VariableValue<dynamic>(
          key: UniqueKey(),
          name: variableKey,
          defaultValue: defaultValue,
          context: context,
          builder: (value) {
            captured = true;
            capturedValue = value;
            return Text('Variable: $value');
          },
        ),
      ),
    ),
  );
  _rethrowWidgetException(tester);
  await _pumpUntil(tester, () => captured);

  if (!captured) {
    throw StateError('VariableValue widget for "$variableKey" never built');
  }

  if (_isUsable(context)) {
    // Peek after the render so the widget's own read is the one that queues
    // the exposure.
    final expected = context.peekVariableValue(variableKey, defaultValue);
    if (jsonEncode(expected) != jsonEncode(capturedValue)) {
      throw StateError('VariableValue widget for "$variableKey" rendered '
          '$capturedValue but the SDK returned $expected');
    }
  }
  return capturedValue;
}
