require 'sinatra'
require 'json'
require 'ostruct'
require 'securerandom'
require 'net/http'
require 'uri'
require 'a_b_smartly'
require 'a_b_smartly_config'
require 'client'
require 'client_config'
require 'context_config'
require 'context_data_provider'
require 'default_context_data_provider'
require 'default_context_data_deserializer'
require 'context_publisher'
require 'context_event_logger'
require 'hashing'
require 'type_utils'

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

# Events the SDK logged since `from`.
def events_since(collector, from)
  collector.events[from..-1] || []
end

def sdk_error(status, e, collector, from)
  halt status, { error: e.message, events: collector ? events_since(collector, from) : [] }.to_json
end

$contexts = {}
$payload_store = {}

def body_json
  request.body.rewind
  raw = request.body.read
  raw.empty? ? {} : JSON.parse(raw, symbolize_names: true)
end

# Runs one SDK call on a stored context and returns its result together with
# the events the SDK logged during the call, on success and on error.
def with_context(error_status = 400)
  content_type :json
  ctx_data = $contexts[params['context_id']]
  halt 404, { error: 'Context not found', events: [] }.to_json unless ctx_data
  collector = ctx_data[:eventCollector]
  from = collector.events.length
  begin
    result = yield ctx_data[:context], ctx_data
    { result: result, events: events_since(collector, from) }.to_json
  rescue => e
    sdk_error(error_status, e, collector, from)
  end
end

def sdk_client(endpoint, api_key: 'dummy', application: 'test', environment: 'test', max_retries: nil)
  client_config = ClientConfig.new
  client_config.endpoint = endpoint
  client_config.api_key = api_key
  client_config.application = application
  client_config.environment = environment
  client_config.max_retries = max_retries unless max_retries.nil?
  Client.create(client_config)
end

def register(context, collector, publisher)
  context_id = "ctx-#{SecureRandom.uuid}"
  $contexts[context_id] = { context: context, eventCollector: collector, publisher: publisher }
  {
    result: {
      contextId: context_id,
      ready: context.ready?,
      failed: context.failed? || false,
      finalized: context.closed?
    },
    events: collector.events.dup
  }.to_json
end

get '/health' do
  content_type :json
  { status: 'healthy', sdk: 'ruby', version: '1.0.0' }.to_json
end

get '/capabilities' do
  content_type :json
  { diagnostics: true,
    diagnosticsUtf8: true,
    diagnosticsBase64: true,
    diagnosticsTypeChecks: true,
    diagnosticsIsPromise: false,
    attrsSeq: true,
    publishFail: true,
    variableKeysMap: true,
    globalCustomFieldKeys: true,
    getUnits: true,
    getAttributes: true,
    readyError: true }.to_json
end

put '/context_payload/:payload_id' do
  $payload_store[params['payload_id']] = body_json[:data] || { experiments: [] }
  content_type :json
  { success: true }.to_json
end

get '/context_payload/:payload_id/context' do
  # A never-stored payload id is a missing collector payload: answer 404 so the
  # SDK's own fetch fails the context.
  content_type :json
  data = $payload_store[params['payload_id']]
  halt 404, { error: 'Payload not found' }.to_json if data.nil?
  data.to_json
end

post '/context' do
  req_data = body_json
  content_type :json
  collector = EventCollector.new

  context_config = ContextConfig.new
  context_config.units = req_data[:units].transform_keys(&:to_sym)
  context_config.publish_delay = -1
  context_config.refresh_interval = 0

  if req_data[:mode] == 'e2e'
    env = %w[ENDPOINT API_KEY APPLICATION ENVIRONMENT].map { |k| ENV["ABSMARTLY_E2E_#{k}"] }
    halt 501, { error: 'e2e mode not configured' }.to_json if env.any?(&:nil?)

    sdk_config = ABSmartlyConfig.new
    sdk_config.client = sdk_client(env[0], api_key: env[1], application: env[2], environment: env[3])
    sdk_config.context_event_logger = collector
    context = ABSmartly.new(sdk_config).create_context(context_config)
    (req_data[:attributes] || {}).each { |name, value| context.set_attribute(name.to_s, value) }
    return register(context, collector, nil)
  end

  publisher = CustomPublisher.new(collector)
  sdk_config = ABSmartlyConfig.new
  sdk_config.context_event_handler = publisher
  sdk_config.context_event_logger = collector
  throttle = ((req_data[:options] || {})[:payloadThrottle] || 0).to_i

  if req_data[:data]
    # Pre-fetched data goes through the SDK's own deserializer.
    sdk_config.client = sdk_client('http://unused')
    raw = req_data[:data].to_json
    data = DefaultContextDataDeserializer.new.deserialize(raw, 0, raw.size)
    context = ABSmartly.new(sdk_config).create_context_with(context_config,
                                                            OpenStruct.new(success?: true, data_future: data))
  elsif req_data[:failLoad]
    # A real fetch against a closed port: the SDK's client fails the way a
    # network failure does.
    sdk_config.client = sdk_client('http://127.0.0.1:1', max_retries: 0)
    context = ABSmartly.new(sdk_config).create_context(context_config)
  else
    client = sdk_client(translate_endpoint(req_data[:endpoint]) || 'http://dummy')
    sdk_config.client = client
    if throttle > 0
      sdk_config.context_data_provider = DeferredDataProvider.new(client, throttle)
      context = ABSmartly.new(sdk_config).create_context_async(context_config)
    else
      context = ABSmartly.new(sdk_config).create_context(context_config)
    end
  end

  register(context, collector, publisher)
