require 'sinatra'
require 'json'
require 'ostruct'
require 'net/http'
require 'uri'
require 'absmartly/liquid'
require 'context_data_provider'
require 'default_context_data_provider'
require 'context_publisher'
require 'a_b_smartly_config'
require 'default_context_data_deserializer'
require 'hashing'
require 'type_utils'

# Register Liquid SDK filters and tags
ABsmartly::Liquid.register_all

# Custom filter to convert Ruby objects to JSON for template output
module JsonFilter
  def json(input)
    input.to_json
  end
end
Liquid::Template.register_filter(JsonFilter)

set :port, 3000
set :bind, '0.0.0.0'

def translate_endpoint(endpoint)
  return endpoint if endpoint.nil?
  endpoint.gsub(/localhost:\d+/, '127.0.0.1:3000')
end

class EventCollector
  attr_reader :events

  def initialize
    @events = []
  end

  def handle_event(event_type, data)
    @events << {
      type: event_type.to_s,
      data: deep_copy(data),
      timestamp: (Time.now.to_f * 1000).to_i
    }
  end

  # Serialization failures propagate: a dropped (nil) payload would let an
  # event pass with its data silently missing.
  def deep_copy(obj)
    obj = convert_to_serializable(obj)
    JSON.parse(JSON.generate(obj))
  end

  def convert_to_serializable(obj)
    case obj
    when OpenStruct
      ostruct_to_hash(obj)
    when Array
      obj.map { |item| convert_to_serializable(item) }
    when Hash
      obj.transform_values { |v| convert_to_serializable(v) }
    when Exposure, GoalAchievement, PublishEvent, Unit, Attribute, ContextData, Experiment
      convert_object_to_hash(obj)
    else
      if obj.class.name.start_with?('OpenStruct')
        ostruct_to_hash(obj)
      else
        obj
      end
    end
  end

  def convert_object_to_hash(obj)
    obj.instance_variables.each_with_object({}) do |var, hash|
      key_str = var.to_s.delete('@')
      camel_key = snake_to_camel(key_str)
      value = obj.instance_variable_get(var)
      hash[camel_key.to_sym] = convert_to_serializable(value)
    end
  end

  def snake_to_camel(str)
    str.split('_').each_with_index.map do |word, index|
      index.zero? ? word : word.capitalize
    end.join
  end

  def ostruct_to_hash(obj)
    return obj unless obj.is_a?(OpenStruct)

    obj.to_h.transform_values do |v|
      if v.is_a?(OpenStruct)
        ostruct_to_hash(v)
      elsif v.is_a?(Array)
        v.map { |item| item.is_a?(OpenStruct) ? ostruct_to_hash(item) : item }
      else
        v
      end
    end
  end
end

# Preloaded context data (scenario `data`) is parsed by the SDK's own
# DefaultContextDataDeserializer, the same one its Client uses for a fetch.
class PreloadedData
  attr_reader :data_future

  def initialize(data)
    json = JSON.generate(data)
    @data_future = DefaultContextDataDeserializer.new.deserialize(json, 0, json.bytesize)
    raise 'Context data could not be deserialized' if @data_future.nil?
  end

  def success?
    true
  end
end

class FailingDataWrapper
  def initialize(exception = StandardError.new('Context load failed'))
    @exception = exception
  end

  def data_future
    nil
  end

  def success?
    false
  end

  def exception
    @exception
  end
end

class FailingDataProvider < ContextDataProvider
  def context_data
    FailingDataWrapper.new(StandardError.new('Context load failed'))
  end
end

# Delays the SDK's own context-data fetch by throttle_ms. Fetch and
# deserialization are delegated to the SDK's DefaultContextDataProvider over a
# real Client, so a throttled context exercises the same HTTP + parse path as
# a non-throttled one.
class DeferredDataProvider < ContextDataProvider
  def initialize(client, throttle_ms)
    @delegate = DefaultContextDataProvider.new(client)
    @throttle_ms = throttle_ms
  end

  def context_data
    sleep(@throttle_ms / 1000.0) if @throttle_ms > 0
    @delegate.context_data
  end
