import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:shelf/shelf.dart' as shelf;
import 'package:shelf/shelf_io.dart' as shelf_io;
import 'package:shelf_router/shelf_router.dart' as shelf_router;
import 'package:absmartly_sdk/absmartly_sdk.dart';

import 'lib/widget_test_queue.dart';

class DummyHTTPResponse implements Response {
  final int statusCode;
  final String? statusMessage;
  final List<int>? content;

  DummyHTTPResponse(this.statusCode, this.statusMessage, this.content);

  @override
  int? getStatusCode() => statusCode;

  @override
  String? getStatusMessage() => statusMessage;

  @override
  List<int>? getContent() => content;

  @override
  String? getContentType() => 'application/json';
}

class DummyHTTPClient implements HTTPClient {
  @override
  Future<Response> get(String url, Map<String, String>? query, Map<String, String>? headers) {
    return Future.value(DummyHTTPResponse(200, 'OK', []));
  }

  @override
  Future<Response> post(String url, Map<String, String>? query, Map<String, String>? headers, List<int>? body) {
    return Future.value(DummyHTTPResponse(200, 'OK', []));
  }

  @override
  Future<Response> put(String url, Map<String, String>? query, Map<String, String>? headers, List<int>? body) {
    return Future.value(DummyHTTPResponse(200, 'OK', []));
  }

  @override
  void close() {}
}

class EventCollector implements ContextEventLogger {
  final List<Map<String, dynamic>> events = [];

  @override
  void handleEvent(Context context, EventType type, dynamic data) {
    String eventType;
    switch (type) {
      case EventType.error:
        eventType = 'error';
        break;
      case EventType.ready:
        eventType = 'ready';
        break;
      case EventType.refresh:
        eventType = 'refresh';
        break;
      case EventType.publish:
        eventType = 'publish';
        break;
      case EventType.exposure:
        eventType = 'exposure';
        break;
      case EventType.goal:
        eventType = 'goal';
        break;
      case EventType.close:
        eventType = 'finalize';
        break;
    }

    events.add({
      'type': eventType,
      'data': _serializeEventData(data),
      'timestamp': DateTime.now().millisecondsSinceEpoch,
    });
  }

  dynamic _serializeEventData(dynamic data) {
    if (data == null) return null;

    if (data is Map) {
      return Map.fromEntries(
        data.entries.map((e) => MapEntry(e.key.toString(), _serializeEventData(e.value)))
      );
    }

    if (data is List) {
      return data.map((item) => _serializeEventData(item)).toList();
    }

    if (data.runtimeType.toString().startsWith('_')) {
      return null;
    }

    try {
      final method = data.runtimeType.toString().contains('Map') ? null :
                     (data as dynamic).toMap;
      if (method != null) {
        return _serializeEventData(method());
      }
    } catch (_) {}

    return data;
  }

  List<Map<String, dynamic>> getNewEvents(int since) {
    return events.sublist(since);
  }
}

class CustomEventHandler implements ContextEventHandler {
  final EventCollector eventCollector;

  CustomEventHandler(this.eventCollector);

  @override
  Completer<void> publish(Context context, PublishEvent event) {
    final completer = Completer<void>();
    completer.complete();
    return completer;
  }
}

class CustomDataProvider implements ContextDataProvider {
  ContextData? _data;
  ContextData? _nextData;

  void setData(ContextData data) {
    _data = data;
  }

  void setNextData(ContextData data) {
    _nextData = data;
  }

  @override
  Completer<ContextData> getContextData() {
    final completer = Completer<ContextData>();
    if (_nextData != null) {
      final data = _nextData!;
      _nextData = null;
      completer.complete(data);
    } else if (_data != null) {
      completer.complete(_data!);
    } else {
      completer.completeError(Exception('No context data provided'));
    }
    return completer;
  }
}

class CustomVariableParser implements VariableParser {
  @override
  Map<String, dynamic>? parse(
    Context context,
    String experimentName,
    String variantName,
    String? config,
  ) {
    if (config == null || config.isEmpty) {
      return {};
    }

    try {
      final decoded = jsonDecode(config);
      if (decoded is Map<String, dynamic>) {
        return decoded;
      }
      return {'__raw_value': decoded};
    } catch (e) {
      return {};
    }
  }
}

class ContextStore {
  final Context context;
  final EventCollector eventCollector;
  final CustomDataProvider dataProvider;
  final Map<String, dynamic> rawData;

  ContextStore(this.context, this.eventCollector, this.dataProvider, this.rawData);
}

final Map<String, ContextStore> contexts = {};
final Map<String, ContextData> payloadStore = {};

