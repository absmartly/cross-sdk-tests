# Behavioral self-test for the `holdouts` / `holdout_arms` wrapper capabilities, a port of
# javascript-wrapper/holdoutSelfTest.js (same fixtures, ids and expected sequences). Each battery
# builds a real ContextData through the SDK's own DefaultContextDataDeserializer, creates a real
# Context, calls treatment() and requires BOTH the returned variant and the exact ordered
# id/name/variant sequence of exposure events that call logged.
#
# Run once per process and cached. Fails closed: any exception (an SDK build without holdout
# support, a thrown assertion, an unexpected shape) makes the capability report false.
module HoldoutSelfTest
  ExposureLog = Struct.new(:entries) do
    def handle_event(event_type, data)
      return unless event_type == ContextEventLogger::EVENT_TYPE::EXPOSURE && data

      entries << { id: data.id, name: data.name, variant: data.variant }
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

  HOLDOUTS_MUTEX = Mutex.new
  HOLDOUT_ARMS_MUTEX = Mutex.new

  module_function

  def experiment(id, name, unit_type, full_on_variant, variant_names, holdout_ids, split: [0.5, 0.5])
    {
      id: id, name: name, iteration: 1, unitType: unit_type, seedHi: 100, seedLo: 200, split: split,
      trafficSeedHi: 1, trafficSeedLo: 2, trafficSplit: [0, 1], fullOnVariant: full_on_variant,
      applications: [{ name: 'website' }], variants: variant_names.map { |n| { name: n, config: nil } },
      audience: nil, audienceStrict: false, customFieldValues: nil, holdoutIds: holdout_ids
    }
  end

  def holdout(id, name, unit_type, seed_hi, seed_lo, split, holdout_type)
    {
      id: id, name: name, iteration: 1, unitType: unit_type, seedHi: seed_hi, seedLo: seed_lo, split: split,
      trafficSeedHi: 0, trafficSeedLo: 0, trafficSplit: [0, 1], fullOnVariant: 0, applications: [],
      variants: split.each_index.map { |i| { name: ('A'.ord + i).chr, config: nil } },
      audience: nil, audienceStrict: false, customFieldValues: nil, holdoutType: holdout_type
    }
  end

  def context_for(payload, units)
    log = ExposureLog.new([])
    client_config = ClientConfig.new
    client_config.endpoint = 'http://unused'
    client_config.api_key = 'dummy'
    client_config.application = 'test'
    client_config.environment = 'test'
    sdk_config = ABSmartlyConfig.new
    sdk_config.client = Client.create(client_config)
    publisher = RecordingPublisher.new
    sdk_config.context_event_handler = publisher
    sdk_config.context_event_logger = log
    context_config = ContextConfig.new
    context_config.units = units
    context_config.publish_delay = -1
    context_config.refresh_interval = 0
    raw = JSON.generate(payload)
    data = DefaultContextDataDeserializer.new.deserialize(raw, 0, raw.size)
    context = ABSmartly.new(sdk_config).create_context_with(context_config,
                                                            OpenStruct.new(success?: true, data_future: data))
    raise 'context not ready' unless context.ready? && !context.failed?

    [context, log, publisher]
  end

  def verify(prefix, label, context, log, name, expected_treatment, expected_sequence)
    before = log.entries.length
    treatment = context.treatment(name)
    exposures = log.entries[before..]
    return if treatment == expected_treatment && exposures == expected_sequence

    raise "#{prefix} check #{label} FAILED: expected treatment=#{expected_treatment} with exposures " \
          "#{expected_sequence.to_json}, got treatment=#{treatment} exposures=#{exposures.to_json}"
  end

  # Mirrors scenarios 203-208 and 221 (see javascript-wrapper/holdoutSelfTest.js for the fixture notes).
  # Check C lists its holdoutIds high id first, so it also checks that holdouts expose in id order.
  def run_holdouts_battery
    held_out = 'session_id'
    held_out_uid = 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'
    not_held_out = 'session_id_b'
    not_held_out_uid = 'b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3'
    late = 'user_id'
    two = [0.1, 0.9]

    payload = {
      experiments: [
        experiment(301, 'probe_a_covered', held_out, 0, %w[A B], [401]),
        experiment(302, 'probe_b_covered', not_held_out, 0, %w[A B], [402]),
        experiment(304, 'probe_c_union', held_out, 0, %w[A B], [405, 404]),
        experiment(305, 'probe_d_covered', held_out, 0, %w[A B], [406]),
        experiment(306, 'probe_d_uncovered_sibling', held_out, 0, %w[A B], nil),
        experiment(307, 'probe_e_dangling_plus_valid', held_out, 0, %w[A B], [999, 407]),
        experiment(303, 'probe_f_fullon', held_out, 2, %w[A B C], [403]),
        experiment(308, 'probe_g_late_unit', late, 2, %w[A B C], [408], split: [0.5, 0.5, 0.0])
      ],
      holdouts: [
        holdout(401, 'probe_a_holdout', held_out, 13, 111, two, 'full'),
        holdout(402, 'probe_b_holdout', not_held_out, 13, 111, two, 'full'),
        holdout(404, 'probe_c_holdout_low', held_out, 1, 222, two, 'full'),
        holdout(405, 'probe_c_holdout_high', held_out, 13, 111, two, 'full'),
        holdout(406, 'probe_d_holdout', held_out, 13, 111, two, 'full'),
        holdout(407, 'probe_e_holdout', held_out, 13, 111, two, 'full'),
        holdout(403, 'probe_f_holdout', held_out, 13, 111, two, 'full'),
        holdout(408, 'probe_g_holdout', late, 1, 222, two, 'full')
      ]
    }
    context, log, publisher = context_for(payload, { held_out.to_sym => held_out_uid, not_held_out.to_sym => not_held_out_uid })
    p = '[holdouts probe]'

    verify(p, 'A (mirrors 203)', context, log, 'probe_a_covered', 0, [{ id: 401, name: 'probe_a_holdout', variant: 0 }])
    verify(p, 'B (mirrors 204)', context, log, 'probe_b_covered', 0,
           [{ id: 302, name: 'probe_b_covered', variant: 0 }, { id: 402, name: 'probe_b_holdout', variant: 1 }])
    verify(p, 'C (mirrors 205)', context, log, 'probe_c_union', 0,
           [{ id: 404, name: 'probe_c_holdout_low', variant: 1 }, { id: 405, name: 'probe_c_holdout_high', variant: 0 }])
    verify(p, 'D (mirrors 206, covered)', context, log, 'probe_d_covered', 0,
           [{ id: 406, name: 'probe_d_holdout', variant: 0 }])
    verify(p, 'D (mirrors 206, uncovered sibling)', context, log, 'probe_d_uncovered_sibling', 1,
           [{ id: 306, name: 'probe_d_uncovered_sibling', variant: 1 }])
    verify(p, 'E (mirrors 207)', context, log, 'probe_e_dangling_plus_valid', 0,
           [{ id: 407, name: 'probe_e_holdout', variant: 0 }])
    verify(p, 'F (mirrors 208)', context, log, 'probe_f_fullon', 0, [{ id: 403, name: 'probe_f_holdout', variant: 0 }])

    context.publish

    # G (mirrors 221): a pre-unit peek exposes nothing; once the unit is set, treatment publishes
    # both the experiment's and the holdout's exposure exactly once.
    before = log.entries.length
    peeked = context.peek_treatment('probe_g_late_unit')
    raise "#{p} check G (pre-unit peek) FAILED: treatment=#{peeked}" unless peeked == 2 && log.entries.length == before

    context.set_unit(late, held_out_uid)
    verify(p, 'G (mirrors 221, post-unit treatment)', context, log, 'probe_g_late_unit', 2,
           [{ id: 308, name: 'probe_g_late_unit', variant: 2 }, { id: 408, name: 'probe_g_holdout', variant: 1 }])
    before_publish = publisher.events.length
    context.publish
    batches = publisher.events[before_publish..]
    published = batches.flat_map { |event| event.exposures || [] }
                       .map { |e| { id: e.id, name: e.name, variant: e.variant } }
    expected = [{ id: 308, name: 'probe_g_late_unit', variant: 2 }, { id: 408, name: 'probe_g_holdout', variant: 1 }]
    raise "#{p} check G publish FAILED: #{published.inspect}" unless batches.length == 1 && published == expected

    true
  end

  # Mirrors scenarios 214-216; the same ids the java and javascript wrappers use.
  def run_holdout_arms_battery
    unit = 'session_id'
    three = [0.3, 0.3, 0.4]
    payload = {
      experiments: [
        experiment(601, 'arms_0_non_fullon', unit, 0, %w[A B], [621]),
        experiment(602, 'arms_0_fullon', unit, 2, %w[A B C], [621]),
        experiment(603, 'arms_1_non_fullon', unit, 0, %w[A B], [622]),
        experiment(604, 'arms_1_fullon', unit, 2, %w[A B C], [622]),
        experiment(605, 'arms_2_non_fullon', unit, 0, %w[A B], [623]),
        experiment(606, 'arms_2_fullon', unit, 2, %w[A B C], [623])
      ],
      holdouts: [
        holdout(621, 'arms_holdout_0', unit, 0, 1, three, 'all_full_on'),
        holdout(622, 'arms_holdout_1', unit, 0, 3, three, 'all_full_on'),
        holdout(623, 'arms_holdout_2', unit, 0, 0, three, 'all_full_on')
      ]
    }
    context, log = context_for(payload, { unit.to_sym => 'e791e240fcd3df7d238cfc285f475e8152fcc0ec' })
    p = '[holdout_arms probe]'

    verify(p, '0 non-full-on (mirrors 214)', context, log, 'arms_0_non_fullon', 0,
           [{ id: 621, name: 'arms_holdout_0', variant: 0 }])
    verify(p, '0 full-on (mirrors 214)', context, log, 'arms_0_fullon', 0, [])
    verify(p, '1 non-full-on (mirrors 215)', context, log, 'arms_1_non_fullon', 0,
           [{ id: 622, name: 'arms_holdout_1', variant: 1 }])
    verify(p, '1 full-on (mirrors 215)', context, log, 'arms_1_fullon', 2, [{ id: 604, name: 'arms_1_fullon', variant: 2 }])
    verify(p, '2 non-full-on (mirrors 216)', context, log, 'arms_2_non_fullon', 1,
           [{ id: 605, name: 'arms_2_non_fullon', variant: 1 }, { id: 623, name: 'arms_holdout_2', variant: 2 }])
    verify(p, '2 full-on (mirrors 216)', context, log, 'arms_2_fullon', 2, [{ id: 606, name: 'arms_2_fullon', variant: 2 }])
    true
  end

  def safely(prefix)
    result = yield
    warn "#{prefix} behavioral self-test PASSED"
    result
  rescue StandardError, ScriptError => e
    warn "#{prefix} behavioral self-test FAILED: #{e.message}"
    false
  end

  def holdouts?
    return @holdouts if defined?(@holdouts)

    HOLDOUTS_MUTEX.synchronize do
      return @holdouts if defined?(@holdouts)

      @holdouts = safely('[holdouts probe]') { run_holdouts_battery }
    end
  end

  # Three arms can't work without the two-arm basics, so that battery is a precondition.
  def holdout_arms?
    return @holdout_arms if defined?(@holdout_arms)

    base = holdouts?
    HOLDOUT_ARMS_MUTEX.synchronize do
      return @holdout_arms if defined?(@holdout_arms)

      @holdout_arms = base && safely('[holdout_arms probe]') { run_holdout_arms_battery }
    end
  end
end
