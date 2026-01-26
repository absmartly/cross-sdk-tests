import 'dart:async';

import 'package:absmartly_sdk/absmartly_sdk.dart';

enum WidgetTestRequestType {
  treatment,
  variableValue,
}

class WidgetTestRequest {
  WidgetTestRequest({
    required this.type,
    required this.experimentName,
    required this.context,
    this.variableKey,
    this.defaultValue,
  }) : completer = Completer<dynamic>();

  final WidgetTestRequestType type;
  final String experimentName;
  final Context context;
  final String? variableKey;
  final dynamic defaultValue;
  final Completer<dynamic> completer;
  final DateTime createdAt = DateTime.now();

  static const Duration defaultTimeout = Duration(seconds: 30);
}

class WidgetTestQueue {
  WidgetTestQueue._();
  static final WidgetTestQueue instance = WidgetTestQueue._();

  final List<WidgetTestRequest> _queue = [];
  final StreamController<void> _notifier = StreamController<void>.broadcast();
  bool _isShutdown = false;

  void enqueue(WidgetTestRequest request) {
    if (_isShutdown) {
      request.completer.completeError(Exception('Queue is shutdown'));
      return;
    }
    _queue.add(request);
    _notifier.add(null);
  }

  Stream<void> get notifications => _notifier.stream;

  WidgetTestRequest? dequeue() {
    if (_queue.isEmpty) return null;
    return _queue.removeAt(0);
  }

  bool get isEmpty => _queue.isEmpty;

  int get length => _queue.length;

  void shutdown() {
    _isShutdown = true;
    for (final request in _queue) {
      if (!request.completer.isCompleted) {
        request.completer.completeError(Exception('Queue shutdown'));
      }
    }
    _queue.clear();
    _notifier.close();
  }

  void reset() {
    _isShutdown = false;
    _queue.clear();
  }
}