Map<String, dynamic> _normalizeContextData(Map<String, dynamic> data) {
  final result = Map<String, dynamic>.from(data);

  if (result.containsKey('experiments')) {
    final experiments = result['experiments'] as List;
    result['experiments'] = experiments.map((exp) {
      final expMap = Map<String, dynamic>.from(exp as Map);

      if (expMap.containsKey('split') && expMap['split'] != null) {
        final split = expMap['split'] as List;
        expMap['split'] = List<double>.from(split.map((v) => (v as num).toDouble()));
      }

      if (expMap.containsKey('trafficSplit') && expMap['trafficSplit'] != null) {
        final trafficSplit = expMap['trafficSplit'] as List;
        expMap['trafficSplit'] = List<double>.from(trafficSplit.map((v) => (v as num).toDouble()));
      }

      return expMap;
    }).toList();
  }

  return result;
}

Future<void> startServer() async {

  final router = shelf_router.Router();

  router.get('/health', (shelf.Request request) {
    return shelf.Response.ok(
      jsonEncode({
        'status': 'healthy',
        'sdk': 'flutter',
        'version': '1.0.0',
      }),
      headers: {'Content-Type': 'application/json'},
    );
  });

  router.get('/capabilities', (shelf.Request request) {
    return shelf.Response.ok(
      jsonEncode({
        'asyncContext': false,
        'attrsSeq': false,
        'isWrapper': true,
        'wrapsSDK': 'dart',
        'passThroughOperations': [
          'peek', 'track', 'attribute', 'override', 'customAssignment',
          'pending', 'isFinalized', 'publish', 'finalize', 'setUnit', 'getUnit',
          'getAttribute', 'peekVariableValue', 'customFieldValue',
          'variableKeys', 'customFieldKeys', 'customFieldValueType', 'setOverride',
          'setCustomAssignment', 'refresh'
        ],
      }),
      headers: {'Content-Type': 'application/json'},
    );
  });

  router.put('/context_payload', (shelf.Request request) async {
    try {
      final body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;
      final data = body['data'] as Map<String, dynamic>;

      final payloadId = 'payload-${DateTime.now().millisecondsSinceEpoch}-${DateTime.now().microsecond}';

      final normalizedData = _normalizeContextData(data);
      final contextData = ContextData.fromMap(normalizedData);
      payloadStore[payloadId] = contextData;

      final url = 'http://flutter-sdk:3000/context_payload/$payloadId';

      return shelf.Response.ok(
        jsonEncode({
          'payloadUrl': url,
          'payloadId': payloadId,
        }),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      return shelf.Response.internalServerError(
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.get('/context_payload/<payloadId>', (shelf.Request request, String payloadId) async {
    try {
      final throttle = int.tryParse(request.url.queryParameters['throttle'] ?? '0') ?? 0;

      if (throttle > 0) {
        await Future.delayed(Duration(milliseconds: throttle));
      }

      final data = payloadStore[payloadId] ?? ContextData();

      return shelf.Response.ok(
        jsonEncode(data.toMap()),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      return shelf.Response.internalServerError(
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context', (shelf.Request request) async {
    try {
      final body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;
      final data = body['data'] as Map<String, dynamic>?;
      final endpoint = body['endpoint'] as String?;
      final units = body['units'] as Map<String, dynamic>;
      final options = body['options'] as Map<String, dynamic>? ?? {};

      final contextId = 'ctx-${DateTime.now().millisecondsSinceEpoch}-${DateTime.now().microsecond}';

      final eventCollector = EventCollector();
      final eventHandler = CustomEventHandler(eventCollector);
      final dataProvider = CustomDataProvider();
      final variableParser = CustomVariableParser();

      if (data != null) {
        final normalizedData = _normalizeContextData(data);
        final contextData = ContextData.fromMap(normalizedData);
        dataProvider.setData(contextData);
      }

      final httpClient = DummyHTTPClient();
      final clientConfig = ClientConfig();
      clientConfig.setEndpoint(endpoint ?? 'http://dummy');
      clientConfig.setAPIKey('dummy');
      clientConfig.setApplication('test');
      clientConfig.setEnvironment('test');
      final client = Client.create(clientConfig, httpClient: httpClient);

      final config = ABSmartlyConfig();
      config.setClient(client);
      config.setContextDataProvider(dataProvider);
      config.setContextEventHandler(eventHandler);
      config.setContextEventLogger(eventCollector);
      config.setVariableParser(variableParser);

      final sdk = ABSmartly(config);

      final unitsMap = Map<String, String>.from(
        units.map((key, value) => MapEntry(key.toString(), value.toString()))
      );

      final contextConfig = ContextConfig();
      contextConfig.setUnits(unitsMap);

      final publishDelay = options['publishDelay'] as int? ?? -1;
      contextConfig.setPublishDelay(publishDelay < 0 ? 999999999 : publishDelay);
      contextConfig.setRefreshInterval(options['refreshPeriod'] as int? ?? 0);

      final context = sdk.createContext(contextConfig);
      await context.waitUntilReady();

      contexts[contextId] = ContextStore(context, eventCollector, dataProvider, data ?? {});

      return shelf.Response.ok(
        jsonEncode({
          'result': {
            'contextId': contextId,
            'ready': context.isReady(),
            'failed': context.isFailed(),
            'finalized': context.isClosed(),
          },
          'events': eventCollector.events,
        }),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e, stackTrace) {
      print('Error creating context: $e');
      print(stackTrace);
      return shelf.Response.internalServerError(
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context/<contextId>/setUnit', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    final body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;
    final unitType = body['unitType'] as String;

    try {
      final eventsBefore = ctxData.eventCollector.events.length;

      ctxData.context.setUnit(unitType, body['uid'].toString());

      final newEvents = ctxData.eventCollector.getNewEvents(eventsBefore);
      return shelf.Response.ok(
        jsonEncode({'result': null, 'events': newEvents}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      var errorMsg = e.toString();
      if (errorMsg.contains('already set')) {
        errorMsg = "Unit '$unitType' UID already set.";
      }
      return shelf.Response(400,
        body: jsonEncode({'error': errorMsg}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context/<contextId>/getUnit', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    try {
      final body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;
      final eventsBefore = ctxData.eventCollector.events.length;

      final unitStr = ctxData.context.getUnit(body['unitType'] as String);

      dynamic result = unitStr;
      if (unitStr != null) {
        final numValue = num.tryParse(unitStr);
        if (numValue != null) {
          result = numValue;
        }
      }

      final newEvents = ctxData.eventCollector.getNewEvents(eventsBefore);
      return shelf.Response.ok(
        jsonEncode({'result': result, 'events': newEvents}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      return shelf.Response(400,
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context/<contextId>/attribute', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    try {
      final body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;
      final eventsBefore = ctxData.eventCollector.events.length;

      ctxData.context.setAttribute(body['name'] as String, body['value']);

      final newEvents = ctxData.eventCollector.getNewEvents(eventsBefore);
      return shelf.Response.ok(
        jsonEncode({'result': null, 'events': newEvents}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      return shelf.Response(400,
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context/<contextId>/getAttribute', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    try {
      final body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;
      final eventsBefore = ctxData.eventCollector.events.length;

      final result = ctxData.context.getAttribute(body['name'] as String);

      final newEvents = ctxData.eventCollector.getNewEvents(eventsBefore);
      return shelf.Response.ok(
        jsonEncode({'result': result, 'events': newEvents}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      return shelf.Response(400,
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context/<contextId>/treatment', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    try {
      final body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;
      final eventsBefore = ctxData.eventCollector.events.length;

      final widgetRequest = WidgetTestRequest(
        type: WidgetTestRequestType.treatment,
        experimentName: body['experimentName'] as String,
        context: ctxData.context,
      );
      WidgetTestQueue.instance.enqueue(widgetRequest);

      final variant = await widgetRequest.completer.future
          .timeout(WidgetTestRequest.defaultTimeout);

      final newEvents = ctxData.eventCollector.getNewEvents(eventsBefore);
      return shelf.Response.ok(
        jsonEncode({'result': variant, 'events': newEvents}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      return shelf.Response(400,
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context/<contextId>/peek', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    try {
      final body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;
      final eventsBefore = ctxData.eventCollector.events.length;

      final variant = ctxData.context.peekTreatment(body['experimentName'] as String);

      final newEvents = ctxData.eventCollector.getNewEvents(eventsBefore);
      return shelf.Response.ok(
        jsonEncode({'result': variant, 'events': newEvents}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      return shelf.Response(400,
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context/<contextId>/variableValue', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    try {
      final body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;
      final eventsBefore = ctxData.eventCollector.events.length;

      final widgetRequest = WidgetTestRequest(
        type: WidgetTestRequestType.variableValue,
        experimentName: '',
        context: ctxData.context,
        variableKey: body['key'] as String,
        defaultValue: body['defaultValue'],
      );
      WidgetTestQueue.instance.enqueue(widgetRequest);

      final value = await widgetRequest.completer.future
          .timeout(WidgetTestRequest.defaultTimeout);

      final newEvents = ctxData.eventCollector.getNewEvents(eventsBefore);
      return shelf.Response.ok(
        jsonEncode({'result': value, 'events': newEvents}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      return shelf.Response(400,
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context/<contextId>/peekVariableValue', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    try {
      final body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;
      final eventsBefore = ctxData.eventCollector.events.length;

      final value = ctxData.context.peekVariableValue(
        body['key'] as String,
        body['defaultValue'],
      );

      final newEvents = ctxData.eventCollector.getNewEvents(eventsBefore);
      return shelf.Response.ok(
        jsonEncode({'result': value, 'events': newEvents}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      print('Error in peekVariableValue handler: $e');
      return shelf.Response(400,
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context/<contextId>/track', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    try {
      final body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;
      final goalName = body['goalName'] as String;
      final rawProperties = body['properties'];

      Map<String, dynamic>? properties;
      if (rawProperties != null) {
        if (rawProperties is! Map) {
          return shelf.Response(400,
            body: jsonEncode({'error': "Goal '$goalName' properties must be of type object."}),
            headers: {'Content-Type': 'application/json'},
          );
        }
        properties = Map<String, dynamic>.from(rawProperties);
      }

      final eventsBefore = ctxData.eventCollector.events.length;

      ctxData.context.track(goalName, properties);

      final newEvents = ctxData.eventCollector.getNewEvents(eventsBefore);
      return shelf.Response.ok(
        jsonEncode({'result': null, 'events': newEvents}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      return shelf.Response(400,
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context/<contextId>/override', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    try {
      final body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;

      ctxData.context.setOverride(
        body['experimentName'] as String,
        body['variant'] as int,
      );

      return shelf.Response.ok(
        jsonEncode({'result': null, 'events': []}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      return shelf.Response(400,
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context/<contextId>/customAssignment', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    try {
      final body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;

      ctxData.context.setCustomAssignment(
        body['experimentName'] as String,
        body['variant'] as int,
      );

      return shelf.Response.ok(
        jsonEncode({'result': null, 'events': []}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      return shelf.Response(400,
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context/<contextId>/customFieldValue', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    try {
      final body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;
      final experimentName = body['experimentName'] as String;
      final fieldName = body['fieldName'] as String;

      final experiments = ctxData.rawData['experiments'] as List<dynamic>?;
      dynamic result;

      if (experiments != null) {
        for (final exp in experiments) {
          if (exp['name'] == experimentName) {
            final customFieldValues = exp['customFieldValues'] as List<dynamic>?;
            if (customFieldValues != null) {
              for (final field in customFieldValues) {
                if (field['name'] == fieldName) {
                  final type = field['type'] as String?;
                  final value = field['value'];

                  switch (type) {
                    case 'string':
                    case 'text':
                      result = value;
                      break;
                    case 'number':
                      result = num.tryParse(value.toString());
                      break;
                    case 'boolean':
                      result = value.toString().toLowerCase() == 'true';
                      break;
                    case 'json':
                      try {
                        result = jsonDecode(value.toString());
                      } catch (_) {
                        result = value;
                      }
                      break;
                    default:
                      result = value;
                  }
                  break;
                }
              }
            }
            break;
          }
        }
      }

      return shelf.Response.ok(
        jsonEncode({'result': result, 'events': []}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      return shelf.Response(400,
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context/<contextId>/variableKeys', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    try {
      final eventsBefore = ctxData.eventCollector.events.length;

      final keys = ctxData.context.getVariableKeys();
      final newEvents = ctxData.eventCollector.getNewEvents(eventsBefore);

      return shelf.Response.ok(
        jsonEncode({'result': keys.keys.toList(), 'events': newEvents}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      return shelf.Response(400,
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context/<contextId>/customFieldKeys', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    try {
      final eventsBefore = ctxData.eventCollector.events.length;

      final keys = <String>{};
      final experiments = ctxData.rawData['experiments'] as List<dynamic>?;
      if (experiments != null) {
        for (final exp in experiments) {
          final customFieldValues = exp['customFieldValues'] as List<dynamic>?;
          if (customFieldValues != null) {
            for (final field in customFieldValues) {
              keys.add(field['name'] as String);
            }
          }
        }
      }

      final result = keys.toList()..sort();
      final newEvents = ctxData.eventCollector.getNewEvents(eventsBefore);

      return shelf.Response.ok(
        jsonEncode({'result': result, 'events': newEvents}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      return shelf.Response(400,
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context/<contextId>/customFieldValueType', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    try {
      final body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;
      final experimentName = body['experimentName'] as String;
      final fieldName = body['fieldName'] as String;
      final eventsBefore = ctxData.eventCollector.events.length;

      dynamic result;
      final experiments = ctxData.rawData['experiments'] as List<dynamic>?;
      if (experiments != null) {
        for (final exp in experiments) {
          if (exp['name'] == experimentName) {
            final customFieldValues = exp['customFieldValues'] as List<dynamic>?;
            if (customFieldValues != null) {
              for (final field in customFieldValues) {
                if (field['name'] == fieldName) {
                  result = field['type'];
                  break;
                }
              }
            }
            break;
          }
        }
      }

      final newEvents = ctxData.eventCollector.getNewEvents(eventsBefore);

      return shelf.Response.ok(
        jsonEncode({'result': result, 'events': newEvents}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      return shelf.Response(400,
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context/<contextId>/setOverride', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    try {
      final body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;
      final experimentName = body['experimentName'] as String;
      final variant = body['variant'] is int ? body['variant'] as int : (body['variant'] as num).toInt();
      final eventsBefore = ctxData.eventCollector.events.length;

      ctxData.context.setOverride(experimentName, variant);

      final newEvents = ctxData.eventCollector.getNewEvents(eventsBefore);

      return shelf.Response.ok(
        jsonEncode({'result': null, 'events': newEvents}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      return shelf.Response(400,
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context/<contextId>/setCustomAssignment', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    try {
      final body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;
      final experimentName = body['experimentName'] as String;
      final variant = body['variant'] is int ? body['variant'] as int : (body['variant'] as num).toInt();
      final eventsBefore = ctxData.eventCollector.events.length;

      ctxData.context.setCustomAssignment(experimentName, variant);

      final newEvents = ctxData.eventCollector.getNewEvents(eventsBefore);

      return shelf.Response.ok(
        jsonEncode({'result': null, 'events': newEvents}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      return shelf.Response(400,
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.get('/context/<contextId>/pending', (shelf.Request request, String contextId) {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    return shelf.Response.ok(
      jsonEncode({
        'result': ctxData.context.getPendingCount(),
        'events': [],
      }),
      headers: {'Content-Type': 'application/json'},
    );
  });

  router.get('/context/<contextId>/isFinalized', (shelf.Request request, String contextId) {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    return shelf.Response.ok(
      jsonEncode({
        'result': ctxData.context.isClosed(),
        'events': [],
      }),
      headers: {'Content-Type': 'application/json'},
    );
  });

  router.post('/context/<contextId>/publish', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    try {
      final eventsBefore = ctxData.eventCollector.events.length;

      await ctxData.context.publish();

      final newEvents = ctxData.eventCollector.getNewEvents(eventsBefore);
      return shelf.Response.ok(
        jsonEncode({'result': null, 'events': newEvents}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      return shelf.Response.internalServerError(
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context/<contextId>/refresh', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    try {
      final body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;
      final newData = body['newData'] as Map<String, dynamic>;
      final eventsBefore = ctxData.eventCollector.events.length;

      final normalizedData = _normalizeContextData(newData);
      final contextData = ContextData.fromMap(normalizedData);
      ctxData.dataProvider.setNextData(contextData);

      await ctxData.context.refresh();

      final newEvents = ctxData.eventCollector.getNewEvents(eventsBefore);
      return shelf.Response.ok(
        jsonEncode({'result': null, 'events': newEvents}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      print('Refresh error: $e');
      return shelf.Response.internalServerError(
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.post('/context/<contextId>/finalize', (shelf.Request request, String contextId) async {
    final ctxData = contexts[contextId];
    if (ctxData == null) {
      return shelf.Response.notFound(jsonEncode({'error': 'Context not found'}));
    }

    try {
      final eventsBefore = ctxData.eventCollector.events.length;

      await ctxData.context.close();

      final newEvents = ctxData.eventCollector.getNewEvents(eventsBefore);
      return shelf.Response.ok(
        jsonEncode({'result': null, 'events': newEvents}),
        headers: {'Content-Type': 'application/json'},
      );
    } catch (e) {
      print('Finalize error: $e');
      return shelf.Response.internalServerError(
        body: jsonEncode({'error': e.toString()}),
        headers: {'Content-Type': 'application/json'},
      );
    }
  });

  router.delete('/context/<contextId>', (shelf.Request request, String contextId) {
    contexts.remove(contextId);
    return shelf.Response.ok(
      jsonEncode({'result': 'deleted'}),
      headers: {'Content-Type': 'application/json'},
    );
  });

  final handler = shelf.Pipeline()
      .addMiddleware(shelf.logRequests())
      .addHandler(router);

  final port = int.parse(Platform.environment['PORT'] ?? '3000');
  final server = await shelf_io.serve(handler, '0.0.0.0', port);
  print('Flutter SDK wrapper listening on port ${server.port}');
}
