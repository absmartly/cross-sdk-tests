defmodule ElixirWrapper.SwitchbackSelfTest do
  @moduledoc """
  Behavioral self-test behind the `switchback` capability: a port of the
  java-wrapper's SwitchbackSelfTest. It samples the platform golden vectors
  through the SDK's assigner, then serves an R33 payload, decoded from JSON
  the way the wrapper decodes scenario payloads, through a real context and
  checks every treatment and the complete exposure record (except
  `exposedAt`), then the publish payload against a unit baseline.

  The grid keeps "now" inside period 0 until 2069 (a 400,000-hour period
  from 2024-01-01T00:00Z), as the scenarios do. It runs once per wrapper
  process, the result is cached, and it fails closed: a failed check, an
  exception, an exit or a throw reports `false`.
  """

  require Logger

  @period_ms 400_000 * 3_600_000
  @anchor 1_704_067_200_000
  # hashUnit("serving-9")
  @serving_hash "r5uxZRoi-JuM5yYJa0kmSQ"

  # {clusterUnit, periodIndex, seed, armCount, expected}: platform golden
  # vectors, including Long.MIN_VALUE/MAX_VALUE periods and seeds.
  @assign_cases [
    {"market_1", "0", "0", 2, 0},
    {"market_1", "-9223372036854775808", "1", 2, 1},
    {"market_1", "9223372036854775807", "1", 2, 1},
    {"123456789", "0", "0", 2, 1},
    {"market_1", "10", "0", 3, 2},
    {"123456789", "-2", "-1", 3, 0},
    {"market_1", "5", "1", 10, 0},
    {"market_2", "-11", "-9223372036854775808", 10, 3},
    {"market_2", "9223372036854775807", "-9223372036854775808", 10, 2},
    {"market_1", "1", "9223372036854775807", 2, 1}
  ]

  @permutation_cases [
    {"market_1", "0", "0", [0, 3, 7, 5, 8, 2, 6, 4, 1, 9]},
    {"market_1", "-9223372036854775808", "0", [1, 0]},
    {"market_1", "9223372036854775807", "1", [0, 1]},
    {"123456789", "0", "0", [2, 0, 1]}
  ]

  @assigner ABSmartly.Switchback.Assigner

  def switchback?, do: cached(:switchback, &run_switchback/0)

  @doc false
  def run(:switchback), do: safely(:switchback, &run_switchback/0)
  def run(name, fun), do: safely(name, fun)

  @doc "Whether the linked SDK has the switchback assigner."
  def available?,
    do: Application.get_env(:elixir_wrapper, :switchback_available, true) and Code.ensure_loaded?(@assigner)

  @doc "assignSwitchback through the SDK."
  def assign(unit, period, seed, arms) do
    assigner = Application.get_env(:elixir_wrapper, :switchback_assigner, @assigner)
    apply(assigner, :assign, [unit, period, seed, arms])
  end

  @doc "fisherYatesPermutation(arms, baseHash(unit, cycle, seed)) through the SDK."
  def permutation(unit, cycle, seed, arms) do
    base = apply(@assigner, :base_hash, [unit, cycle, seed])
    @assigner |> apply(:fisher_yates_permutation, [arms, base]) |> Tuple.to_list()
  end

  defp cached(name, fun) do
    key = {__MODULE__, name}

    case :persistent_term.get(key, :unset) do
      :unset ->
        result = safely(name, fun)
        :persistent_term.put(key, result)
        result

      result ->
        result
    end
  end

  defp safely(name, fun) do
    case fun.() do
      :ok ->
        Logger.info("[#{name} probe] behavioral self-test PASSED")
        true

      {:failed, check, detail} ->
        Logger.warning("[#{name} probe] check #{check} FAILED: #{detail}")
        false
    end
  rescue
    exception ->
      Logger.warning("[#{name} probe] FAILED with error: #{Exception.message(exception)}")
      false
  catch
    kind, reason ->
      Logger.warning("[#{name} probe] FAILED with #{inspect(kind)}: #{inspect(reason)}")
      false
  end

  defp run_switchback do
    with :ok <- check_golden_sample(),
         :ok <- check_wire_model(),
         do: check_context_serving()
  end

  defp check_golden_sample do
    if available?() do
      Enum.find_value(@assign_cases, :ok, fn {unit, period, seed, arms, expected} ->
        actual = assign(unit, String.to_integer(period), String.to_integer(seed), arms)
        if actual != expected, do: {:failed, "golden assign #{unit}/#{period}/#{seed}/#{arms}", "got #{actual}"}
      end)
      |> case do
        :ok ->
          Enum.find_value(@permutation_cases, :ok, fn {unit, cycle, seed, expected} ->
            actual = permutation(unit, String.to_integer(cycle), String.to_integer(seed), length(expected))
            if actual != expected, do: {:failed, "golden permutation #{unit}/#{cycle}", inspect(actual)}
          end)

        failed ->
          failed
      end
    else
      {:failed, "assigner", "linked elixir-sdk has no ABSmartly.Switchback.Assigner"}
    end
  end

  # The R33 wire model: switchbackClusters and switchbackClusterValues, and
  # none of the abandoned Phase 1E bucket fields.
  defp check_wire_model do
    fields = Map.keys(%ABSmartly.Types.ExperimentData{})

    if :switchback_clusters in fields and :switchback_cluster_values in fields and
         :randomization_type in fields and :switchback_cluster_count not in fields,
       do: :ok,
       else: {:failed, "wire model", "ExperimentData lacks the R33 switchback fields"}
  end

  defp base(id, name, seed_hi, seed_lo, arms) do
    %{
      "id" => id,
      "name" => name,
      "iteration" => 1,
      "unitType" => "unit_serving",
      "seedHi" => seed_hi,
      "seedLo" => seed_lo,
      "split" => List.duplicate(1.0 / arms, arms),
      "trafficSeedHi" => 1,
      "trafficSeedLo" => 2,
      "trafficSplit" => [0, 1],
      "fullOnVariant" => 0,
      "applications" => [%{"name" => "website"}],
      "variants" => for(i <- 0..(arms - 1), do: %{"name" => <<?A + i>>, "config" => nil}),
      "audienceStrict" => false
    }
  end

  # The cluster map is written as JSON text so its declared order is the
  # text order, which a map would lose.
  defp switchback(id, name, seed_hi, seed_lo, arms, clusters, extra \\ "") do
    fields =
      base(id, name, seed_hi, seed_lo, arms)
      |> Map.merge(%{
        "randomizationType" => "switchback",
        "switchbackStartAt" => @anchor,
        "switchbackPeriodMs" => @period_ms,
        "switchbackDesignType" => "balanced_block",
        "switchbackEstimand" => "global_average_treatment_effect"
      })
      |> Map.merge(if(clusters, do: %{"unitType" => elem(hd(clusters), 0)}, else: %{}))
      |> Jason.encode!()

    map =
      if clusters,
        do: ~s(,"switchbackClusters":{) <> Enum.map_join(clusters, ",", fn {t, p} -> Jason.encode!(t) <> ":" <> Jason.encode!(p) end) <> "}",
        else: ""

    String.trim_trailing(fields, "}") <> map <> extra <> "}"
  end

  defp unit(id, name, extra \\ "") do
    String.trim_trailing(Jason.encode!(base(id, name, -559_038_737, 1_234_567_890, 3)), "}") <> extra <> "}"
  end

  @doc false
  def probe_payload do
    experiments = [
      switchback(521, "sb_agree", 0, 42, 10, [{"unit_serving", "city"}, {"unit_user", "profile/city"}]),
      switchback(522, "sb_declared_order", 0, 42, 10, [{"unit_user", "profile/city"}, {"unit_serving", "city"}]),
      switchback(523, "sb_disagree", 0, 42, 10, [{"unit_serving", "city"}, {"unit_user", "profile/zone"}]),
      switchback(524, "sb_whole_number", 0, 42, 10, [{"unit_serving", "store_number"}]),
      switchback(528, "sb_allow_empty", 0, 42, 10, [{"unit_serving", "city"}], ~s(,"switchbackClusterValues":[])),
      switchback(525, "sb_value_filtered", 0, 1, 10, [{"unit_serving", "city"}], ~s(,"switchbackClusterValues":["market_2"])),
      switchback(526, "sb_phase1e_only", 0, 1, 10, nil,
        ~s(,"switchbackClusterUnitType":"unit_serving","switchbackClusterCount":10,"switchbackClusterAttribute":"city")),
      switchback(529, "sb_float_value", 0, 42, 10, [{"unit_serving", "float_city"}]),
      switchback(513, "sb_payload", 0, 1, 3, [{"unit_serving", "city"}]),
      unit(514, "sb_unit_baseline"),
      unit(527, "sb_unit_with_fields",
        ~s(,"randomizationType":"unit","switchbackClusters":{"unit_serving":"city"},"switchbackClusterValues":["market_1"],) <>
          ~s("switchbackStartAt":#{@anchor},"switchbackPeriodMs":#{@period_ms},"switchbackDesignType":"balanced_block"))
    ]

    ~s({"experiments":[) <> Enum.join(experiments, ",") <> "]}"
  end

  defmodule CapturePublisher do
    @moduledoc false
    @behaviour ABSmartly.ContextPublisher

    @impl ABSmartly.ContextPublisher
    def publish(endpoint, _api_key, _application, _environment, event_map, _retries) do
      send(:erlang.binary_to_term(Base.decode64!(endpoint)), {:probe_publish, event_map})
      :ok
    end
  end

  defp check_context_serving do
    {:ok, decoded} = ElixirWrapper.OrderedJson.decode(probe_payload())
    data = ABSmartly.Types.ContextData.from_map(decoded)
    declared = Enum.find(data.experiments, &(&1.name == "sb_declared_order")).switchback_clusters

    case declared do
      %{values: [{"unit_user", _}, {"unit_serving", _}]} ->
        serve(data)

      other ->
        {:failed, "declared order", "switchbackClusters lost its order: #{inspect(other)}"}
    end
  end

  defp serve(data) do
    owner = self()

    handler = fn
      :exposure, exposure -> send(owner, {:probe_exposure, ABSmartly.Types.Exposure.to_map(exposure)})
      _type, _data -> :ok
    end

    sdk_config = %ABSmartly.Types.SDKConfig{
      endpoint: Base.encode64(:erlang.term_to_binary(owner)),
      api_key: "probe",
      application: "test",
      environment: "test",
      publisher: CapturePublisher
    }

    # unit_user is listed first, so the declared order, not the units',
    # must pick the serving unit.
    context_config = %{units: %{"unit_user" => "probe-user", "unit_serving" => "serving-9"}}

    # The customer path: ABSmartly.SDK.create_context_with/4.
    {:ok, ctx} =
      ABSmartly.SDK.create_context_with(%ABSmartly.SDK{config: sdk_config}, context_config.units, data, %{
        event_handler: handler,
        publish_delay: -1,
        refresh_period: 0
      })

    try do
      :ok = ABSmartly.Context.set_attribute(ctx, "city", "market_1")
      :ok = ABSmartly.Context.set_attribute(ctx, "profile", %{"city" => "market_1", "zone" => "market_2"})
      :ok = ABSmartly.Context.set_attribute(ctx, "store_number", 123_456_789)
      :ok = ABSmartly.Context.set_attribute(ctx, "float_city", 3.0)

      checks = [
        {"agreement", "sb_agree", 3, [exposure(521, "sb_agree", "unit_serving", 3)]},
        {"agreement repeat", "sb_agree", 3, []},
        {"declared order", "sb_declared_order", 3, [exposure(522, "sb_declared_order", "unit_user", 3)]},
        {"disagreement", "sb_disagree", 0, []},
        {"whole number", "sb_whole_number", 6, [exposure(524, "sb_whole_number", "unit_serving", 6)]},
        {"value filtered out", "sb_value_filtered", 0, []},
        {"empty allow-list admits all", "sb_allow_empty", 3, [exposure(528, "sb_allow_empty", "unit_serving", 3)]},
        {"Phase 1E fields only", "sb_phase1e_only", 0, []},
        {"a float is no cluster value", "sb_float_value", 0, []}
      ]

      with :ok <- run_checks(ctx, checks),
           do: check_payload_shape(ctx)
    after
      DynamicSupervisor.terminate_child(ABSmartly.ContextSupervisor, ctx)
    end
  end

  defp exposure(id, name, unit, variant) do
    %{
      "id" => id,
      "name" => name,
      "unit" => unit,
      "variant" => variant,
      "assigned" => true,
      "eligible" => true,
      "overridden" => false,
      "fullOn" => false,
      "custom" => false,
      "audienceMismatch" => false,
      "ruleOverride" => false
    }
  end

  defp run_checks(ctx, checks) do
    Enum.find_value(checks, :ok, fn {label, name, expected, expected_exposures} ->
      drain(:probe_exposure)
      treatment = ABSmartly.Context.treatment(ctx, name)
      exposures = Enum.map(drain(:probe_exposure), &without_exposed_at/1)

      unless treatment == expected and exposures == expected_exposures do
        {:failed, label,
         "expected #{expected} with #{inspect(expected_exposures)}, got #{treatment} with #{inspect(exposures)}"}
      end
    end)
  end

  defp without_exposed_at(exposure) do
    if is_integer(exposure["exposedAt"]) and exposure["exposedAt"] > 0,
      do: Map.delete(exposure, "exposedAt"),
      else: exposure
  end

  defp drain(tag, acc \\ []) do
    receive do
      {^tag, item} -> drain(tag, [item | acc])
    after
      0 -> Enum.reverse(acc)
    end
  end

  defp publish_one(ctx) do
    drain(:probe_publish)
    :ok = ABSmartly.Context.publish(ctx)

    case drain(:probe_publish) do
      [event] -> Jason.decode!(Jason.encode!(event))
      other -> {:failed, "publish", "expected one publish, got #{inspect(other)}"}
    end
  end

  # A unit baseline and then a switchback on the same unit, each published
  # on its own, have the same fields at every depth; a unit experiment
  # carrying every R33 field publishes the same bytes as the baseline.
  defp check_payload_shape(ctx) do
    :ok = ABSmartly.Context.publish(ctx)
    ABSmartly.Context.treatment(ctx, "sb_unit_baseline")
    baseline = publish_one(ctx)
    ABSmartly.Context.treatment(ctx, "sb_payload")
    switchback = publish_one(ctx)
    ABSmartly.Context.treatment(ctx, "sb_unit_with_fields")
    with_fields = publish_one(ctx)

    cond do
      not Enum.all?([baseline, switchback, with_fields], &is_map/1) ->
        {:failed, "publish", inspect([baseline, switchback, with_fields])}

      shape(baseline) != shape(switchback) ->
        {:failed, "publish shape", "#{inspect(switchback)} vs #{inspect(baseline)}"}

      exposures_of(baseline) != [exposure(514, "sb_unit_baseline", "unit_serving", 1)] ->
        {:failed, "unit baseline", inspect(baseline)}

      exposures_of(switchback) != [exposure(513, "sb_payload", "unit_serving", 1)] ->
        {:failed, "switchback publish", inspect(switchback)}

      not Enum.member?(switchback["units"], %{"type" => "unit_serving", "uid" => @serving_hash}) ->
        {:failed, "published units", inspect(switchback["units"])}

      normalized(with_fields) != normalized(baseline) ->
        {:failed, "unit inertness", "#{inspect(with_fields)} vs #{inspect(baseline)}"}

      true ->
        :ok
    end
  end

  defp exposures_of(event), do: Enum.map(event["exposures"], &without_exposed_at/1)

  defp normalized(event) do
    event
    |> Map.delete("publishedAt")
    |> Map.update!("exposures", fn exposures ->
      Enum.map(exposures, &Map.merge(&1, %{"id" => 0, "name" => "", "exposedAt" => 0}))
    end)
  end

  defp shape(%{} = map), do: Map.new(map, fn {k, v} -> {k, shape(v)} end)
  defp shape(list) when is_list(list), do: Enum.map(list, &shape/1)
  defp shape(_scalar), do: :scalar
end
