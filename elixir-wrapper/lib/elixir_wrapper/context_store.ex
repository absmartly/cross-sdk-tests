defmodule ElixirWrapper.ContextStore do
  @moduledoc """
  Storage for contexts and payloads using ETS.
  """

  use GenServer

  def start_link(_opts) do
    GenServer.start_link(__MODULE__, :ok, name: __MODULE__)
  end

  def store_context(context_id, context, collector) do
    GenServer.call(__MODULE__, {:store_context, context_id, context, collector})
  end

  def get_context(context_id) do
    GenServer.call(__MODULE__, {:get_context, context_id})
  end

  # One-shot publish failure, keyed by the SDK context GenServer pid so the
  # injected ElixirWrapper.ArmablePublisher (which only sees its caller chain)
  # can consume it from inside the real SDK publish path.
  def arm_publish_fail(context_pid) when is_pid(context_pid) do
    GenServer.call(__MODULE__, {:arm_publish_fail, context_pid})
  end

  # Atomically consumes an armed failure for any pid in `pids`.
  def take_publish_fail(pids) when is_list(pids) do
    GenServer.call(__MODULE__, {:take_publish_fail, pids})
  end

  def delete_context(context_id) do
    case GenServer.call(__MODULE__, {:delete_context, context_id}) do
      {:ok, {context, collector}} ->
        # Termination may publish and call back into this store, so it must run
        # in the caller, not in the store's handle_call.
        case DynamicSupervisor.terminate_child(ABSmartly.ContextSupervisor, context) do
          {:error, :not_found} -> stop_if_alive(context)
          :ok -> :ok
        end

        stop_if_alive(collector)

      :not_found ->
        :ok
    end
  end

  defp stop_if_alive(pid) do
    try do
      GenServer.stop(pid, :normal)
    catch
      :exit, {:noproc, _} -> :ok
      :exit, {:normal, _} -> :ok
    end
  end

  def store_payload(payload_id, data) do
    GenServer.call(__MODULE__, {:store_payload, payload_id, data})
  end

  def get_payload(payload_id) do
    GenServer.call(__MODULE__, {:get_payload, payload_id})
  end

  @impl true
  def init(:ok) do
    contexts = :ets.new(:contexts, [:set, :private])
    payloads = :ets.new(:payloads, [:set, :private])
    flags = :ets.new(:flags, [:set, :private])
    {:ok, %{contexts: contexts, payloads: payloads, flags: flags}}
  end

  @impl true
  def handle_call({:store_context, context_id, context, collector}, _from, state) do
    :ets.insert(state.contexts, {context_id, {context, collector}})
    {:reply, :ok, state}
  end

  def handle_call({:get_context, context_id}, _from, state) do
    case :ets.lookup(state.contexts, context_id) do
      [{^context_id, value}] -> {:reply, {:ok, value}, state}
      [] -> {:reply, {:error, :not_found}, state}
    end
  end

  def handle_call({:arm_publish_fail, pid}, _from, state) do
    :ets.insert(state.flags, {{:publish_fail, pid}, true})
    {:reply, :ok, state}
  end

  def handle_call({:take_publish_fail, pids}, _from, state) do
    taken =
      Enum.any?(pids, fn
        pid when is_pid(pid) -> :ets.take(state.flags, {:publish_fail, pid}) != []
        _ -> false
      end)

    {:reply, taken, state}
  end

  def handle_call({:delete_context, context_id}, _from, state) do
    case :ets.take(state.contexts, context_id) do
      [{^context_id, {ctx, _collector} = value}] ->
        :ets.delete(state.flags, {:publish_fail, ctx})
        {:reply, {:ok, value}, state}

      [] ->
        {:reply, :not_found, state}
    end
  end

  def handle_call({:store_payload, payload_id, data}, _from, state) do
    :ets.insert(state.payloads, {payload_id, data})
    {:reply, :ok, state}
  end

  def handle_call({:get_payload, payload_id}, _from, state) do
    case :ets.lookup(state.payloads, payload_id) do
      [{^payload_id, data}] -> {:reply, {:ok, data}, state}
      [] -> {:reply, {:error, :not_found}, state}
    end
  end
end
