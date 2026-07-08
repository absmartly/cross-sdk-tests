from flask import Flask, request, jsonify
import sys
import os
import json
import re
import time
import uuid
import base64
import hashlib
from concurrent.futures import Future

from sdk.absmartly import ABSmartly
from sdk.absmartly_config import ABSmartlyConfig
from sdk.client import Client
from sdk.client_config import ClientConfig
from sdk.context_config import ContextConfig
from sdk.context_event_logger import ContextEventLogger, EventType
from sdk.context_publisher import ContextPublisher
from sdk.json.context_data import ContextData
from sdk.default_http_client import DefaultHTTPClient
from sdk.default_http_client_config import DefaultHTTPClientConfig
from sdk.context_data_provider import ContextDataProvider
import jsons
import threading
import urllib.request

app = Flask(__name__)

class DeferredContextDataProvider(ContextDataProvider):
    def __init__(self, endpoint, throttle_ms):
        self.endpoint = endpoint
        self.throttle_ms = throttle_ms

    def get_context_data(self):
        future = Future()
        def fetch():
            time.sleep(self.throttle_ms / 1000.0)
            try:
                with urllib.request.urlopen(self.endpoint, timeout=10) as response:
                    raw = json.loads(response.read().decode())
                    data = jsons.load(raw, ContextData)
                    future.set_result(data)
            except Exception as e:
                # Surface fetch/parse failures so the SDK's failed-load path
                # runs (context becomes FAILED, readyError reports the error)
                # instead of swallowing them into empty-but-ready data.
                future.set_exception(e)
        threading.Thread(target=fetch, daemon=True).start()
        return future

class FailingContextDataProvider(ContextDataProvider):
    def get_context_data(self):
        future = Future()
        future.set_exception(Exception('Context load failed'))
        return future

class EventCollector(ContextEventLogger):
    def __init__(self):
        self.events = []
        self._suppress_init_errors = False

    def handle_event(self, event_type, data):
        event_type_str = event_type.value if hasattr(event_type, 'value') else str(event_type)
        if event_type_str == 'close':
            event_type_str = 'finalize'
        if event_type_str == 'error' and self._suppress_init_errors:
            return
        serialized_data = self._serialize(data) if data is not None else None
        self.events.append({
            'type': event_type_str,
            'data': serialized_data,
            'timestamp': int(time.time() * 1000)
        })

    def _serialize(self, data):
        try:
            return jsons.dump(data)
        except Exception:
            try:
                return json.loads(json.dumps(data, default=str))
            except Exception:
                return str(data)

class CustomPublisher(ContextPublisher):
    def __init__(self, event_collector):
        self.event_collector = event_collector
        self._should_fail = False

    def publish(self, context, event):
        if self._should_fail:
            self._should_fail = False
            future = Future()
            future.set_exception(Exception('Publish failed'))
            return future
        future = Future()
        future.set_result(None)
        return future

contexts = {}
payload_store = {}

def translate_endpoint(endpoint):
    if endpoint is None:
        return None
    return re.sub(r'localhost:\d+', '127.0.0.1:3000', endpoint)

def is_context_finalized_error(err):
    msg = str(err).lower()
    return 'closed' in msg or 'closing' in msg or 'finalized' in msg

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'sdk': 'python',
        'version': '1.0.0'
    })

@app.route('/capabilities', methods=['GET'])
def capabilities():
    return jsonify({'diagnostics': True,
        'attrsSeq': False,
        'publishFail': True,
        'variableKeysMap': True,
        'globalCustomFieldKeys': True,
        'getUnits': True,
        'getAttributes': True,
        'readyError': True
    })

@app.route('/context_payload/<payload_id>', methods=['PUT'])
def store_context_payload(payload_id):
    req_data = request.json
    payload_store[payload_id] = req_data.get('data', {'experiments': []})
    return jsonify({'success': True})

@app.route('/context_payload/<payload_id>', methods=['GET'])
def get_context_payload(payload_id):
    throttle = int(request.args.get('throttle', 0))
    data = payload_store.get(payload_id, {'experiments': []})

    if throttle > 0:
        time.sleep(throttle / 1000.0)

    return jsonify(data)

@app.route('/context_payload/<payload_id>/context', methods=['GET'])
def mock_api_context(payload_id):
    data = payload_store.get(payload_id, {'experiments': []})
    return jsonify(data)

