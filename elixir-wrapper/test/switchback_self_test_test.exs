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
      assert {400, %{"error" => _}} = call(:post, "/switchback/assign", bad), inspect(bad)
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
end
