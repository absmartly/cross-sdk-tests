defmodule ElixirWrapper.SwitchbackSelfTestTest do
  use ExUnit.Case, async: false
  import ExUnit.CaptureLog
  import Plug.Test

  alias ElixirWrapper.{Router, SwitchbackSelfTest}

  defp call(method, path, body \\ nil) do
    conn =
      if body,
        do: conn(method, path, Jason.encode!(body)) |> Plug.Conn.put_req_header("content-type", "application/json"),
        else: conn(method, path)

    response = Router.call(conn, Router.init([]))
    {response.status, Jason.decode!(response.resp_body)}
  end

  test "the switchback self-test passes against the pinned elixir-sdk" do
    assert SwitchbackSelfTest.run(:switchback)
  end

  test "/capabilities advertises switchback from the self-test" do
    {200, body} = call(:get, "/capabilities")
    assert body["switchback"] == SwitchbackSelfTest.switchback?()
  end

  test "/capabilities reports false when the self-test failed" do
    key = {SwitchbackSelfTest, :switchback}
    saved = :persistent_term.get(key, :unset)

    on_exit(fn ->
      if saved == :unset, do: :persistent_term.erase(key), else: :persistent_term.put(key, saved)
    end)

    :persistent_term.put(key, false)
    {200, body} = call(:get, "/capabilities")
    assert body["switchback"] == false
  end

  test "a probe fails closed on a failed check, an exception, an exit or a throw" do
    capture_log(fn ->
      refute SwitchbackSelfTest.run(:probe, fn -> {:failed, "X", "wrong exposures"} end)
      refute SwitchbackSelfTest.run(:probe, fn -> raise "boom" end)
      refute SwitchbackSelfTest.run(:probe, fn -> exit(:boom) end)
      refute SwitchbackSelfTest.run(:probe, fn -> throw(:boom) end)
    end)
  end

  test "/switchback/assign and /switchback/permutation serve the golden vectors" do
    assert call(:post, "/switchback/assign", %{
             "clusterUnit" => "market_1",
             "periodIndex" => "-9223372036854775808",
             "seed" => "1",
             "armCount" => 2
           }) == {200, %{"result" => 1, "events" => []}}

    assert call(:post, "/switchback/permutation", %{
             "clusterUnit" => "market_1",
             "cycleIndex" => "0",
             "seed" => "0",
             "armCount" => 10
           }) == {200, %{"result" => [0, 3, 7, 5, 8, 2, 6, 4, 1, 9], "events" => []}}
  end

  test "invalid switchback requests are 400" do
    valid = %{"clusterUnit" => "u", "periodIndex" => "0", "seed" => "0", "armCount" => 2}

    for bad <- [
          Map.delete(valid, "clusterUnit"),
          Map.put(valid, "clusterUnit", 7),
          Map.put(valid, "periodIndex", 0),
          Map.put(valid, "periodIndex", "1.5"),
          Map.put(valid, "periodIndex", "9223372036854775808"),
          Map.put(valid, "seed", " 1"),
          Map.put(valid, "armCount", 1),
          Map.put(valid, "armCount", "2")
        ] do
      assert {400, %{"error" => _, "events" => []}} = call(:post, "/switchback/assign", bad), inspect(bad)
    end
  end

  test "a stored payload keeps the switchbackClusters order through the wrapper" do
    body = ~s({"data":{"experiments":[{"switchbackClusters":{"z_unit":"a","a_unit":"b"}}]}})

    conn(:put, "/context_payload/order-probe", body)
    |> Plug.Conn.put_req_header("content-type", "application/json")
    |> Router.call(Router.init([]))

    response = conn(:get, "/context_payload/order-probe/context") |> Router.call(Router.init([]))
    assert response.resp_body =~ ~s({"z_unit":"a","a_unit":"b"})
  end

  # The ready event carries the experiments, whose switchbackClusters is a
  # Jason.OrderedObject: collecting it must not crash on its tuples.
  test "events carrying a switchbackClusters object are collected" do
    collector = ElixirWrapper.EventCollector.new()
    clusters = %Jason.OrderedObject{values: [{"z_unit", "a"}, {"a_unit", "b"}]}
    ElixirWrapper.EventCollector.push(collector, :ready, %{experiments: [%{switchback_clusters: clusters}]})
    [event] = ElixirWrapper.EventCollector.get_all(collector)
    assert event.data == %{"experiments" => [%{"switchback_clusters" => %{"z_unit" => "a", "a_unit" => "b"}}]}
  end

  defmodule AnswersEverything do
    @moduledoc false
    def assign(_unit, _period, _seed, _arms), do: 0
  end

  # The wrapper's own 400 contract: armCount < 2 is refused by the wrapper,
  # so a regressed assigner that answers it cannot turn it into a 200.
  test "armCount below 2 is a 400 whatever the assigner answers" do
    Application.put_env(:elixir_wrapper, :switchback_assigner, AnswersEverything)
    on_exit(fn -> Application.delete_env(:elixir_wrapper, :switchback_assigner) end)

    for arms <- [1, 0, -3] do
      assert {400, %{"error" => _, "events" => []}} =
               call(:post, "/switchback/assign", %{
                 "clusterUnit" => "u",
                 "periodIndex" => "0",
                 "seed" => "0",
                 "armCount" => arms
               })
    end

    assert {200, %{"result" => 0}} =
             call(:post, "/switchback/assign", %{
               "clusterUnit" => "u",
               "periodIndex" => "0",
               "seed" => "0",
               "armCount" => 2
             })
  end

  test "an SDK without the assigner answers 501 on both endpoints" do
    Application.put_env(:elixir_wrapper, :switchback_available, false)
    on_exit(fn -> Application.delete_env(:elixir_wrapper, :switchback_available) end)

    body = %{"clusterUnit" => "u", "periodIndex" => "0", "cycleIndex" => "0", "seed" => "0", "armCount" => 2}
    assert {501, %{"error" => _, "events" => []}} = call(:post, "/switchback/assign", body)
    assert {501, %{"error" => _, "events" => []}} = call(:post, "/switchback/permutation", body)
  end

  defmodule WrongAssign do
    @moduledoc false
    # Golden permutations, one wrong assign.
    defdelegate base_hash(unit, cycle, seed), to: ABSmartly.Switchback.Assigner
    defdelegate fisher_yates_permutation(arms, base), to: ABSmartly.Switchback.Assigner

    def assign(unit, period, seed, arms) do
      variant = ABSmartly.Switchback.Assigner.assign(unit, period, seed, arms)
      if {unit, period} == {"market_2", -11}, do: rem(variant + 1, arms), else: variant
    end
  end

  defmodule SwappedPermutation do
    @moduledoc false
    # Golden assigns, permutation inputs swapped (cycle <-> seed).
    defdelegate assign(unit, period, seed, arms), to: ABSmartly.Switchback.Assigner
    defdelegate fisher_yates_permutation(arms, base), to: ABSmartly.Switchback.Assigner
    def base_hash(unit, cycle, seed), do: ABSmartly.Switchback.Assigner.base_hash(unit, seed, cycle)
  end

  defmodule Raising do
    @moduledoc false
    def assign(_unit, _period, _seed, _arms), do: raise("broken assigner")
  end

  defp with_assigner(assigner, fun) do
    Application.put_env(:elixir_wrapper, :switchback_assigner, assigner)

    try do
      fun.()
    after
      Application.delete_env(:elixir_wrapper, :switchback_assigner)
    end
  end

  test "the self-test fails on a wrong golden assign, a swapped permutation or a raising assigner" do
    capture_log(fn ->
      for assigner <- [WrongAssign, SwappedPermutation, Raising] do
        with_assigner(assigner, fn -> refute SwitchbackSelfTest.run(:switchback), inspect(assigner) end)
      end
    end)

    with_assigner(WrongAssign, fn ->
      assert {:failed, "golden assign market_2/-11" <> _, _} = SwitchbackSelfTest.check_golden_sample()
    end)

    with_assigner(SwappedPermutation, fn ->
      assert {:failed, "golden permutation market_1/1", _} = SwitchbackSelfTest.check_golden_sample()
    end)
  end

  test "/switchback/permutation passes cycleIndex and seed in their own places" do
    assert call(:post, "/switchback/permutation", %{
             "clusterUnit" => "market_1",
             "cycleIndex" => "1",
             "seed" => "0",
             "armCount" => 10
           }) == {200, %{"result" => [6, 0, 3, 8, 9, 7, 2, 4, 5, 1], "events" => []}}
  end

  test "an armCount no experiment can have is a 400, and the wrapper stays up" do
    for arms <- [16_385, 65_537, 2_147_483_647, 2_147_483_648] do
      body = %{"clusterUnit" => "u", "periodIndex" => "0", "cycleIndex" => "0", "seed" => "0", "armCount" => arms}
      assert {400, %{"error" => _, "events" => []}} = call(:post, "/switchback/assign", body)
      assert {400, %{"error" => _, "events" => []}} = call(:post, "/switchback/permutation", body)
    end
  end

  test "the wire-model check rejects a model without R33 fields or with the Phase 1E count" do
    assert {:failed, "wire model", _} = SwitchbackSelfTest.check_wire_model([:randomization_type])

    assert {:failed, "wire model", _} =
             SwitchbackSelfTest.check_wire_model([
               :randomization_type,
               :switchback_clusters,
               :switchback_cluster_values,
               :switchback_cluster_count
             ])
  end

  test "context serving fails when the declared order is lost or an exposure differs" do
    sorted = String.replace(SwitchbackSelfTest.probe_payload(),
      ~s("switchbackClusters":{"unit_user":"profile/city","unit_serving":"city"}),
      ~s("switchbackClusters":{"unit_serving":"city","unit_user":"profile/city"}))

    refute sorted == SwitchbackSelfTest.probe_payload()
    assert {:failed, "declared order", _} = SwitchbackSelfTest.check_context_serving(sorted)

    # A different seed changes sb_agree's draw, so the agreement check fails.
    reseeded = String.replace(SwitchbackSelfTest.probe_payload(),
      ~s("name":"sb_agree","randomizationType":"switchback","seedHi":0,"seedLo":42),
      ~s("name":"sb_agree","randomizationType":"switchback","seedHi":0,"seedLo":43))

    refute reseeded == SwitchbackSelfTest.probe_payload()

    capture_log(fn ->
      assert {:failed, "agreement", _} = SwitchbackSelfTest.check_context_serving(reseeded)
    end)

    # A serving unit published under the wrong type is caught on the
    # exposure record alone (the treatment is unchanged).
    retyped = String.replace(SwitchbackSelfTest.probe_payload(),
      ~s("switchbackClusters":{"unit_user":"profile/city","unit_serving":"city"}),
      ~s("switchbackClusters":{"unit_serving":"profile/city","unit_user":"city"}))

    refute retyped == SwitchbackSelfTest.probe_payload()

    capture_log(fn ->
      assert {:failed, label, detail} = SwitchbackSelfTest.check_context_serving(retyped)
      assert label in ["declared order", "agreement"]
      assert detail =~ "unit_serving" or detail =~ "order"
    end)
  end

  test "context serving fails before serving when a decoder loses the cluster order" do
    # Plain Jason decodes the two-entry map unordered, so ExperimentData
    # refuses it and the declared-order check fails first.
    assert {:failed, "declared order", _} =
             SwitchbackSelfTest.check_context_serving(SwitchbackSelfTest.probe_payload(), &Jason.decode/1)
  end

  test "context serving compares the exposure record, not only the treatment" do
    # sb_agree keeps its treatment (both units hold the same city) but is
    # then served by unit_user, which only the exposure's unit shows.
    swapped = String.replace(SwitchbackSelfTest.probe_payload(),
      ~s({"unit_serving":"city","unit_user":"profile/city"}),
      ~s({"unit_user":"profile/city","unit_serving":"city"}))

    refute swapped == SwitchbackSelfTest.probe_payload()

    capture_log(fn ->
      assert {:failed, "agreement", detail} = SwitchbackSelfTest.check_context_serving(swapped)
      assert detail =~ "unit_user"
    end)
  end

  test "context serving fails when the published switchback differs from the baseline" do
    renamed = String.replace(SwitchbackSelfTest.probe_payload(), ~s("name":"sb_payload"), ~s("name":"sb_payload_renamed"), global: false)
    refute renamed == SwitchbackSelfTest.probe_payload()

    capture_log(fn ->
      assert {:failed, _, _} = SwitchbackSelfTest.check_context_serving(renamed)
    end)
  end

  test "an armCount at the cap is served" do
    assert {200, %{"result" => variant, "events" => []}} =
             call(:post, "/switchback/assign", %{
               "clusterUnit" => "u",
               "periodIndex" => "0",
               "seed" => "0",
               "armCount" => 16_384
             })

    assert variant in 0..16_383
  end

  # Only an experiment's own switchbackClusters is ordered: an ordinary
  # attribute value with a key of that name stays a plain map, so a nested
  # audience lookup on it still matches.
  test "an attribute object named switchbackClusters is decoded as a plain map" do
    attribute = ~s({"name":"profile","value":{"switchbackClusters":{"country":"US","city":"NY"}}})

    assert ElixirWrapper.OrderedJson.decode(attribute) ==
             {:ok,
              %{
                "name" => "profile",
                "value" => %{"switchbackClusters" => %{"country" => "US", "city" => "NY"}}
              }}

    payload = ~s({"data":{"experiments":[{"switchbackClusters":{"z":"1","a":"2"}}]},"units":{}})

    assert {:ok, %{"data" => %{"experiments" => [%{"switchbackClusters" => clusters}]}}} =
             ElixirWrapper.OrderedJson.decode(payload)

    assert clusters == %Jason.OrderedObject{values: [{"z", "1"}, {"a", "2"}]}

    mixed =
      ~s({"attributes":{"switchbackClusters":{"country":"US"}},) <>
        ~s("data":{"experiments":[{"switchbackClusters":{"z":"1","a":"2"}}]}})

    assert {:ok, decoded} = ElixirWrapper.OrderedJson.decode(mixed)
    assert decoded["attributes"] == %{"switchbackClusters" => %{"country" => "US"}}

    assert hd(decoded["data"]["experiments"])["switchbackClusters"] ==
             %Jason.OrderedObject{values: [{"z", "1"}, {"a", "2"}]}
  end

  test "an attribute value shaped like context data decodes as plain JSON" do
    body = ~s({"name":"profile","value":{"experiments":[{"switchbackClusters":{"country":"US","z":"1"}}]}})
    assert ElixirWrapper.OrderedJson.decode(body) == Jason.decode(body)
  end

  test "only the data member's experiments are ordered" do
    body =
      ~s({"data":{"experiments":[{"switchbackClusters":{"z":"1","a":"2"}}]},) <>
        ~s("options":{"experiments":[{"switchbackClusters":{"q":"1","b":"2"}}]}})

    {:ok, decoded} = ElixirWrapper.OrderedJson.decode(body)

    assert hd(decoded["data"]["experiments"])["switchbackClusters"] ==
             %Jason.OrderedObject{values: [{"z", "1"}, {"a", "2"}]}

    assert decoded["options"] == Jason.decode!(body)["options"]
  end

  # Through the router, as a scenario runs it: the attribute route keeps a
  # context-shaped attribute as plain JSON, so a strict audience reading a
  # key inside it still matches.
  test "a strict audience on a context-shaped attribute matches through the router" do
    experiment = %{
      "id" => 1,
      "name" => "exp_strict",
      "iteration" => 1,
      "unitType" => "session_id",
      "seedHi" => 3_603_515,
      "seedLo" => 233_373_850,
      "split" => [0.0, 1.0],
      "trafficSeedHi" => 449_867_249,
      "trafficSeedLo" => 455_443_629,
      "trafficSplit" => [0.0, 1.0],
      "fullOnVariant" => 0,
      "applications" => [%{"name" => "website"}],
      "variants" => [%{"name" => "A", "config" => nil}, %{"name" => "B", "config" => nil}],
      "audienceStrict" => true,
      "audience" =>
        ~s({"filter":[{"eq":[{"var":"profile/experiments/0/switchbackClusters/country"},{"value":"US"}]}]})
    }

    {200, %{"result" => %{"contextId" => id}}} =
      call(:post, "/context", %{
        "data" => %{"experiments" => [experiment]},
        "units" => %{"session_id" => "e791e240fcd3df7d238cfc285f475e8152fcc0ec"}
      })

    {200, _} =
      call(:post, "/context/#{id}/attribute", %{
        "name" => "profile",
        "value" => %{"experiments" => [%{"switchbackClusters" => %{"country" => "US"}}]}
      })

    assert {200, %{"result" => 1, "events" => [%{"data" => exposure}]}} =
             call(:post, "/context/#{id}/treatment", %{"experimentName" => "exp_strict"})

    assert exposure["audienceMismatch"] == false
    call(:delete, "/context/#{id}")
  end

  test "a repeated data member orders only the first, as Jason keeps it" do
    body = ~s({"data":{"experiments":[{"switchbackClusters":{"z":"1","a":"2"}}]},"data":{"x":1}})
    {:ok, decoded} = ElixirWrapper.OrderedJson.decode(body)

    assert hd(decoded["data"]["experiments"])["switchbackClusters"] ==
             %Jason.OrderedObject{values: [{"z", "1"}, {"a", "2"}]}
  end

  test "a data member without an experiments list decodes as plain JSON" do
    body = ~s({"data":{"experiments":"x","foo":{"experiments":[{"switchbackClusters":{"z":"1","a":"2"}}]}}})
    assert ElixirWrapper.OrderedJson.decode(body) == Jason.decode(body)
  end
end