end

# Render a template against this context only. The ABsmartly Liquid filters
# and tags resolve the context from the 'absmartly' drop; the process-global
# ABsmartly::Liquid.current_context is never set, so one request can never
# render against another request's context.
def render_liquid(context, source, assigns)
  Liquid::Template.parse(source).render!(
    assigns.merge('absmartly' => ABsmartly::Liquid::Drop.new(context))
  )
end

# The absmartly_treatment / absmartly_peek output is the SDK's integer variant;
# anything else means the template did not render a variant.
def events_since(collector, events_before)
  collector.events[events_before..-1] || []
end

# Error responses carry the SDK's message unchanged plus the events the SDK
# emitted during the call.
def error_response(status, error, collector, events_before)
  body = { error: error.message }
  body[:events] = collector ? events_since(collector, events_before) : []
  halt status, body.to_json
end

def parse_variant(rendered)
  Integer(rendered.strip, 10)
rescue ArgumentError, TypeError
  raise "Liquid template rendered a non-integer variant: #{rendered.inspect}"
end

class CustomPublisher < ContextPublisher
  attr_accessor :should_fail

  def initialize(event_collector)
    @event_collector = event_collector
    @should_fail = false
  end

  def publish(context, event)
    if @should_fail
      @should_fail = false
      raise 'Publish failed'
    end
    self
  end
end

$contexts = {}
$payload_store = {}

get '/health' do
  content_type :json
  {
    status: 'healthy',
    sdk: 'liquid',
    version: '1.0.0'
  }.to_json
end

get '/capabilities' do
  content_type :json
  {diagnostics: true,
    diagnosticsUtf8: true,
    diagnosticsBase64: true,
    diagnosticsTypeChecks: true,
    diagnosticsIsPromise: false,
    attrsSeq: false,
    isWrapper: true,
    wrapsSDK: 'ruby',
    publishFail: true,
    variableKeysMap: true,
    globalCustomFieldKeys: true,
    getUnits: true,
    getAttributes: true,
    readyError: true,
    passThroughOperations: [
      'attribute', 'override', 'customAssignment', 'pending', 'isFinalized',
      'publish', 'finalize', 'setUnit', 'getUnit', 'getAttribute',
      'variableKeys', 'customFieldKeys', 'customFieldValueType',
      'setOverride', 'setCustomAssignment', 'refresh',
      'diagnostic', 'isReady', 'isFailed',
      'getUnits', 'getAttributes', 'readyError', 'variableKeysMap',
      'globalCustomFieldKeys', 'publishFail'
    ]
  }.to_json
end

put '/context_payload/:payload_id' do
  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  $payload_store[params['payload_id']] = req_data[:data] || { experiments: [] }

  content_type :json
  { success: true }.to_json
end

get '/context_payload/:payload_id' do
  throttle = (params['throttle'] || '0').to_i
  data = $payload_store[params['payload_id']] || { experiments: [] }

  sleep(throttle / 1000.0) if throttle > 0

  content_type :json
  data.to_json
end

get '/context_payload/:payload_id/context' do
  # A never-stored payload id is a missing collector payload: answer 404 so the
  # SDK's own fetch fails the context, instead of serving an empty-but-valid
  # payload that would make it silently ready.
  content_type :json
  data = $payload_store[params['payload_id']]
  halt 404, { error: 'Payload not found' }.to_json if data.nil?
  data.to_json
end

