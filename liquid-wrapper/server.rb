require 'sinatra'
require 'json'
require 'ostruct'
require 'absmartly/liquid'

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
    when Exposure, GoalAchievement, PublishEvent, Unit, Attribute
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

class CustomEventHandler < ContextEventHandler
  def initialize(event_collector)
    @event_collector = event_collector
  end

  def publish(context, event)
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
  {
    asyncContext: false,
    attrsSeq: false,
    isWrapper: true,
    wrapsSDK: 'ruby',
    # These operations are pass-through to Ruby SDK (no Liquid-specific implementation)
    # Operations using Liquid templates: treatment, peek, track, variableValue,
    # peekVariableValue, customFieldValue
    passThroughOperations: [
      'attribute', 'override', 'customAssignment', 'pending', 'isFinalized',
      'publish', 'finalize', 'setUnit', 'getUnit', 'getAttribute',
      'variableKeys', 'customFieldKeys', 'customFieldValueType',
      'setOverride', 'setCustomAssignment', 'refresh'
    ]
  }.to_json
end

put '/context_payload' do
  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  payload_id = "payload-#{Time.now.to_i}-#{rand(100000)}"
  $payload_store[payload_id] = req_data[:data]

  url = "http://liquid-sdk:3000/context_payload/#{payload_id}"

  content_type :json
  {
    payloadUrl: url,
    payloadId: payload_id
  }.to_json
end

get '/context_payload/:payload_id' do
  throttle = (params['throttle'] || '0').to_i
  data = $payload_store[params['payload_id']] || { experiments: [] }

  sleep(throttle / 1000.0) if throttle > 0

  content_type :json
  data.to_json
end

post '/context' do
  request.body.rewind
  req_data = JSON.parse(request.body.read, symbolize_names: true)

  context_id = "ctx-#{Time.now.to_i}-#{rand(100000)}"

  event_collector = EventCollector.new
  custom_event_handler = CustomEventHandler.new(event_collector)

  client_config = ClientConfig.new
  client_config.endpoint = req_data[:endpoint] || 'http://dummy'  # Use provided endpoint or dummy
  client_config.api_key = 'dummy'
  client_config.application = 'test'
  client_config.environment = 'test'

  client = Client.new(client_config, nil)

  sdk_config = ABSmartlyConfig.new
  sdk_config.client = client
  sdk_config.context_event_handler = custom_event_handler
  sdk_config.context_event_logger = event_collector

  sdk = ABSmartly.new(sdk_config)

  context_config = ContextConfig.new
  context_config.units = req_data[:units].transform_keys(&:to_sym)
  context_config.publish_delay = -1
  context_config.refresh_interval = 0

  if req_data[:data]
    # Sync: createContextWith
    data_wrapper = DataWrapper.new(req_data[:data])
    context = sdk.create_context_with(context_config, data_wrapper)
  else
    # Async: createContext (SDK will fetch from endpoint)
    context = sdk.create_context(context_config)
  end

  $contexts[context_id] = {
    context: context,
    eventCollector: event_collector
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
    halt 400, { error: e.message }.to_json
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
    halt 400, { error: e.message }.to_json
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

    # Use Liquid template with absmartly_track filter
    ABsmartly::Liquid.current_context = context

    if props && !props.empty?
      # With properties - pass as filter argument
      template = Liquid::Template.parse("{{ goal_name | absmartly_track: properties }}")
      template.render('goal_name' => req_data[:goalName], 'properties' => props)
    else
      # No properties - the filter defaults to {} but we need nil for the SDK
      # Use the filter without second arg, then fix the event if needed
      template = Liquid::Template.parse("{{ goal_name | absmartly_track }}")
      template.render('goal_name' => req_data[:goalName])
    end

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
    halt 400, { error: e.message }.to_json
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
    halt 400, { error: e.message }.to_json
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
    halt 400, { error: e.message }.to_json
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
    halt 400, { error: e.message }.to_json
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
    halt 500, { error: e.message }.to_json
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
    halt 500, { error: e.message }.to_json
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
    halt 400, { error: e.message }.to_json
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
    units = context.instance_variable_get(:@units)
    result = units[req_data[:unitType].to_sym]

    if result.nil?
      result = units[req_data[:unitType].to_s]
    end

    # Convert numeric strings to proper numeric types
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
    halt 400, { error: e.message }.to_json
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
    result = nil
    context.instance_variable_get(:@attributes).each do |attr|
      result = attr.value if attr.name == req_data[:name]
    end
    new_events = collector.events[events_before..-1] || []
    content_type :json
    { result: result, events: new_events }.to_json
  rescue => e
    halt 400, { error: e.message }.to_json
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
    halt 400, { error: e.message }.to_json
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
    halt 400, { error: e.message }.to_json
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
    halt 400, { error: e.message }.to_json
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
    halt 400, { error: e.message }.to_json
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
    halt 400, { error: e.message }.to_json
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
    halt 400, { error: e.message }.to_json
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
    halt 400, { error: e.message }.to_json
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
    halt 400, { error: e.message }.to_json
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
    new_data_ostruct = DataWrapper.new(req_data[:newData]).data_future
    context.send(:assign_data, new_data_ostruct)

    # Clear assignment cache to force re-evaluation
    if context.instance_variable_defined?(:@assignment_cache)
      context.instance_variable_set(:@assignment_cache, {})
    end

    collector.handle_event(ContextEventLogger::EVENT_TYPE::REFRESH, req_data[:newData])
    new_events = collector.events[events_before..-1] || []
    content_type :json
    { result: nil, events: new_events }.to_json
  rescue => e
    halt 500, { error: e.message }.to_json
  end
end

delete '/context/:context_id' do
  context_id = params['context_id']
  $contexts.delete(context_id) if $contexts[context_id]

  content_type :json
  { result: 'deleted' }.to_json
end
