defmodule ElixirWrapper.ArmablePublisher do
  @moduledoc """
  `ABSmartly.ContextPublisher` injected through the SDK's public
  `SDKConfig.publisher` hook.

  Test contexts have no collector, so by default the transport succeeds without
  sending anything, like the Go and Scala wrappers' publishers. e2e contexts use
  `ElixirWrapper.ArmableE2EPublisher`, which forwards to the SDK's real one. `POST /context/:id/publishFail`
  arms a ONE-SHOT transport failure for that context's GenServer pid; the next
  real `ABSmartly.Context.publish/1` call then reaches this publisher, which
  returns `{:error, "Publish failed"}`. What the SDK does with that failure
  (surface it, preserve pending) is entirely the SDK's behaviour - the wrapper
  never fakes the publish result.

  The SDK calls the publisher from a `Task.async` spawned by the context
  GenServer (or from the GenServer itself on terminate), so the owning context
  pid is found via `self()` / the `:"$callers"` process-dictionary chain.
  """

  @behaviour ABSmartly.ContextPublisher

  alias ElixirWrapper.ContextStore

  @impl ABSmartly.ContextPublisher
  def publish(_endpoint, _api_key, _application, _environment, _event_map, _retries) do
    if armed?(), do: {:error, "Publish failed"}, else: :ok
  end

  @doc false
  def armed? do
    ContextStore.take_publish_fail([self() | Process.get(:"$callers", [])])
  end
end

defmodule ElixirWrapper.ArmableE2EPublisher do
  @moduledoc """
  e2e counterpart of `ElixirWrapper.ArmablePublisher`: an armed
  `/publishFail` fails the next publish once; otherwise the publish goes to the
  SDK's real `ABSmartly.DefaultContextPublisher` transport.
  """

  @behaviour ABSmartly.ContextPublisher

  @impl ABSmartly.ContextPublisher
  def publish(endpoint, api_key, application, environment, event_map, retries) do
    if ElixirWrapper.ArmablePublisher.armed?() do
      {:error, "Publish failed"}
    else
      ABSmartly.DefaultContextPublisher.publish(
        endpoint,
        api_key,
        application,
        environment,
        event_map,
        retries
      )
    end
  end
end