post '/context' do
  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  unless req_data[:units].is_a?(Hash)
    content_type :json
    halt 400, { error: 'units required' }.to_json
  end

  if req_data[:mode] == 'e2e'
    e2e_endpoint = ENV['ABSMARTLY_E2E_ENDPOINT']
    e2e_api_key = ENV['ABSMARTLY_E2E_API_KEY']
    e2e_application = ENV['ABSMARTLY_E2E_APPLICATION']
    e2e_environment = ENV['ABSMARTLY_E2E_ENVIRONMENT']
    unless e2e_endpoint && e2e_api_key && e2e_application && e2e_environment
      content_type :json
      halt 501, { error: 'e2e mode not configured' }.to_json
    end

    context_id = "ctx-#{Time.now.to_i}-#{rand(100000)}"
    event_collector = EventCollector.new

    client_config = ClientConfig.new
    client_config.endpoint = e2e_endpoint
    client_config.api_key = e2e_api_key
    client_config.application = e2e_application
    client_config.environment = e2e_environment

    client = Client.create(client_config)

    sdk_config = ABSmartlyConfig.new
    sdk_config.client = client
    sdk_config.context_event_logger = event_collector

    sdk = ABSmartly.new(sdk_config)

    context_config = ContextConfig.new
    context_config.units = req_data[:units].transform_keys(&:to_sym)
    context_config.publish_delay = -1
    context_config.refresh_interval = 0

    context = sdk.create_context(context_config)
    50.times do
      break if context.ready?
      sleep 0.1
    end

    (req_data[:attributes] || {}).each do |name, value|
      context.set_attribute(name.to_s, value)
    end

    $contexts[context_id] = {
      context: context,
      eventCollector: event_collector,
      publisher: nil
    }

    content_type :json
    return {
      result: {
        contextId: context_id,
        ready: context.ready?,
        failed: context.failed? || false,
        finalized: context.closed?
      },
      events: event_collector.events
    }.to_json
  end

  context_id = "ctx-#{Time.now.to_i}-#{rand(100000)}"

  event_collector = EventCollector.new
  publisher = CustomPublisher.new(event_collector)

  client_config = ClientConfig.new
  endpoint = req_data[:endpoint]
  translated_endpoint = endpoint ? translate_endpoint(endpoint) : 'http://dummy'
  client_config.endpoint = translated_endpoint
  client_config.api_key = 'dummy'
  client_config.application = 'test'
  client_config.environment = 'test'

  client = Client.create(client_config)

  sdk_config = ABSmartlyConfig.new
  sdk_config.client = client
  sdk_config.context_event_handler = publisher
  sdk_config.context_event_logger = event_collector

  sdk = ABSmartly.new(sdk_config)

  context_config = ContextConfig.new
  context_config.units = req_data[:units].transform_keys(&:to_sym)
  context_config.publish_delay = -1
  context_config.refresh_interval = 0
  options = req_data[:options] || {}
  payload_throttle = options[:payloadThrottle] || 0

  if req_data[:data]
    context = sdk.create_context_with(context_config, PreloadedData.new(req_data[:data]))
  elsif req_data[:failLoad]
    failing_sdk_config = ABSmartlyConfig.new
    failing_sdk_config.context_data_provider = FailingDataProvider.new
    failing_sdk_config.context_event_handler = publisher
    failing_sdk_config.context_event_logger = event_collector
    context = ABSmartly.new(failing_sdk_config).create_context(context_config)
  elsif payload_throttle.to_i > 0
    # Delay the SDK's own fetch; the context stays not-ready until it lands.
    deferred_sdk_config = ABSmartlyConfig.new
    deferred_sdk_config.context_data_provider = DeferredDataProvider.new(client, payload_throttle.to_i)
    deferred_sdk_config.context_event_handler = publisher
    deferred_sdk_config.context_event_logger = event_collector
    deferred_sdk_config.client = client
    context = ABSmartly.new(deferred_sdk_config).create_context_async(context_config)
  else
    # The SDK's Client fetches <endpoint>/context synchronously; the ready or
    # error event is logged before create_context returns.
    context = sdk.create_context(context_config)
  end

  $contexts[context_id] = {
    context: context,
    eventCollector: event_collector,
    publisher: publisher
  }

  content_type :json
  {
    result: {
      contextId: context_id,
      ready: context.ready?,
      failed: context.failed? || false,
      finalized: context.closed?
    },
    events: event_collector.events.dup
  }.to_json
