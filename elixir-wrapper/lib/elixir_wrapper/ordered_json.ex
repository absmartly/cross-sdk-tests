defmodule ElixirWrapper.OrderedJson do
  @moduledoc """
  Plug JSON decoder that keeps each experiment's `switchbackClusters` in its
  JSON order (the order decides which unit serves a switchback), so scenario
  payloads reach the SDK, and are served back to its fetch, as the collector
  sends them. Every other object, an attribute value included, decodes as
  `Jason.decode/1` decodes it. Uses the SDK's own `ABSmartly.Switchback.Json` when the SDK has
  it, and `Jason` otherwise, so the wrapper still builds against an SDK
  without switchbacks.
  """

  def decode!(body) do
    case decode(body) do
      {:ok, value} -> value
      {:error, error} -> raise error
    end
  end

  def decode(body) do
    if Code.ensure_loaded?(ABSmartly.Switchback.Json),
      do: apply(ABSmartly.Switchback.Json, :decode, [body]),
      else: Jason.decode(body)
  end
end