@app.route('/context', methods=['POST'])
def create_context():
    req_data = request.json

    if req_data.get('mode') == 'e2e':
        e2e_endpoint = os.environ.get('ABSMARTLY_E2E_ENDPOINT')
        e2e_api_key = os.environ.get('ABSMARTLY_E2E_API_KEY')
        e2e_application = os.environ.get('ABSMARTLY_E2E_APPLICATION')
        e2e_environment = os.environ.get('ABSMARTLY_E2E_ENVIRONMENT')
        if not all([e2e_endpoint, e2e_api_key, e2e_application, e2e_environment]):
            return jsonify({'error': 'e2e mode not configured'}), 501

        context_id = f"ctx-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        event_collector = EventCollector()

        client_config = ClientConfig()
        client_config.endpoint = e2e_endpoint
        client_config.api_key = e2e_api_key
        client_config.application = e2e_application
        client_config.environment = e2e_environment

        http_client_config = DefaultHTTPClientConfig()
        http_client = DefaultHTTPClient(http_client_config)
        client = Client(client_config, http_client)

        sdk_config = ABSmartlyConfig()
        sdk_config.client = client
        sdk_config.context_event_logger = event_collector

        sdk = ABSmartly(sdk_config)

        context_config = ContextConfig()
        context_config.units = {k: str(v) for k, v in req_data['units'].items()}
        context_config.publish_delay = -1
        context_config.refresh_interval = 0

        context = sdk.create_context(context_config)
        context.wait_until_ready()

        for name, value in (req_data.get('attributes') or {}).items():
            context.set_attribute(name, value)

        contexts[context_id] = {
            'context': context,
            'eventCollector': event_collector,
            'publisher': None
        }

        return jsonify({
            'result': {
                'contextId': context_id,
                'ready': context.is_ready(),
                'failed': context.is_failed(),
                'finalized': context.is_closed()
            },
            'events': event_collector.events
        })

    context_id = f"ctx-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"

    event_collector = EventCollector()
    publisher = CustomPublisher(event_collector)

    client_config = ClientConfig()
    endpoint = req_data.get('endpoint')
    translated_endpoint = translate_endpoint(endpoint) if endpoint else 'http://dummy'
    client_config.endpoint = translated_endpoint
    client_config.api_key = 'dummy'
    client_config.application = 'test'
    client_config.environment = 'test'

    http_client_config = DefaultHTTPClientConfig()
    http_client = DefaultHTTPClient(http_client_config)
    client = Client(client_config, http_client)

    sdk_config = ABSmartlyConfig()
    sdk_config.client = client
    sdk_config.context_event_logger = event_collector
    sdk_config.context_event_handler = publisher

    sdk = ABSmartly(sdk_config)

    context_config = ContextConfig()
    # Convert all units to strings (Python SDK requirement)
    context_config.units = {k: str(v) for k, v in req_data['units'].items()}
    context_config.publish_delay = -1
    context_config.refresh_interval = 0

    options = req_data.get('options', {})
    payload_throttle = int(options.get('payloadThrottle', 0))

    fail_load = req_data.get('failLoad', False)

    if 'data' in req_data:
        # Sync: createContextWith
        context_data = jsons.load(req_data['data'], ContextData)
        event_collector._suppress_init_errors = True
        context = sdk.create_context_with(context_config, context_data)
        event_collector._suppress_init_errors = False
    elif fail_load:
        failing_sdk_config = ABSmartlyConfig()
        failing_sdk_config.context_data_provider = FailingContextDataProvider()
        failing_sdk_config.context_event_logger = event_collector
        failing_sdk_config.context_event_handler = publisher
        failing_sdk = ABSmartly(failing_sdk_config)
        context = failing_sdk.create_context(context_config)
        for _ in range(50):
            if event_collector.events:
                break
            time.sleep(0.01)
    elif payload_throttle > 0 and endpoint:
        deferred_provider = DeferredContextDataProvider(translated_endpoint, payload_throttle)
        deferred_sdk_config = ABSmartlyConfig()
        deferred_sdk_config.context_data_provider = deferred_provider
        deferred_sdk_config.context_event_logger = event_collector
        deferred_sdk_config.context_event_handler = publisher
        deferred_sdk = ABSmartly(deferred_sdk_config)
        context = deferred_sdk.create_context(context_config)
    else:
        # Async: createContext (SDK will fetch from endpoint)
        context = sdk.create_context(context_config)
        context.wait_until_ready()
        # Wait for events to be collected (like Go/Java wrappers)
        for _ in range(50):
            if event_collector.events:
                break
            time.sleep(0.01)

    contexts[context_id] = {
        'context': context,
        'eventCollector': event_collector,
        'publisher': publisher
    }

    return jsonify({
        'result': {
            'contextId': context_id,
            'ready': context.is_ready(),
            'failed': context.is_failed(),
            'finalized': context.is_closed()
        },
        'events': event_collector.events
    })

