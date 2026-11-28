# Run with RUBYLIB pointing at the linked ruby-sdk/lib.
require 'minitest/autorun'
require 'json'
require 'ostruct'
require 'absmartly'
require 'default_context_event_serializer'
require_relative 'holdout_self_test'
require_relative 'switchback_self_test'

class ProbeRegressionTest < Minitest::Test
  def setup
    @saved = {}
    %i[@holdouts @holdout_arms].each do |key|
      @saved[key] = HoldoutSelfTest.instance_variable_get(key) if HoldoutSelfTest.instance_variable_defined?(key)
      HoldoutSelfTest.remove_instance_variable(key) if HoldoutSelfTest.instance_variable_defined?(key)
    end
  end

  def teardown
    %i[@holdouts @holdout_arms].each do |key|
      HoldoutSelfTest.remove_instance_variable(key) if HoldoutSelfTest.instance_variable_defined?(key)
      HoldoutSelfTest.instance_variable_set(key, @saved[key]) if @saved.key?(key)
    end
  end

  def test_linked_sdk_passes_both_holdout_batteries_and_switchback
    assert HoldoutSelfTest.run_holdouts_battery
    assert HoldoutSelfTest.run_holdout_arms_battery
    assert SwitchbackSelfTest.check_context_serving
  end

  def test_concurrent_first_calls_run_each_battery_once_including_false_results
    [true, false].each do |verdict|
      %i[holdouts holdout_arms].each do |name|
        key = "@#{name}".to_sym
        HoldoutSelfTest.remove_instance_variable(key) if HoldoutSelfTest.instance_variable_defined?(key)
        HoldoutSelfTest.instance_variable_set(:@holdouts, true) if name == :holdout_arms
        count = 0
        count_mutex = Mutex.new
        probe = lambda do
          count_mutex.synchronize { count += 1 }
          sleep 0.05
          verdict
        end
        HoldoutSelfTest.stub("run_#{name}_battery", probe) do
          threads = 16.times.map { Thread.new { HoldoutSelfTest.public_send("#{name}?") } }
          assert_equal Array.new(16, verdict), threads.map(&:value)
          assert_equal 1, count
          assert_equal verdict, HoldoutSelfTest.public_send("#{name}?")
          assert_equal 1, count
        end
      end
    end
  end

  def test_failed_base_skips_arms_battery
    HoldoutSelfTest.stub(:run_holdouts_battery, false) do
      HoldoutSelfTest.stub(:run_holdout_arms_battery, -> { flunk 'arms ran despite failed base' }) do
        refute HoldoutSelfTest.holdout_arms?
      end
    end
  end

  def test_late_unit_holdout_must_reach_the_publish_batch
    publisher = HoldoutSelfTest::RecordingPublisher.new
    original = publisher.method(:publish)
    publisher.define_singleton_method(:publish) do |context, event|
      event.exposures = event.exposures.reject { |e| e.id == 408 } if event.exposures
      original.call(context, event)
    end
    HoldoutSelfTest::RecordingPublisher.stub(:new, publisher) do
      error = assert_raises(RuntimeError) { HoldoutSelfTest.run_holdouts_battery }
      assert_match(/check G publish FAILED/, error.message)
    end
  end

  def test_inert_publish_cannot_reuse_baseline_identity
    original = SwitchbackSelfTest.method(:publish_one)
    mutate = lambda do |*args|
      event = original.call(*args)
      event['exposures'].each do |e|
        e.merge!('id' => 514, 'name' => 'sb_unit_baseline') if e['id'] == 527
      end
      event
    end
    SwitchbackSelfTest.stub(:publish_one, mutate) do
      error = assert_raises(RuntimeError) { SwitchbackSelfTest.check_context_serving }
      assert_match(/publish exposure/, error.message)
    end
  end
end