end

post '/context/:context_id/treatment' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  collector = ctx_data[:eventCollector]
  events_before = collector.events.length

  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  begin
    # Use Liquid template with absmartly_treatment block tag
    # The tag needs context['absmartly'] to be a Drop with treatment method
    rendered = render_liquid(context,
      "{% absmartly_treatment experiment_name %}{{ variant }}{% endabsmartly_treatment %}",
      'experiment_name' => req_data[:experimentName])
    variant = parse_variant(rendered)

    new_events = events_since(collector, events_before)

    content_type :json
    { result: variant, events: new_events }.to_json
  rescue => e
    error_response(400, e, collector, events_before)
  end
end

post '/context/:context_id/peek' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  collector = ctx_data[:eventCollector]
  events_before = collector.events.length

  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  begin
    # Use Liquid template with absmartly_peek filter
    rendered = render_liquid(context, "{{ experiment_name | absmartly_peek }}",
      'experiment_name' => req_data[:experimentName])
    variant = parse_variant(rendered)

    new_events = events_since(collector, events_before)

    content_type :json
    { result: variant, events: new_events }.to_json
  rescue => e
    error_response(400, e, collector, events_before)
  end
end

post '/context/:context_id/track' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  collector = ctx_data[:eventCollector]
  events_before = collector.events.length

  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  begin
    # Track renders the Liquid absmartly_track filter; properties pass through
    # unchanged (nil stays nil) and the SDK validates them.
    render_liquid(context, "{{ goal_name | absmartly_track: properties }}",
      'goal_name' => req_data[:goalName], 'properties' => req_data[:properties])
    new_events = events_since(collector, events_before)

    content_type :json
    { result: nil, events: new_events }.to_json
  rescue => e
    error_response(400, e, collector, events_before)
  end
end

post '/context/:context_id/attribute' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  collector = ctx_data[:eventCollector]
  events_before = collector.events.length

  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  begin
    context.set_attribute(req_data[:name], req_data[:value])
    new_events = events_since(collector, events_before)

    content_type :json
    { result: nil, events: new_events }.to_json
  rescue => e
    error_response(400, e, collector, events_before)
  end
end

# Runs one SDK call against a stored context and replies with its result and
# the events the SDK emitted during the call (also on error).
def sdk_route(context_id, error_status = 400)
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]
  ctx_data = $contexts[context_id]
  collector = ctx_data[:eventCollector]
  events_before = collector.events.length
  content_type :json
  begin
    result = yield ctx_data[:context]
    { result: result, events: events_since(collector, events_before) }.to_json
  rescue => e
    error_response(error_status, e, collector, events_before)
  end
end

def read_body
  request.body.rewind
  JSON.parse(request.body.read, symbolize_names: true)
end

post '/context/:context_id/override' do
  req_data = read_body
  sdk_route(params['context_id']) do |context|
    context.set_override(req_data[:experimentName], req_data[:variant])
    nil
  end
end

post '/context/:context_id/customAssignment' do
  req_data = read_body
  sdk_route(params['context_id']) do |context|
    context.set_custom_assignment(req_data[:experimentName], req_data[:variant])
    nil
  end
end

get '/context/:context_id/pending' do
  sdk_route(params['context_id']) { |context| context.pending_count }
end

get '/context/:context_id/isFinalized' do
  sdk_route(params['context_id']) { |context| context.closed? }
end

get '/context/:context_id/isReady' do
  sdk_route(params['context_id']) { |context| context.ready? }
end

get '/context/:context_id/isFailed' do
  sdk_route(params['context_id']) { |context| context.failed? }
end

get '/context/:context_id/experiments' do
  sdk_route(params['context_id']) do |context|
    # Through the Liquid Drop, which raises the SDK's errors.
    JSON.parse(render_liquid(context, "{{ absmartly.experiments | json }}", {}).strip)
  end
