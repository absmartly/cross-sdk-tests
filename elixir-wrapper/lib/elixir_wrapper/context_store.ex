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

  def set_publish_fail(context_id, value) do
    GenServer.call(__MODULE__, {:set_publish_fail, context_id, value})
  end

  def get_publish_fail(context_id) do
    GenServer.call(__MODULE__, {:get_publish_fail, context_id})
  end

  def delete_context(context_id) do
    GenServer.call(__MODULE__, {:delete_context, context_id})
  end

  def store_payload(payload_id, data) do
    GenServer.call(__MODULE__, {:store_payload, payload_id, data})
  end

  def get_payload(payload_id) do
    GenServer.call(__MODULE__, {:get_payload, payload_id})
  end

  @doc """
  Arm per-payload HTTP fault injection: the next `fail_times` calls to
  `take_fault/1` for this payload report the given status. Used to drive the
  SDK's real retry/bail logic over the live-fetch path (scenarios 68-70).
  """
  def set_fault(payload_id, fail_times, status) do
    GenServer.call(__MODULE__, {:set_fault, payload_id, fail_times, status})
  end

  @doc """
  Consume one fault for this payload. Returns `{:fault, status}` while the
  armed count has not been exhausted, `:ok` once it has (or was never armed).
  The increment happens inside the GenServer so concurrent SDK retries cannot
  race and over-consume the budget.
  """
  def take_fault(payload_id) do
    GenServer.call(__MODULE__, {:take_fault, payload_id})
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

  def handle_call({:set_publish_fail, context_id, value}, _from, state) do
    :ets.insert(state.flags, {{:publish_fail, context_id}, value})
    {:reply, :ok, state}
  end

  def handle_call({:get_publish_fail, context_id}, _from, state) do
    case :ets.lookup(state.flags, {:publish_fail, context_id}) do
      [{_, value}] -> {:reply, value, state}
      [] -> {:reply, false, state}
    end
  end

  def handle_call({:delete_context, context_id}, _from, state) do
    :ets.delete(state.contexts, context_id)
    :ets.delete(state.flags, {:publish_fail, context_id})
    {:reply, :ok, state}
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

  def handle_call({:set_fault, payload_id, fail_times, status}, _from, state) do
    :ets.insert(state.flags, {{:fault, payload_id}, {fail_times, status, 0}})
    {:reply, :ok, state}
  end

  def handle_call({:take_fault, payload_id}, _from, state) do
    case :ets.lookup(state.flags, {:fault, payload_id}) do
      [{key, {fail_times, status, count}}] when count < fail_times ->
        :ets.insert(state.flags, {key, {fail_times, status, count + 1}})
        {:reply, {:fault, status}, state}

      _ ->
        {:reply, :ok, state}
    end
  end
end
