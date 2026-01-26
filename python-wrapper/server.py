from flask import Flask, request, jsonify
import sys
import os
import json
import time
import uuid
from concurrent.futures import Future

from sdk.absmartly import ABSmartly
from sdk.absmartly_config import ABSmartlyConfig
from sdk.client import Client
from sdk.client_config import ClientConfig
from sdk.context_config import ContextConfig
from sdk.context_event_logger import ContextEventLogger, EventType
from sdk.context_event_handler import ContextEventHandler
from sdk.json.context_data import ContextData
import jsons

app = Flask(__name__)

class EventCollector(ContextEventLogger):
    def __init__(self):
        self.events = []

    def handle_event(self, event_type, data):
        event_type_str = event_type.value if hasattr(event_type, 'value') else str(event_type)
        if event_type_str == 'close':
            event_type_str = 'finalize'
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

class CustomEventHandler(ContextEventHandler):
    def __init__(self, event_collector):
        self.event_collector = event_collector

    def publish(self, context, event):
        # Don't log publish event here - SDK's event_logger will handle it
        # Just return resolved future without HTTP call
        future = Future()
        future.set_result(None)
        return future

contexts = {}
payload_store = {}

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'sdk': 'python',
        'version': '1.0.0'
    })

@app.route('/capabilities', methods=['GET'])
def capabilities():
    return jsonify({
        'asyncContext': False,  # Async context not fully supported yet
        'attrsSeq': False       # Attribute sequence tracking not implemented
    })

@app.route('/context_payload', methods=['PUT'])
def store_context_payload():
    req_data = request.json
    payload_id = f"payload-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    payload_store[payload_id] = req_data['data']

    url = f"http://python-sdk:3000/context_payload/{payload_id}"
    return jsonify({'payloadUrl': url, 'payloadId': payload_id})

@app.route('/context_payload/<payload_id>', methods=['GET'])
def get_context_payload(payload_id):
    throttle = int(request.args.get('throttle', 0))
    data = payload_store.get(payload_id, {'experiments': []})

    if throttle > 0:
        time.sleep(throttle / 1000.0)

    return jsonify(data)

@app.route('/context', methods=['POST'])
def create_context():
    req_data = request.json
    context_id = f"ctx-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"

    event_collector = EventCollector()
    custom_event_handler = CustomEventHandler(event_collector)

    client_config = ClientConfig()
    client_config.endpoint = req_data.get('endpoint', 'http://dummy')  # Use provided endpoint or dummy
    client_config.api_key = 'dummy'
    client_config.application = 'test'
    client_config.environment = 'test'

    client = Client(client_config, None)

    sdk_config = ABSmartlyConfig()
    sdk_config.client = client
    sdk_config.context_event_logger = event_collector
    sdk_config.context_event_handler = custom_event_handler

    sdk = ABSmartly(sdk_config)

    context_config = ContextConfig()
    # Convert all units to strings (Python SDK requirement)
    context_config.units = {k: str(v) for k, v in req_data['units'].items()}
    context_config.publish_delay = -1
    context_config.refresh_interval = 0

    if 'data' in req_data:
        # Sync: createContextWith
        context_data = jsons.load(req_data['data'], ContextData)
        context = sdk.create_context_with(context_config, context_data)
    else:
        # Async: createContext (SDK will fetch from endpoint)
        context = sdk.create_context(context_config)

    contexts[context_id] = {
        'context': context,
        'eventCollector': event_collector
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
        new_data_dict = request.json['newData']
        context_data = jsons.load(new_data_dict, ContextData)

        context.assignment_cache.clear()

        context.set_data(context_data)
        collector.handle_event(EventType.REFRESH, context_data)
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3000, debug=False)
