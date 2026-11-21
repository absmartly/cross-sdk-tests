defmodule ElixirWrapper.HoldoutSelfTestTest do
  use ExUnit.Case, async: false
  import ExUnit.CaptureLog
  import Plug.Test

  alias ElixirWrapper.{HoldoutSelfTest, Router}

  test "the holdout self-tests pass against the pinned elixir-sdk" do
    assert HoldoutSelfTest.run(:holdouts)
    assert HoldoutSelfTest.run(:holdout_arms)
  end

  test "/capabilities advertises holdouts and holdout_arms from the self-tests" do
    response = conn(:get, "/capabilities") |> Router.call(Router.init([]))
    assert response.status == 200
    body = Jason.decode!(response.resp_body)
    assert body["holdouts"] == HoldoutSelfTest.holdouts?()
    assert body["holdout_arms"] == HoldoutSelfTest.holdout_arms?()
  end

  test "/capabilities reports false when the self-tests failed" do
    keys = [{HoldoutSelfTest, :holdouts}, {HoldoutSelfTest, :holdout_arms}]
    saved = Enum.map(keys, &{&1, :persistent_term.get(&1, :unset)})

    on_exit(fn ->
      for {key, value} <- saved do
        if value == :unset,
          do: :persistent_term.erase(key),
          else: :persistent_term.put(key, value)
      end
    end)

    Enum.each(keys, &:persistent_term.put(&1, false))

    body =
      conn(:get, "/capabilities")
      |> Router.call(Router.init([]))
      |> Map.fetch!(:resp_body)
      |> Jason.decode!()

    assert body["holdouts"] == false
    assert body["holdout_arms"] == false
  end

  test "a probe fails closed on a failed check, an exception, an exit or a throw" do
    capture_log(fn ->
      refute HoldoutSelfTest.run(:probe, fn -> {:failed, "X", "wrong exposures"} end)
      refute HoldoutSelfTest.run(:probe, fn -> raise "boom" end)
      refute HoldoutSelfTest.run(:probe, fn -> exit(:boom) end)
      refute HoldoutSelfTest.run(:probe, fn -> throw(:boom) end)
    end)
  end
end