end

post('/context/:context_id/treatment') { r = body_json; with_context { |c| c.treatment(r[:experimentName]) } }
post('/context/:context_id/peek') { r = body_json; with_context { |c| c.peek_treatment(r[:experimentName]) } }
post('/context/:context_id/track') { r = body_json; with_context { |c| c.track(r[:goalName], r[:properties]); nil } }
post('/context/:context_id/attribute') { r = body_json; with_context { |c| c.set_attribute(r[:name], r[:value]); nil } }

%w[override setOverride].each do |route|
  post("/context/:context_id/#{route}") { r = body_json; with_context { |c| c.set_override(r[:experimentName], r[:variant]); nil } }
end
%w[customAssignment setCustomAssignment].each do |route|
  post("/context/:context_id/#{route}") { r = body_json; with_context { |c| c.set_custom_assignment(r[:experimentName], r[:variant]); nil } }
end

get('/context/:context_id/pending') { with_context { |c| c.pending_count } }
get('/context/:context_id/isFinalized') { with_context { |c| c.closed? } }
get('/context/:context_id/isReady') { with_context { |c| c.ready? } }
get('/context/:context_id/isFailed') { with_context { |c| c.failed? || false } }
get('/context/:context_id/experiments') { with_context { |c| c.experiments } }

post('/context/:context_id/getUnits') { with_context { |c| c.get_units.transform_keys(&:to_s) } }
post('/context/:context_id/getAttributes') { with_context { |c| c.get_attributes } }
post('/context/:context_id/readyError') do
  with_context do |c|
    error = c.ready_error
    error ? { isError: true, message: error.to_s } : nil
  end
end
post('/context/:context_id/variableKeysMap') { with_context { |c| c.variable_keys.transform_keys(&:to_s) } }
post('/context/:context_id/variableKeys') { with_context { |c| c.variable_keys.keys.map(&:to_s) } }
post('/context/:context_id/globalCustomFieldKeys') { with_context { |c| c.custom_field_keys } }
post('/context/:context_id/customFieldKeys') { with_context { |c| c.custom_field_keys } }

post('/context/:context_id/publishFail') do
  with_context { |_c, d| d[:publisher].should_fail = true; nil }
end
post('/context/:context_id/publish') { with_context(500) { |c| c.publish; nil } }
post('/context/:context_id/finalize') { with_context(500) { |c| c.finalize; nil } }
post('/context/:context_id/refresh') { with_context(500) { |c| c.refresh; nil } }

post('/context/:context_id/setUnit') { r = body_json; with_context { |c| c.set_unit(r[:unitType].to_sym, r[:uid]); nil } }
post('/context/:context_id/getUnit') { r = body_json; with_context { |c| c.get_unit(r[:unitType].to_sym) } }
post('/context/:context_id/getAttribute') { r = body_json; with_context { |c| c.get_attribute(r[:name]) } }
post('/context/:context_id/variableValue') { r = body_json; with_context { |c| c.variable_value(r[:key], r[:defaultValue]) } }
post('/context/:context_id/peekVariableValue') { r = body_json; with_context { |c| c.peek_variable_value(r[:key], r[:defaultValue]) } }
post('/context/:context_id/customFieldValue') { r = body_json; with_context { |c| c.custom_field_value(r[:experimentName], r[:fieldName]) } }
post('/context/:context_id/customFieldValueType') { r = body_json; with_context { |c| c.custom_field_type(r[:experimentName], r[:fieldName]) } }

delete '/context/:context_id' do
  $contexts.delete(params['context_id'])
  content_type :json
  { result: 'deleted' }.to_json
end

post '/diagnostic' do
  content_type :json
  req_data = body_json
  value = req_data[:value]

  result = case req_data[:operation]
           when 'hashUnit' then Hashing.hash_unit(value)
           when 'base64UrlNoPadding' then Hashing.base64_url_no_padding(Hashing.to_utf8(value))
           when 'utf8Bytes' then Hashing.to_utf8(value).bytes
           when 'isObject' then TypeUtils.object?(value)
           when 'isNumeric' then TypeUtils.numeric?(value)
           else
             halt 400, { error: "Unsupported diagnostic operation: #{req_data[:operation]}", events: [] }.to_json
           end

  { result: result, events: [] }.to_json
rescue => e
  halt 500, { error: e.message, events: [] }.to_json
end
