defmodule ElixirWrapper.DeleteContextTest do
  use ExUnit.Case, async: false
  import Plug.Test

  alias ElixirWrapper.{ContextStore, EventCollector, Router}

  test "delete stops the context and collector without changing the response" do
    collector = EventCollector.new()
    {:ok, context} = Agent.start_link(fn -> :context end)
    ContextStore.store_context("delete-test", context, collector)
    ContextStore.arm_publish_fail(context)

    response = conn(:delete, "/context/delete-test") |> Router.call(Router.init([]))
    assert response.status == 200
    assert Jason.decode!(response.resp_body) == %{"result" => "deleted"}
    refute Process.alive?(context)
    refute Process.alive?(collector)
    assert ContextStore.get_context("delete-test") == {:error, :not_found}
    refute ContextStore.take_publish_fail([context])

    response = conn(:delete, "/context/delete-test") |> Router.call(Router.init([]))
    assert response.status == 200
    assert Jason.decode!(response.resp_body) == %{"result" => "deleted"}
  end

  defmodule CallbackContext do
    use GenServer

    def start_link(owner), do: GenServer.start_link(__MODULE__, owner)

    @impl true
    def init(owner) do
      Process.flag(:trap_exit, true)
      {:ok, owner}
    end

    @impl true
    def terminate(_reason, owner) do
      result = ElixirWrapper.ContextStore.get_context("supervised-test")
      send(owner, {:shutdown_callback, result})
    end
  end

  test "delete terminates supervised contexts outside the store" do
    collector = EventCollector.new()
    child = %{id: CallbackContext, start: {CallbackContext, :start_link, [self()]}, restart: :temporary}
    {:ok, context} = DynamicSupervisor.start_child(ABSmartly.ContextSupervisor, child)
    ContextStore.store_context("supervised-test", context, collector)
    assert :ok == ContextStore.delete_context("supervised-test")
    assert_received {:shutdown_callback, {:error, :not_found}}
    refute Process.alive?(context)
    refute Process.alive?(collector)
  end
end
