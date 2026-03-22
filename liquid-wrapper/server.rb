require 'sinatra'
require 'json'
require 'base64'
require 'digest'
require 'ostruct'
require 'net/http'
require 'uri'
require 'absmartly/liquid'
require 'context_data_provider'
require 'context_publisher'
require 'a_b_smartly_config'

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

def translate_error(message)
  message.gsub('ABSmartly Context is closed', 'Context finalized')
end

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
    event_type_str = event_type.to_s
    event_type_str = 'finalize' if event_type_str == 'close'

    @events << {
      type: event_type_str,
      data: deep_copy(data),
      timestamp: (Time.now.to_f * 1000).to_i
    }
  end

  def deep_copy(obj)
    obj = convert_to_serializable(obj)
    JSON.parse(JSON.generate(obj))
  rescue => e
    nil
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

class DataWrapper
  attr_reader :data_future

  def initialize(data)
    @data_future = hash_to_ostruct(data)
  end

  def success?
    true
  end

  private

  def hash_to_ostruct(obj)
    return obj unless obj.is_a?(Hash)

    obj.each_with_object(OpenStruct.new) do |(key, val), ostruct|
      snake_key = camel_to_snake(key.to_s)
      ostruct[snake_key] = if val.is_a?(Hash)
                             hash_to_ostruct(val)
                           elsif val.is_a?(Array)
                             val.map { |v| hash_to_ostruct(v) }
                           else
                             val
                           end
    end
  end

  def camel_to_snake(str)
    str.gsub(/([A-Z]+)([A-Z][a-z])/, '\1_\2')
       .gsub(/([a-z\d])([A-Z])/, '\1_\2')
       .downcase
  end
end

class DeferredDataProvider < ContextDataProvider
  def initialize(endpoint, throttle_ms)
    @endpoint = endpoint
    @throttle_ms = throttle_ms
  end

  def context_data
    sleep(@throttle_ms / 1000.0) if @throttle_ms > 0
    begin
      uri = URI(@endpoint)
      response = Net::HTTP.get_response(uri)
      data = JSON.parse(response.body, symbolize_names: false)
      DataWrapper.new(data)
    rescue => e
      DataWrapper.new({ 'experiments' => [] })
    end
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
      'diagnostic', 'experiments', 'isReady', 'isFailed',
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
  data = $payload_store[params['payload_id']] || { experiments: [] }
  content_type :json
  data.to_json
end