end

post '/context/:context_id/getUnits' do
  sdk_route(params['context_id']) { |context| context.get_units.transform_keys(&:to_s) }
end

post '/context/:context_id/getAttributes' do
  sdk_route(params['context_id']) { |context| context.get_attributes }
end

post '/context/:context_id/readyError' do
  sdk_route(params['context_id']) do |context|
    error = context.ready_error
    error ? { isError: true, message: error.to_s } : nil
  end
end

post '/context/:context_id/variableKeysMap' do
  sdk_route(params['context_id']) { |context| context.variable_keys.transform_keys(&:to_s) }
end

post '/context/:context_id/globalCustomFieldKeys' do
  sdk_route(params['context_id']) { |context| context.custom_field_keys }
end

post '/context/:context_id/publishFail' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]
  $contexts[context_id][:publisher].should_fail = true
  content_type :json
  { result: nil, events: [] }.to_json
end

post '/context/:context_id/publish' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  collector = ctx_data[:eventCollector]
  events_before = collector.events.length

  begin
    context.publish
    new_events = events_since(collector, events_before)

    content_type :json
    { result: nil, events: new_events }.to_json
  rescue => e
    error_response(500, e, collector, events_before)
  end
end

post '/context/:context_id/finalize' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  collector = ctx_data[:eventCollector]
  events_before = collector.events.length

  begin
    context.close
    new_events = events_since(collector, events_before)

    content_type :json
    { result: nil, events: new_events }.to_json
  rescue => e
    error_response(500, e, collector, events_before)
  end
end

post '/context/:context_id/setUnit' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  collector = ctx_data[:eventCollector]
  events_before = collector.events.length

  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  begin
    context.set_unit(req_data[:unitType], req_data[:uid])
    new_events = events_since(collector, events_before)
    content_type :json
    { result: nil, events: new_events }.to_json
  rescue => e
    error_response(400, e, collector, events_before)
  end
end

post '/context/:context_id/getUnit' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  collector = ctx_data[:eventCollector]
  events_before = collector.events.length

  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  begin
    result = context.get_unit(req_data[:unitType])

    new_events = events_since(collector, events_before)
    content_type :json
    { result: result, events: new_events }.to_json
  rescue => e
    error_response(400, e, collector, events_before)
  end
end

post '/context/:context_id/getAttribute' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  collector = ctx_data[:eventCollector]
  events_before = collector.events.length

  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  begin
    result = context.get_attribute(req_data[:name])
    new_events = events_since(collector, events_before)
    content_type :json
    { result: result, events: new_events }.to_json
  rescue => e
    error_response(400, e, collector, events_before)
  end
end

post '/context/:context_id/variableValue' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  collector = ctx_data[:eventCollector]
  events_before = collector.events.length

  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  begin
    # Use Liquid template with absmartly_variable filter + json filter
    rendered = render_liquid(context, "{{ key | absmartly_variable: default_value | json }}",
      'key' => req_data[:key],
      'default_value' => req_data[:defaultValue])

    # Parse the JSON result
    result = JSON.parse(rendered.strip)

    new_events = events_since(collector, events_before)
    content_type :json
    { result: result, events: new_events }.to_json
  rescue => e
    error_response(400, e, collector, events_before)
  end
end

post '/context/:context_id/peekVariableValue' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  collector = ctx_data[:eventCollector]
  events_before = collector.events.length

  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  begin
    # Use Liquid template with absmartly_peek_variable filter + json filter
    rendered = render_liquid(context, "{{ key | absmartly_peek_variable: default_value | json }}",
      'key' => req_data[:key],
      'default_value' => req_data[:defaultValue])

    # Parse the JSON result
    result = JSON.parse(rendered.strip)

    new_events = events_since(collector, events_before)
    content_type :json
    { result: result, events: new_events }.to_json
  rescue => e
    error_response(400, e, collector, events_before)
  end
