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

  def delete_context(context_id) do
    GenServer.call(__MODULE__, {:delete_context, context_id})
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
    {:ok, %{contexts: contexts, payloads: payloads}}
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

  def handle_call({:delete_context, context_id}, _from, state) do
    :ets.delete(state.contexts, context_id)
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
end
