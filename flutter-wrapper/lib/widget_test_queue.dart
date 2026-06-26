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
  bool _isShutdown = false;

  // A pending waiter parked in [nextRequest]. Completed the instant an item is
  // enqueued so the driver never busy-polls and never misses a wakeup.
  Completer<void>? _waiter;

  void enqueue(WidgetTestRequest request) {
    if (_isShutdown) {
      request.completer.completeError(Exception('Queue is shutdown'));
      return;
    }
    _queue.add(request);
    final waiter = _waiter;
    if (waiter != null && !waiter.isCompleted) {
      _waiter = null;
      waiter.complete();
    }
  }

  WidgetTestRequest? dequeue() {
    if (_queue.isEmpty) return null;
    return _queue.removeAt(0);
  }

  /// Returns the next request, waiting (without polling) if the queue is empty.
  /// Race-free: if an item is already queued it returns immediately; otherwise
  /// it parks a single waiter that [enqueue] completes. Returns null on shutdown.
  Future<WidgetTestRequest?> nextRequest() async {
    while (!_isShutdown) {
      final request = dequeue();
      if (request != null) return request;
      final waiter = _waiter ??= Completer<void>();
      await waiter.future;
    }
    return null;
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
    final waiter = _waiter;
    if (waiter != null && !waiter.isCompleted) {
      _waiter = null;
      waiter.complete();
    }
  }

  void reset() {
    _isShutdown = false;
    _queue.clear();
    _waiter = null;
  }
}