end

post '/context/:context_id/customFieldValue' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  collector = ctx_data[:eventCollector]
  events_before = collector.events.length

  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  begin
    # Use Liquid template with absmartly_custom_field filter + json filter for complex types
    rendered = render_liquid(context, "{{ experiment_name | absmartly_custom_field: field_name | json }}",
      'experiment_name' => req_data[:experimentName],
      'field_name' => req_data[:fieldName])

    # Parse the JSON result
    result = JSON.parse(rendered.strip)

    new_events = events_since(collector, events_before)
    content_type :json
    { result: result, events: new_events }.to_json
  rescue => e
    error_response(400, e, collector, events_before)
  end
end

post '/context/:context_id/variableKeys' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  collector = ctx_data[:eventCollector]
  events_before = collector.events.length

  begin
    keys = context.variable_keys
    result = keys.is_a?(Hash) ? keys.keys.map(&:to_s) : keys
    new_events = events_since(collector, events_before)
    content_type :json
    { result: result, events: new_events }.to_json
  rescue => e
    error_response(400, e, collector, events_before)
  end
end

post '/context/:context_id/customFieldKeys' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  collector = ctx_data[:eventCollector]
  events_before = collector.events.length

  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  begin
    keys = context.custom_field_keys
    new_events = events_since(collector, events_before)
    content_type :json
    { result: keys, events: new_events }.to_json
  rescue => e
    error_response(400, e, collector, events_before)
  end
end

post '/context/:context_id/customFieldValueType' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  collector = ctx_data[:eventCollector]
  events_before = collector.events.length

  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  begin
    value_type = context.custom_field_type(req_data[:experimentName], req_data[:fieldName])
    new_events = events_since(collector, events_before)
    content_type :json
    { result: value_type, events: new_events }.to_json
  rescue => e
    error_response(400, e, collector, events_before)
  end
end

post '/context/:context_id/setOverride' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  collector = ctx_data[:eventCollector]
  events_before = collector.events.length

  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  begin
    context.set_override(req_data[:experimentName], req_data[:variant])
    new_events = events_since(collector, events_before)
    content_type :json
    { result: nil, events: new_events }.to_json
  rescue => e
    error_response(400, e, collector, events_before)
  end
end

post '/context/:context_id/setCustomAssignment' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  collector = ctx_data[:eventCollector]
  events_before = collector.events.length

  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  begin
    context.set_custom_assignment(req_data[:experimentName], req_data[:variant])
    new_events = events_since(collector, events_before)
    content_type :json
    { result: nil, events: new_events }.to_json
  rescue => e
    error_response(400, e, collector, events_before)
  end
end

post '/context/:context_id/refresh' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  collector = ctx_data[:eventCollector]
  events_before = collector.events.length

  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  begin
    context.refresh
    new_events = events_since(collector, events_before)
    content_type :json
    { result: nil, events: new_events }.to_json
  rescue => e
    error_response(500, e, collector, events_before)
  end
end

delete '/context/:context_id' do
  context_id = params['context_id']
  $contexts.delete(context_id) if $contexts[context_id]

  content_type :json
  { result: 'deleted' }.to_json
end

post '/diagnostic' do
  content_type :json
  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)
  op = req_data[:operation]
  value = req_data[:value]

  result = case op
           when 'hashUnit' then Hashing.hash_unit(value)
           when 'base64UrlNoPadding' then Hashing.base64_url_no_padding(Hashing.to_utf8(value))
           when 'utf8Bytes' then Hashing.to_utf8(value).bytes
           when 'isObject' then TypeUtils.object?(value)
           when 'isNumeric' then TypeUtils.numeric?(value)
           else
             # isPromise has no Ruby SDK implementation, so it is not offered.
             halt 400, { error: "Unsupported diagnostic operation: #{op}", events: [] }.to_json
           end

  { result: result, events: [] }.to_json
rescue => e
  error_response(500, e, nil, 0)
end
