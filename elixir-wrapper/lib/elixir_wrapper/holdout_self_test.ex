defmodule ElixirWrapper.HoldoutSelfTest do
  @moduledoc """
  Behavioral self-test for the `holdouts` and `holdout_arms` capabilities: a
  port of javascript-wrapper/holdoutSelfTest.js (and the java-wrapper's
  HoldoutSelfTest/HoldoutArmsSelfTest). It builds a real context from a
  ContextData payload whose values come from the cross-sdk-tests holdout
  scenarios, calls treatment and checks both the returned variant and the exact
  ordered id/name/variant sequence of exposure events each call fires.

  Each battery runs once per wrapper process; the result is cached. It fails
  closed: a failed check, an exception or an exit (an SDK without holdouts, a
  crash, a wrong shape) makes the capability `false` instead of crashing
  /capabilities.
  """

  require Logger

  @held_out_uid "e791e240fcd3df7d238cfc285f475e8152fcc0ec"
  @not_held_out_uid "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3"

  def holdouts?, do: cached(:holdouts, &run_holdouts/0)

  # Three-arm support cannot exist without basic holdout support.
  def holdout_arms?, do: holdouts?() and cached(:holdout_arms, &run_holdout_arms/0)

  # The uncached batteries, so tests can check them and that they fail closed.
  @doc false
  def run(:holdouts), do: safely(:holdouts, &run_holdouts/0)
  def run(:holdout_arms), do: safely(:holdout_arms, &run_holdout_arms/0)

  @doc false
  def run(name, fun), do: safely(name, fun)

  @doc false
  def cached(name, fun) do
    key = {__MODULE__, name}

    case :persistent_term.get(key, :unset) do
      :unset ->
        :global.trans({key, self()}, fn ->
          case :persistent_term.get(key, :unset) do
            :unset ->
              result = safely(name, fun)
              :persistent_term.put(key, result)
              result

            result ->
              result
          end
        end, [node()])

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

  defp variants(names), do: Enum.map(names, &%{"name" => &1, "config" => nil})

  defp experiment(id, name, unit_type, split, full_on, variant_names, holdout_ids) do
    %{
      "id" => id,
      "name" => name,
      "iteration" => 1,
      "unitType" => unit_type,
      "seedHi" => 100,
      "seedLo" => 200,
      "split" => split,
      "trafficSeedHi" => 1,
      "trafficSeedLo" => 2,
      "trafficSplit" => [0, 1],
      "fullOnVariant" => full_on,
      "applications" => [%{"name" => "website"}],
      "variants" => variants(variant_names),
      "audience" => nil,
      "audienceStrict" => false,
      "customFieldValues" => nil,
      "holdoutIds" => holdout_ids
    }
  end

  # `holdout_type` nil leaves holdoutType out of the payload.
  defp holdout(id, name, unit_type, {seed_hi, seed_lo}, split, variant_names, holdout_type) do
    %{
      "id" => id,
      "name" => name,
      "iteration" => 1,
      "unitType" => unit_type,
      "seedHi" => seed_hi,
      "seedLo" => seed_lo,
      "split" => split,
      "trafficSeedHi" => 0,
      "trafficSeedLo" => 0,
      "trafficSplit" => [0, 1],
      "fullOnVariant" => 0,
      "applications" => [],
      "variants" => variants(variant_names),
      "audience" => nil,
      "audienceStrict" => false,
      "customFieldValues" => nil
    }
    |> then(&if(holdout_type, do: Map.put(&1, "holdoutType", holdout_type), else: &1))
  end

  # A context that sends each exposure event to this process. Not linked, so
  # a crashing context fails the probe instead of the caller.
  defp start_context(data, units) do
    owner = self()

    handler = fn
      :exposure, %{id: id, name: name, variant: variant} ->
        send(owner, {:probe_exposure, {id, name, variant}})

      _type, _data ->
        :ok
    end

    sdk_config = %ABSmartly.Types.SDKConfig{
      endpoint: "http://localhost:3000",
      api_key: "probe",
      application: "test",
      environment: "test",
      publisher: ElixirWrapper.HoldoutSelfTest.NoopPublisher
    }

    context_config =
      ABSmartly.Types.ContextConfig.from_options(%{
        "units" => units,
        "publishDelay" => -1,
        "refreshPeriod" => 0,
        "eventHandler" => handler
      })

    {:ok, ctx} =
      GenServer.start(
        ABSmartly.Context,
        {sdk_config, ABSmartly.Types.ContextData.from_map(data), context_config, nil}
      )

    ctx
  end

  defp drain(acc \\ []) do
    receive do
      {:probe_exposure, exposure} -> drain([exposure | acc])
    after
      0 -> Enum.reverse(acc)
    end
  end

  # Calls treatment once and requires both its result and the exposures that
  # call fired, in order.
  defp check(ctx, label, experiment_name, expected_treatment, expected_exposures) do
    drain()
    treatment = ABSmartly.Context.treatment(ctx, experiment_name)
    exposures = drain()

    if treatment == expected_treatment and exposures == expected_exposures do
      :ok
    else
      {:failed, label,
       "expected #{inspect(expected_treatment)} with #{inspect(expected_exposures)}, " <>
         "got #{inspect(treatment)} with #{inspect(exposures)}"}
    end
  end

  defp run_checks(ctx, checks) do
    Enum.reduce_while(checks, :ok, fn check, :ok ->
      case check.(ctx) do
        :ok -> {:cont, :ok}
        failed -> {:halt, failed}
      end
    end)
  after
    GenServer.stop(ctx)
  end

  # Two-arm battery: checks A-G mirror scenarios 203-208 and 221.
  defp run_holdouts do
    s = "session_id"
    b = "session_id_b"
    two = [0.1, 0.9]
    covered = [0.5, 0.5]
    held = {13, 111}
    not_held = {1, 222}

    data = %{
      "experiments" => [
        experiment(301, "probe_a_covered", s, covered, 0, ~w(A B), [401]),
        experiment(302, "probe_b_covered", b, covered, 0, ~w(A B), [402]),
        experiment(304, "probe_c_union", s, covered, 0, ~w(A B), [404, 405]),
        experiment(305, "probe_d_covered", s, covered, 0, ~w(A B), [406]),
        experiment(306, "probe_d_uncovered_sibling", s, covered, 0, ~w(A B), nil),
        experiment(307, "probe_e_dangling_plus_valid", s, covered, 0, ~w(A B), [999, 407]),
        experiment(303, "probe_f_fullon", s, covered, 2, ~w(A B C), [403]),
        experiment(308, "probe_g_late_unit", "user_id", [0.5, 0.5, 0.0], 2, ~w(A B C), [408])
      ],
      "holdouts" => [
        holdout(401, "probe_a_holdout", s, held, two, ~w(A B), "full"),
        holdout(402, "probe_b_holdout", b, held, two, ~w(A B), "full"),
        holdout(404, "probe_c_holdout_low", s, not_held, two, ~w(A B), "full"),
        holdout(405, "probe_c_holdout_high", s, held, two, ~w(A B), "full"),
        holdout(406, "probe_d_holdout", s, held, two, ~w(A B), "full"),
        holdout(407, "probe_e_holdout", s, held, two, ~w(A B), "full"),
        holdout(403, "probe_f_holdout", s, held, two, ~w(A B), "full"),
        holdout(408, "probe_g_holdout", "user_id", not_held, two, ~w(A B), "full")
      ]
    }

    ctx = start_context(data, %{s => @held_out_uid, b => @not_held_out_uid})

    run_checks(ctx, [
      &check(&1, "A (203)", "probe_a_covered", 0, [{401, "probe_a_holdout", 0}]),
      &check(&1, "B (204)", "probe_b_covered", 0, [
        {302, "probe_b_covered", 0},
        {402, "probe_b_holdout", 1}
      ]),
      &check(&1, "C (205)", "probe_c_union", 0, [
        {404, "probe_c_holdout_low", 1},
        {405, "probe_c_holdout_high", 0}
      ]),
      &check(&1, "D (206, covered)", "probe_d_covered", 0, [{406, "probe_d_holdout", 0}]),
      &check(&1, "D (206, uncovered sibling)", "probe_d_uncovered_sibling", 1, [
        {306, "probe_d_uncovered_sibling", 1}
      ]),
      &check(&1, "E (207)", "probe_e_dangling_plus_valid", 0, [{407, "probe_e_holdout", 0}]),
      &check(&1, "F (208)", "probe_f_fullon", 0, [{403, "probe_f_holdout", 0}]),
      &check_late_unit/1
    ])
  end

  # Check G (221): peek resolves the full-on experiment before its unit type
  # is set and exposes nothing; once the unit is set, treatment exposes both
  # the experiment and its holdout.
  defp check_late_unit(ctx) do
    drain()
    peeked = ABSmartly.Context.peek(ctx, "probe_g_late_unit")

    case {peeked, drain()} do
      {2, []} ->
        :ok = ABSmartly.Context.set_unit(ctx, "user_id", @held_out_uid)

        check(ctx, "G (221, after set_unit)", "probe_g_late_unit", 2, [
          {308, "probe_g_late_unit", 2},
          {408, "probe_g_holdout", 1}
        ])

      other ->
        {:failed, "G (221, peek before the unit)", "expected {2, []}, got #{inspect(other)}"}
    end
  end

  # Three-arm battery: checks 0-2 mirror scenarios 214-216.
  defp run_holdout_arms do
    s = "session_id"
    three = [0.3, 0.3, 0.4]

    covered = fn id, name, full_on, holdout_id ->
      names = if full_on == 0, do: ~w(A B), else: ~w(A B C)
      experiment(id, name, s, [0.5, 0.5], full_on, names, [holdout_id])
    end

    data = %{
      "experiments" => [
        covered.(601, "arms_0_non_fullon", 0, 621),
        covered.(602, "arms_0_fullon", 2, 621),
        covered.(603, "arms_1_non_fullon", 0, 622),
        covered.(604, "arms_1_fullon", 2, 622),
        covered.(605, "arms_2_non_fullon", 0, 623),
        covered.(606, "arms_2_fullon", 2, 623)
      ],
      "holdouts" => [
        holdout(621, "arms_holdout_0", s, {0, 1}, three, ~w(A B C), "all_full_on"),
        # No holdoutType: the arity must come from the split (scenario 222
        # only covers arm 0, which holds out at any arity).
        holdout(622, "arms_holdout_1", s, {0, 3}, three, ~w(A B C), nil),
        holdout(623, "arms_holdout_2", s, {0, 0}, three, ~w(A B C), "all_full_on")
      ]
    }

    ctx = start_context(data, %{s => @held_out_uid})

    run_checks(ctx, [
      &check(&1, "0 non-full-on (214)", "arms_0_non_fullon", 0, [{621, "arms_holdout_0", 0}]),
      &check(&1, "0 full-on (214)", "arms_0_fullon", 0, []),
      &check(&1, "1 non-full-on (215)", "arms_1_non_fullon", 0, [{622, "arms_holdout_1", 1}]),
      &check(&1, "1 full-on (215)", "arms_1_fullon", 2, [{604, "arms_1_fullon", 2}]),
      &check(&1, "2 non-full-on (216)", "arms_2_non_fullon", 1, [
        {605, "arms_2_non_fullon", 1},
        {623, "arms_holdout_2", 2}
      ]),
      &check(&1, "2 full-on (216)", "arms_2_fullon", 2, [{606, "arms_2_fullon", 2}])
    ])
  end

  defmodule NoopPublisher do
    @moduledoc false
    @behaviour ABSmartly.ContextPublisher

    @impl ABSmartly.ContextPublisher
    def publish(_endpoint, _api_key, _application, _environment, _event_map, _retries), do: :ok
  end
end