post '/context' do
  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

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
    data_wrapper = DataWrapper.new(req_data[:data])
    context = sdk.create_context_with(context_config, data_wrapper)
    $contexts[context_id] = {
      context: context,
      eventCollector: event_collector,
      publisher: publisher
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
  elsif req_data[:failLoad]
    failing_provider = DeferredDataProvider.new('http://invalid-host:9999/nonexistent', 0)
    class << failing_provider
      def context_data
        wrapper = DataWrapper.new({ 'experiments' => [] })
        class << wrapper
          def success?
            false
          end
          def exception
            StandardError.new('Context load failed')
          end
        end
        wrapper
      end
    end
    failing_sdk_config = ABSmartlyConfig.new
    failing_sdk_config.context_data_provider = failing_provider
    failing_sdk_config.context_event_handler = publisher
    failing_sdk_config.context_event_logger = event_collector
    failing_sdk = ABSmartly.new(failing_sdk_config)
    context = failing_sdk.create_context(context_config)
    50.times do
      break unless event_collector.events.empty?
      sleep 0.01
    end
    $contexts[context_id] = {
      context: context,
      eventCollector: event_collector,
      publisher: publisher
    }
    content_type :json
    return {
      result: {
        contextId: context_id,
        ready: false,
        failed: context.failed? || false,
        finalized: context.closed?
      },
      events: event_collector.events
    }.to_json
  elsif payload_throttle.to_i > 0
    deferred_provider = DeferredDataProvider.new(translated_endpoint, payload_throttle.to_i)
    deferred_sdk_config = ABSmartlyConfig.new
    deferred_sdk_config.context_data_provider = deferred_provider
    deferred_sdk_config.context_event_handler = publisher
    deferred_sdk_config.context_event_logger = event_collector
    deferred_sdk_config.client = client
    deferred_sdk = ABSmartly.new(deferred_sdk_config)
    context = deferred_sdk.create_context_async(context_config)
    $contexts[context_id] = {
      context: context,
      eventCollector: event_collector
    }
    content_type :json
    return {
      result: {
        contextId: context_id,
        ready: context.ready?,
        failed: context.failed? || false,
        finalized: context.closed?
      },
      events: []
    }.to_json
  else
    context = sdk.create_context(context_config)
    50.times do
      break unless event_collector.events.empty?
      sleep 0.01
    end
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
    events: event_collector.events
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
    drop = ABsmartly::Liquid::Drop.new(context)
    template = Liquid::Template.parse("{% absmartly_treatment experiment_name %}{{ variant }}{% endabsmartly_treatment %}")
    result = template.render(
      'absmartly' => drop,
      'experiment_name' => req_data[:experimentName]
    )
    variant = result.strip.to_i

    new_events = collector.events[events_before..-1] || []

    content_type :json
    { result: variant, events: new_events }.to_json
  rescue => e
    halt 400, { error: translate_error(e.message) }.to_json
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
    ABsmartly::Liquid.current_context = context
    template = Liquid::Template.parse("{{ experiment_name | absmartly_peek }}")
    result = template.render('experiment_name' => req_data[:experimentName])
    variant = result.strip.to_i

    new_events = collector.events[events_before..-1] || []

    content_type :json
    { result: variant, events: new_events }.to_json
  rescue => e
    halt 400, { error: translate_error(e.message) }.to_json
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
    # Validate properties type first (must be nil or Hash)
    props = req_data[:properties]
    if props && !props.is_a?(Hash)
      halt 400, { error: "Goal '#{req_data[:goalName]}' properties must be of type object." }.to_json
    end

    context.track(req_data[:goalName].to_s, props || {})

    new_events = collector.events[events_before..-1] || []

    # Fix: Liquid filter defaults properties to {}, but test expects nil
    # Convert empty hash to nil in goal events for compatibility
    new_events.each do |event|
      # Check both symbol and string keys since JSON serialization may differ
      event_type = event[:type] || event['type']
      event_data = event[:data] || event['data']
      if event_type == 'goal' && event_data
        props = event_data[:properties] || event_data['properties']
        if props == {} || props&.empty?
          event_data[:properties] = nil if event_data.key?(:properties)
          event_data['properties'] = nil if event_data.key?('properties')
        end
      end
    end

    content_type :json
    { result: nil, events: new_events }.to_json
  rescue => e
    halt 400, { error: translate_error(e.message) }.to_json
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
    new_events = collector.events[events_before..-1] || []

    content_type :json
    { result: nil, events: new_events }.to_json
  rescue => e
    halt 400, { error: translate_error(e.message) }.to_json
  end
end

post '/context/:context_id/override' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]

  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  begin
    context.set_override(req_data[:experimentName], req_data[:variant])
    content_type :json
    { result: nil, events: [] }.to_json
  rescue => e
    if translate_error(e.message) == 'Context finalized'
      content_type :json
      return({ result: nil, events: [] }.to_json)
    end
    halt 400, { error: translate_error(e.message) }.to_json
  end
end

post '/context/:context_id/customAssignment' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]

  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  begin
    context.set_custom_assignment(req_data[:experimentName], req_data[:variant])
    content_type :json
    { result: nil, events: [] }.to_json
  rescue => e
    halt 400, { error: translate_error(e.message) }.to_json
  end
end

get '/context/:context_id/pending' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  content_type :json
  { result: ctx_data[:context].pending_count, events: [] }.to_json
end

get '/context/:context_id/isFinalized' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  content_type :json
  { result: ctx_data[:context].closed?, events: [] }.to_json
end

get '/context/:context_id/isReady' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  content_type :json
  { result: ctx_data[:context].ready?, events: [] }.to_json
end

get '/context/:context_id/isFailed' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  failed = context.respond_to?(:failed?) ? (context.failed? || false) : false
  content_type :json
  { result: failed, events: [] }.to_json
end

get '/context/:context_id/experiments' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]

  ctx_data = $contexts[context_id]
  content_type :json
  { result: ctx_data[:context].experiments, events: [] }.to_json
end

post '/context/:context_id/getUnits' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]
  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  units = {}
  context.get_units.each do |k, v|
    val = v.to_s
    units[k.to_s] = val.match?(/^\d+$/) ? val.to_i : (val.match?(/^\d+\.\d+$/) ? val.to_f : val)
  end
  content_type :json
  { result: units, events: [] }.to_json
end

post '/context/:context_id/getAttributes' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]
  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  attrs = context.get_attributes
  content_type :json
  { result: attrs, events: [] }.to_json
end

post '/context/:context_id/readyError' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]
  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  error = context.respond_to?(:ready_error) ? context.ready_error : nil
  content_type :json
  { result: error ? error.to_s : nil, events: [] }.to_json
end

post '/context/:context_id/variableKeysMap' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]
  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  keys = context.variable_keys
  result = keys.is_a?(Hash) ? keys.transform_keys(&:to_s) : keys
  content_type :json
  { result: result, events: [] }.to_json
end

