# Behavioral self-test for the `switchback` wrapper capability (R33, cluster-defined switchbacks),
# a port of java-wrapper/SwitchbackSelfTest.java (same payload, ids and expected values). It runs a
# sample of the platform golden vectors through the SDK's own assigner, then serves an R33 payload
# deserialized by the SDK's DefaultContextDataDeserializer through a real Context on the same
# clock-free grid the scenarios use, and requires every treatment and the complete exposure record.
#
# Run once per process and cached. Fails closed: an SDK without switchback serving (no assigner, a
# Phase 1E model, a hashed or bucketed key, a lost cluster order) makes the capability false.
module SwitchbackSelfTest
  PREFIX = '[switchback probe]'.freeze
  # With a 400,000 h period "now" stays in period 0 until 2069.
  PERIOD_MS = 400_000 * 3_600_000
  ANCHOR_PERIOD_0 = 1_704_067_200_000
  # hash_unit("serving-9"), as published in PublishEvent#units.
  SERVING_UNIT_HASH = 'r5uxZRoi-JuM5yYJa0kmSQ'.freeze

  # [cluster_unit, period_index, seed, arm_count, expected_variant] from the golden vectors.
  ASSIGN_CASES = [
    ['market_1', '0', '0', 2, 0],
    ['market_1', '-9223372036854775808', '1', 2, 1],
    ['market_1', '9223372036854775807', '1', 2, 1],
    ['123456789', '0', '0', 2, 1],
    ['market_1', '10', '0', 3, 2],
    ['123456789', '-2', '-1', 3, 0],
    ['market_1', '5', '1', 10, 0],
    ['market_2', '-11', '-9223372036854775808', 10, 3],
    ['market_2', '9223372036854775807', '-9223372036854775808', 10, 2],
    ['market_1', '1', '9223372036854775807', 2, 1]
  ].freeze

  # [cluster_unit, cycle_index, seed, expected_permutation].
  PERMUTATION_CASES = [
    ['market_1', '0', '0', [0, 3, 7, 5, 8, 2, 6, 4, 1, 9]],
    ['market_1', '-9223372036854775808', '0', [1, 0]],
    ['market_1', '9223372036854775807', '1', [0, 1]],
    ['123456789', '0', '0', [2, 0, 1]]
  ].freeze

  EXPOSURE_FIELDS = %i[id name unit variant assigned eligible overridden full_on custom audience_mismatch
                       rule_override].freeze

  ExposureLog = Struct.new(:entries) do
    def handle_event(event_type, data)
      entries << data if event_type == ContextEventLogger::EVENT_TYPE::EXPOSURE && data
    end
  end

  RecordingPublisher = Class.new(ContextPublisher) do
    attr_reader :events

    def initialize
      @events = []
    end

    def publish(_context, event)
      @events << event
      self
    end
  end

  module_function

  def assigner
    defined?(Absmartly::Switchback::Assigner) ? Absmartly::Switchback::Assigner : nil
  end

  def assign(cluster_unit, period_index, seed, arm_count)
    assigner.assign(cluster_unit, period_index, seed, arm_count)
  end

  def permutation(cluster_unit, cycle_index, seed, arm_count)
    assigner.fisher_yates_permutation(arm_count, assigner.base_hash(cluster_unit, cycle_index, seed))
  end

  def check_golden_sample
    raise 'linked ruby-sdk has no switchback assigner' if assigner.nil?

    ASSIGN_CASES.each do |unit, period, seed, arms, expected|
      actual = assign(unit, Integer(period, 10), Integer(seed, 10), arms)
      raise "golden assign FAILED for #{[unit, period, seed, arms].inspect}: got #{actual}" unless actual == expected
    end
    PERMUTATION_CASES.each do |unit, cycle, seed, expected|
      actual = permutation(unit, Integer(cycle, 10), Integer(seed, 10), expected.length)
      raise "golden permutation FAILED for #{[unit, cycle, seed].inspect}: got #{actual}" unless actual == expected
    end
  end

  def base(id, name, seed_hi, seed_lo, arms)
    {
      id: id, name: name, iteration: 1, unitType: 'unit_serving', seedHi: seed_hi, seedLo: seed_lo,
      split: Array.new(arms) { 1.0 / arms }, trafficSeedHi: 1, trafficSeedLo: 2, trafficSplit: [0, 1],
      fullOnVariant: 0, applications: [{ name: 'website' }],
      variants: Array.new(arms) { |i| { name: ('A'.ord + i).chr, config: nil } }, audienceStrict: false
    }
  end

  def switchback(id, name, seed_hi, seed_lo, arms, clusters)
    experiment = base(id, name, seed_hi, seed_lo, arms).merge(randomizationType: 'switchback')
    unless clusters.nil?
      experiment[:switchbackClusters] = clusters.to_h
      experiment[:unitType] = clusters[0][0]
    end
    experiment.merge(switchbackStartAt: ANCHOR_PERIOD_0, switchbackPeriodMs: PERIOD_MS,
                     switchbackDesignType: 'balanced_block', switchbackEstimand: 'global_average_treatment_effect')
  end

  # The unit baseline: seeds whose 3-arm unit draw for serving-9 is 1.
  def unit(id, name)
    base(id, name, -559_038_737, 1_234_567_890, 3)
  end

  # The expected 10-arm variants are platform golden vectors that differ from every wrong model for
  # both uids (hashed key, uid as key, hashed uid, a Phase 1E bucket, the sticky unit assignment):
  # assignSwitchback("market_1", 0, 42, 10) = 3 and assignSwitchback("123456789", 0, 42, 10) = 6.
  def probe_payload
    {
      experiments: [
        switchback(521, 'sb_agree', 0, 42, 10, [%w[unit_serving city], %w[unit_user profile/city]]),
        switchback(522, 'sb_declared_order', 0, 42, 10, [%w[unit_user profile/city], %w[unit_serving city]]),
        switchback(523, 'sb_disagree', 0, 42, 10, [%w[unit_serving city], %w[unit_user profile/zone]]),
        switchback(524, 'sb_whole_number', 0, 42, 10, [%w[unit_serving store_number]]),
        # switchbackClusterValues [] admits every value, like null.
        switchback(528, 'sb_allow_empty', 0, 42, 10, [%w[unit_serving city]]).merge(switchbackClusterValues: []),
        switchback(525, 'sb_value_filtered', 0, 1, 10, [%w[unit_serving city]])
          .merge(switchbackClusterValues: ['market_2']),
        # Only the abandoned Phase 1E fields: no cluster map, so the legacy ineligible control.
        switchback(526, 'sb_phase1e_only', 0, 1, 10, nil)
          .merge(switchbackClusterUnitType: 'unit_serving', switchbackClusterCount: 10, switchbackClusterAttribute: 'city'),
        switchback(513, 'sb_payload', 0, 1, 3, [%w[unit_serving city]]),
        # A float never keys a cluster: 3.0 gives no value (the key "3" would draw 0 too, so the
        # exposure is what tells them apart).
        switchback(530, 'sb_float_value', 0, 42, 10, [%w[unit_serving float_value]]),
        # The first declared unit has no value, so the second serves and is the exposure unit.
        switchback(531, 'sb_second_unit_serves', 0, 42, 10, [%w[unit_serving zone], %w[unit_user profile/zone]]),
        switchback(532, 'sb_rule', 0, 42, 10, [%w[unit_serving city]])
          .merge(assignmentRules: '{"rules":[{"type":"assign","environments":[],"variant":7}]}'),
        switchback(533, 'sb_traffic_miss', 0, 42, 10, [%w[unit_serving city]]).merge(trafficSplit: [1, 0]),
        unit(514, 'sb_unit_baseline'),
        unit(527, 'sb_unit_with_fields').merge(
          randomizationType: 'unit', switchbackClusters: { unit_serving: 'city' }, switchbackClusterValues: ['market_1'],
          switchbackStartAt: ANCHOR_PERIOD_0, switchbackPeriodMs: PERIOD_MS, switchbackDesignType: 'balanced_block'
        )
      ]
    }
  end

  def exposure(id, name, unit, variant, **flags)
    { id: id, name: name, unit: unit, variant: variant, assigned: true, eligible: true, overridden: false,
      full_on: false, custom: false, audience_mismatch: false, rule_override: false }.merge(flags)
  end

  def record(exposure)
    EXPOSURE_FIELDS.to_h { |field| [field, exposure.public_send(field)] }
  end

  def expect_served(context, log, check, name, treatment, *exposures)
    before = log.entries.length
    actual = context.treatment(name)
    emitted = log.entries[before..]
    return if actual == treatment && emitted.map { |e| record(e) } == exposures && emitted.all? { |e| e.exposed_at.to_i > 0 }

    raise "check #{check} FAILED: expected treatment=#{treatment} exposures=#{exposures.to_json}, got " \
          "treatment=#{actual} exposures=#{emitted.map { |e| record(e) }.to_json}"
  end

  def publish_one(context, publisher)
    before = publisher.events.length
    context.publish
    raise 'publish did not produce exactly one event' unless publisher.events.length == before + 1

    JSON.parse(DefaultContextEventSerializer.new.serialize(publisher.events[before]))
  end

  def shape(json)
    case json
    when Hash then json.keys.sort.to_h { |key| [key, shape(json[key])] }
    when Array then json.map { |item| shape(item) }
    else json.class.name
    end
  end

  def check_publish(json, expected)
    exposures = json['exposures']
    raise "publish exposures #{exposures.inspect}" unless json['hashed'] == true && exposures.is_a?(Array) && exposures.length == 1

    published = exposures[0]
    wanted = { 'id' => expected[:id], 'name' => expected[:name], 'unit' => expected[:unit], 'variant' => expected[:variant],
               'assigned' => true, 'eligible' => true, 'overridden' => false, 'fullOn' => false, 'custom' => false,
               'audienceMismatch' => false, 'ruleOverride' => false }
    raise "publish exposure #{published.inspect}, expected #{wanted.inspect}" unless published.reject { |k, _| k == 'exposedAt' } == wanted
    raise "publish exposedAt #{published['exposedAt'].inspect}" unless published['exposedAt'].is_a?(Integer) && published['exposedAt'] > 0
    return if json['units'].any? { |u| u['type'] == 'unit_serving' && u['uid'] == SERVING_UNIT_HASH }

    raise "publish units lack the hashed serving unit: #{json['units'].inspect}"
  end

  def check_context_serving
    raw = JSON.generate(probe_payload)
    data = DefaultContextDataDeserializer.new.deserialize(raw, 0, raw.bytesize)
    declared = data.experiments.find { |e| e.name == 'sb_declared_order' }
    unless declared.respond_to?(:switchback_clusters) && !declared.respond_to?(:switchback_cluster_count)
      raise 'linked ruby-sdk has no R33 switchback wire model'
    end
    order = declared.switchback_clusters.keys.map(&:to_s)
    raise "switchbackClusters lost its declared order: #{order}" unless order == %w[unit_user unit_serving]

    log = ExposureLog.new([])
    publisher = RecordingPublisher.new
    client_config = ClientConfig.new
    client_config.endpoint = 'http://unused'
    client_config.api_key = 'dummy'
    client_config.application = 'test'
    client_config.environment = 'test'
    sdk_config = ABSmartlyConfig.new
    sdk_config.client = Client.create(client_config)
    sdk_config.context_event_handler = publisher
    sdk_config.context_event_logger = log
    context_config = ContextConfig.new
    # unit_user is set first, so the declared order, not the context order, must pick the serving unit.
    context_config.units = { unit_user: 'probe-user', unit_serving: 'serving-9' }
    context_config.publish_delay = -1
    context_config.refresh_interval = 0
    context = ABSmartly.new(sdk_config).create_context_with(context_config, OpenStruct.new(success?: true, data_future: data))
    raise 'context not ready' unless context.ready? && !context.failed?

    context.set_attribute('city', 'market_1')
    context.set_attribute('profile', { 'city' => 'market_1', 'zone' => 'market_2' })
    context.set_attribute('store_number', 123_456_789)
    context.set_attribute('float_value', 3.0)

    expect_served(context, log, 'agreement', 'sb_agree', 3, exposure(521, 'sb_agree', 'unit_serving', 3))
    expect_served(context, log, 'agreement repeat', 'sb_agree', 3)
    expect_served(context, log, 'declared order', 'sb_declared_order', 3, exposure(522, 'sb_declared_order', 'unit_user', 3))
    expect_served(context, log, 'disagreement', 'sb_disagree', 0)
    expect_served(context, log, 'whole number', 'sb_whole_number', 6, exposure(524, 'sb_whole_number', 'unit_serving', 6))
    expect_served(context, log, 'value filtered out', 'sb_value_filtered', 0)
    expect_served(context, log, 'empty allow-list admits all', 'sb_allow_empty', 3,
                  exposure(528, 'sb_allow_empty', 'unit_serving', 3))
    expect_served(context, log, 'Phase 1E fields only', 'sb_phase1e_only', 0)
    expect_served(context, log, 'float value', 'sb_float_value', 0)
    # profile/zone is market_2: assignSwitchback("market_2", 0, 42, 10) = 9.
    expect_served(context, log, 'second unit serves', 'sb_second_unit_serves', 9,
                  exposure(531, 'sb_second_unit_serves', 'unit_user', 9))
    expect_served(context, log, 'assignment rule', 'sb_rule', 7,
                  exposure(532, 'sb_rule', 'unit_serving', 7, assigned: false, rule_override: true))
    expect_served(context, log, 'traffic miss', 'sb_traffic_miss', 0,
                  exposure(533, 'sb_traffic_miss', 'unit_serving', 0, eligible: false))

    context.publish
    expect_served(context, log, 'unit baseline', 'sb_unit_baseline', 1, exposure(514, 'sb_unit_baseline', 'unit_serving', 1))
    baseline = publish_one(context, publisher)
    expect_served(context, log, 'payload', 'sb_payload', 1, exposure(513, 'sb_payload', 'unit_serving', 1))
    served = publish_one(context, publisher)
    raise "publish shape differs: #{shape(served)} vs #{shape(baseline)}" unless shape(served) == shape(baseline)

    check_publish(baseline, exposure(514, 'sb_unit_baseline', 'unit_serving', 1))
    check_publish(served, exposure(513, 'sb_payload', 'unit_serving', 1))

    expect_served(context, log, 'unit inertness', 'sb_unit_with_fields', 1,
                  exposure(527, 'sb_unit_with_fields', 'unit_serving', 1))
    with_fields = publish_one(context, publisher)
    normalize = lambda do |json|
      json.reject { |k, _| k == 'publishedAt' }
          .merge('exposures' => json['exposures'].map { |e| e.merge('id' => 0, 'name' => '', 'exposedAt' => 0) })
    end
    raise "unit inertness: #{with_fields} differs from #{baseline}" unless normalize.call(with_fields) == normalize.call(baseline)

    context.close
    true
  end

  def switchback?
    return @switchback if defined?(@switchback)

    @switchback = begin
      check_golden_sample
      check_context_serving
      warn "#{PREFIX} behavioral self-test PASSED"
      true
    rescue StandardError, ScriptError => e
      warn "#{PREFIX} behavioral self-test FAILED: #{e.message}"
      false
    end
  end
end