@app.route('/context/<context_id>/setUnit', methods=['POST'])
def set_unit(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        unit_type = request.json['unitType']
        uid = str(request.json['uid'])

        existing = context.units.get(unit_type)
        if existing is not None and existing != uid:
            return jsonify({'error': f"Unit '{unit_type}' UID already set."}), 400

        context.set_unit(unit_type, uid)
        new_events = collector.events[events_before:]
        return jsonify({'result': None, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/getUnit', methods=['POST'])
def get_unit(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        result = context.units.get(request.json['unitType'])
        if result is not None:
            try:
                if '.' in str(result):
                    result = float(result)
                else:
                    result = int(result)
            except (ValueError, TypeError):
                pass
        new_events = collector.events[events_before:]
        return jsonify({'result': result, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/attribute', methods=['POST'])
def attribute(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        context.set_attribute(request.json['name'], request.json['value'])
        new_events = collector.events[events_before:]
        return jsonify({'result': None, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/getAttribute', methods=['POST'])
def get_attribute(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        # Python SDK doesn't have get_attribute, search in attributes list
        result = None
        for attr in context.attributes:
            if attr.name == request.json['name']:
                result = attr.value
        new_events = collector.events[events_before:]
        return jsonify({'result': result, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/treatment', methods=['POST'])
def treatment(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    # The python SDK's get_treatment() returns 0 after finalize (context.py:642)
    # rather than raising, so guard the finalized state explicitly (scenario 189).
    if context.is_closed():
        return jsonify({'error': 'Context finalized'}), 400

    try:
        variant = context.get_treatment(request.json['experimentName'])
        new_events = collector.events[events_before:]
        return jsonify({'result': variant, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/peek', methods=['POST'])
def peek(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        variant = context.peek_treatment(request.json['experimentName'])
        new_events = collector.events[events_before:]
        return jsonify({'result': variant, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/variableValue', methods=['POST'])
def variable_value(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        value = context.get_variable_value(request.json['key'], request.json['defaultValue'])
        new_events = collector.events[events_before:]
        return jsonify({'result': value, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/peekVariableValue', methods=['POST'])
def peek_variable_value(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        value = context.peek_variable_value(request.json['key'], request.json['defaultValue'])
        new_events = collector.events[events_before:]
        return jsonify({'result': value, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/track', methods=['POST'])
def track(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        goal_name = request.json['goalName']
        properties = request.json.get('properties')

        if properties is not None and not isinstance(properties, dict):
            return jsonify({'error': f"Goal '{goal_name}' properties must be of type object."}), 400

        pending_before = context.get_pending_count()
        context.track(goal_name, properties)
        pending_after = context.get_pending_count()
        print(f"DEBUG track: pending before={pending_before}, after={pending_after}", file=sys.stderr)
        new_events = collector.events[events_before:]
        return jsonify({'result': None, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/override', methods=['POST'])
def override(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']

    try:
        context.set_override(request.json['experimentName'], request.json['variant'])
        return jsonify({'result': None, 'events': []})
    except Exception as e:
        if is_context_finalized_error(e):
            return jsonify({'result': None, 'events': []})
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/customAssignment', methods=['POST'])
def custom_assignment(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']

    try:
        context.set_custom_assignment(request.json['experimentName'], request.json['variant'])
        return jsonify({'result': None, 'events': []})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/customFieldValue', methods=['POST'])
def custom_field_value(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        value = context.get_custom_field_value(request.json['experimentName'], request.json['fieldName'])
        new_events = collector.events[events_before:]
        return jsonify({'result': value, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/variableKeys', methods=['POST'])
def variable_keys(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        keys = context.get_variable_keys()
        # Python SDK returns dict, we want just the keys as array
        result = list(keys.keys()) if isinstance(keys, dict) else keys
        new_events = collector.events[events_before:]
        return jsonify({'result': result, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/customFieldKeys', methods=['POST'])
def custom_field_keys(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        # Python SDK customFieldKeys() takes NO parameters
        keys = context.get_custom_field_keys()
        new_events = collector.events[events_before:]
        return jsonify({'result': keys, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/customFieldValueType', methods=['POST'])
def custom_field_value_type(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        # Python SDK method is get_custom_field_type, not get_custom_field_value_type
        value_type = context.get_custom_field_type(request.json['experimentName'], request.json['fieldName'])
        new_events = collector.events[events_before:]
        return jsonify({'result': value_type, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/setOverride', methods=['POST'])
def set_override(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        # Python SDK uses set_override method
        context.set_override(request.json['experimentName'], request.json['variant'])
        new_events = collector.events[events_before:]
        return jsonify({'result': None, 'events': new_events})
    except Exception as e:
        if is_context_finalized_error(e):
            return jsonify({'result': None, 'events': []})
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/setCustomAssignment', methods=['POST'])
def set_custom_assignment(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        # Python SDK uses set_custom_assignment method
        context.set_custom_assignment(request.json['experimentName'], request.json['variant'])
        new_events = collector.events[events_before:]
        return jsonify({'result': None, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/pending', methods=['GET'])
def pending(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    count = ctx_data['context'].get_pending_count()
    print(f"DEBUG pending: count={count}", file=sys.stderr)
    return jsonify({'result': count, 'events': []})

@app.route('/context/<context_id>/isFinalized', methods=['GET'])
def is_finalized(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    return jsonify({'result': ctx_data['context'].is_closed(), 'events': []})

@app.route('/context/<context_id>/isReady', methods=['GET'])
def is_ready(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    return jsonify({'result': ctx_data['context'].is_ready(), 'events': []})

@app.route('/context/<context_id>/isFailed', methods=['GET'])
def is_failed(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    return jsonify({'result': ctx_data['context'].is_failed(), 'events': []})

@app.route('/context/<context_id>/experiments', methods=['GET'])
def experiments(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']

    try:
        result = context.get_experiments()
        return jsonify({'result': result, 'events': []})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/getUnits', methods=['POST'])
def get_units(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        result = dict(context.units)
        for k, v in result.items():
            try:
                if '.' in str(v):
                    result[k] = float(v)
                else:
                    result[k] = int(v)
            except (ValueError, TypeError):
                pass
        new_events = collector.events[events_before:]
        return jsonify({'result': result, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/getAttributes', methods=['POST'])
def get_attributes(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        result = {}
        for attr in context.attributes:
            result[attr.name] = attr.value
        new_events = collector.events[events_before:]
        return jsonify({'result': result, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/readyError', methods=['POST'])
def ready_error(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        error = getattr(context, 'ready_error', None)
        if callable(error):
            error = error()
        result = {'isError': True, 'message': str(error)} if error else None
        new_events = collector.events[events_before:]
        return jsonify({'result': result, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/variableKeysMap', methods=['POST'])
def variable_keys_map(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        keys = context.get_variable_keys()
        new_events = collector.events[events_before:]
        return jsonify({'result': keys, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/globalCustomFieldKeys', methods=['POST'])
def global_custom_field_keys(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        keys = context.get_custom_field_keys()
        new_events = collector.events[events_before:]
        return jsonify({'result': keys, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/context/<context_id>/publishFail', methods=['POST'])
def publish_fail(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    ctx_data['publisher']._should_fail = True
    return jsonify({'result': None, 'events': []})

@app.route('/context/<context_id>/publish', methods=['POST'])
def publish(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        context.publish()
        new_events = collector.events[events_before:]
        return jsonify({'result': None, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/context/<context_id>/refresh', methods=['POST'])
def refresh(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        context.refresh()
        for _ in range(50):
            if len(collector.events) > events_before:
                break
            time.sleep(0.01)
        new_events = collector.events[events_before:]
        return jsonify({'result': None, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/context/<context_id>/finalize', methods=['POST'])
def finalize(context_id):
    if context_id not in contexts:
        return jsonify({'error': 'Context not found'}), 404

    ctx_data = contexts[context_id]
    context = ctx_data['context']
    collector = ctx_data['eventCollector']
    events_before = len(collector.events)

    try:
        context.close()
        new_events = collector.events[events_before:]
        return jsonify({'result': None, 'events': new_events})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/context/<context_id>', methods=['DELETE'])
def delete_context(context_id):
    if context_id in contexts:
        del contexts[context_id]
    return jsonify({'result': 'deleted'})

@app.route('/diagnostic', methods=['POST'])
def diagnostic():
    try:
        body = request.json or {}
        op = body.get('operation')
        value = body.get('value')

        if op == 'hashUnit':
            text = '' if value is None else str(value)
            digest = hashlib.md5(text.encode('utf-8')).digest()
            result = base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')
        elif op == 'base64UrlNoPadding':
            text = '' if value is None else str(value)
            result = base64.urlsafe_b64encode(text.encode('utf-8')).decode('ascii').rstrip('=')
        elif op == 'utf8Bytes':
            text = '' if value is None else str(value)
            result = list(text.encode('utf-8'))
        elif op == 'isObject':
            result = isinstance(value, dict)
        elif op == 'isNumeric':
            result = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif op == 'isPromise':
            result = hasattr(value, '__await__')
        else:
            return jsonify({'error': f'Unsupported diagnostic operation: {op}'}), 400

        return jsonify({'result': result, 'events': []})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3000, debug=False)