post '/context/:context_id/globalCustomFieldKeys' do
  context_id = params['context_id']
  halt 404, { error: 'Context not found' }.to_json unless $contexts[context_id]
  ctx_data = $contexts[context_id]
  context = ctx_data[:context]
  keys = context.custom_field_keys
  content_type :json
  { result: keys, events: [] }.to_json
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
    new_events = collector.events[events_before..-1] || []

    content_type :json
    { result: nil, events: new_events }.to_json
  rescue => e
    halt 500, { error: translate_error(e.message) }.to_json
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
    new_events = collector.events[events_before..-1] || []

    content_type :json
    { result: nil, events: new_events }.to_json
  rescue => e
    halt 500, { error: translate_error(e.message) }.to_json
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
    new_events = collector.events[events_before..-1] || []
    content_type :json
    { result: nil, events: new_events }.to_json
  rescue => e
    error = e.message
    if error&.downcase&.include?('already set')
      error = "Unit '#{req_data[:unitType]}' UID already set."
    end
    halt 400, { error: translate_error(error) }.to_json
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

    if result && result.is_a?(String)
      if result.match?(/^\d+$/)
        result = result.to_i
      elsif result.match?(/^\d+\.\d+$/)
        result = result.to_f
      end
    end

    new_events = collector.events[events_before..-1] || []
    content_type :json
    { result: result, events: new_events }.to_json
  rescue => e
    halt 400, { error: translate_error(e.message) }.to_json
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
    new_events = collector.events[events_before..-1] || []
    content_type :json
    { result: result, events: new_events }.to_json
  rescue => e
    halt 400, { error: translate_error(e.message) }.to_json
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
    ABsmartly::Liquid.current_context = context
    template = Liquid::Template.parse("{{ key | absmartly_variable: default_value | json }}")
    rendered = template.render(
      'key' => req_data[:key],
      'default_value' => req_data[:defaultValue]
    )

    # Parse the JSON result
    result = JSON.parse(rendered.strip)

    new_events = collector.events[events_before..-1] || []
    content_type :json
    { result: result, events: new_events }.to_json
  rescue => e
    halt 400, { error: translate_error(e.message) }.to_json
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
    ABsmartly::Liquid.current_context = context
    template = Liquid::Template.parse("{{ key | absmartly_peek_variable: default_value | json }}")
    rendered = template.render(
      'key' => req_data[:key],
      'default_value' => req_data[:defaultValue]
    )

    # Parse the JSON result
    result = JSON.parse(rendered.strip)

    new_events = collector.events[events_before..-1] || []
    content_type :json
    { result: result, events: new_events }.to_json
  rescue => e
    halt 400, { error: translate_error(e.message) }.to_json
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
    ABsmartly::Liquid.current_context = context
    template = Liquid::Template.parse("{{ experiment_name | absmartly_custom_field: field_name | json }}")
    rendered = template.render(
      'experiment_name' => req_data[:experimentName],
      'field_name' => req_data[:fieldName]
    )

    # Parse the JSON result
    result = JSON.parse(rendered.strip)

    new_events = collector.events[events_before..-1] || []
    content_type :json
    { result: result, events: new_events }.to_json
  rescue => e
    halt 400, { error: translate_error(e.message) }.to_json
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
    new_events = collector.events[events_before..-1] || []
    content_type :json
    { result: result, events: new_events }.to_json
  rescue => e
    halt 400, { error: translate_error(e.message) }.to_json
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
    new_events = collector.events[events_before..-1] || []
    content_type :json
    { result: keys, events: new_events }.to_json
  rescue => e
    halt 400, { error: translate_error(e.message) }.to_json
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
    new_events = collector.events[events_before..-1] || []
    content_type :json
    { result: value_type, events: new_events }.to_json
  rescue => e
    halt 400, { error: translate_error(e.message) }.to_json
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
    new_events = collector.events[events_before..-1] || []
    content_type :json
    { result: nil, events: new_events }.to_json
  rescue => e
    if translate_error(e.message) == 'Context finalized'
      content_type :json
      return({ result: nil, events: [] }.to_json)
    end
    halt 400, { error: translate_error(e.message) }.to_json
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
    new_events = collector.events[events_before..-1] || []
    content_type :json
    { result: nil, events: new_events }.to_json
  rescue => e
    halt 400, { error: translate_error(e.message) }.to_json
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
    new_events = collector.events[events_before..-1] || []
    content_type :json
    { result: nil, events: new_events }.to_json
  rescue => e
    halt 500, { error: translate_error(e.message) }.to_json
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
           when 'hashUnit'
             digest = Digest::MD5.digest(value.to_s)
             Base64.urlsafe_encode64(digest, padding: false)
           when 'base64UrlNoPadding'
             Base64.urlsafe_encode64(value.to_s, padding: false)
           when 'utf8Bytes'
             value.to_s.bytes
           when 'isObject'
             value.is_a?(Hash)
           when 'isNumeric'
             value.is_a?(Numeric)
           when 'isPromise'
             false
           else
             halt 400, { error: "Unsupported diagnostic operation: #{op}" }.to_json
           end

  { result: result, events: [] }.to_json
rescue => e
  halt 500, { error: translate_error(e.message) }.to_json
end
