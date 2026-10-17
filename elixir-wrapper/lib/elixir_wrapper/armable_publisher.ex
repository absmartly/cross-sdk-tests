defmodule ElixirWrapper.ArmablePublisher do
  @moduledoc """
  `ABSmartly.ContextPublisher` injected through the SDK's public
  `SDKConfig.publisher` hook.

  By default it delegates to the SDK's own `ABSmartly.DefaultContextPublisher`
  (unchanged transport). `POST /context/:id/publishFail` arms a ONE-SHOT
  transport failure for that context's GenServer pid; the next real
  `ABSmartly.Context.publish/1` call then reaches this publisher, which returns
  `{:error, "Publish failed"}`. What the SDK does with that failure (surface it,
  preserve pending) is entirely the SDK's behaviour - the wrapper never fakes the
  publish result.

  The SDK calls the publisher from a `Task.async` spawned by the context
  GenServer (or from the GenServer itself on terminate), so the owning context
  pid is found via `self()` / the `:"$callers"` process-dictionary chain.
  """

  @behaviour ABSmartly.ContextPublisher

  alias ElixirWrapper.ContextStore

  @impl ABSmartly.ContextPublisher
  def publish(endpoint, api_key, application, environment, event_map, retries) do
    callers = [self() | Process.get(:"$callers", [])]

    if ContextStore.take_publish_fail(callers) do
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
