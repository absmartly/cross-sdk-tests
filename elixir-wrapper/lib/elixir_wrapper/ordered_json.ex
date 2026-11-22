defmodule ElixirWrapper.OrderedJson do
  @moduledoc """
  Plug JSON decoder for the wrapper's request bodies.

  A body decodes as `Jason.decode/1` decodes it, except the context data a
  scenario sends in its `data` member (`POST /context`, `PUT
  /context_payload/:id`): there each experiment's `switchbackClusters` keeps
  its JSON order, which decides the serving unit, so the payload reaches the
  SDK, and is served back to its fetch, as the collector sends it. Every
  other member, an attribute value included, is plain JSON. Uses the SDK's
  own `ABSmartly.Switchback.Json` when the SDK has it, so the wrapper still
  builds against an SDK without switchbacks.
  """

  def decode!(body) do
    case decode(body) do
      {:ok, value} -> value
      {:error, error} -> raise error
    end
  end

  def decode(body) do
    with {:ok, %{"data" => %{"experiments" => experiments}} = decoded} when is_list(experiments) <-
           Jason.decode(body),
         true <- Code.ensure_loaded?(ABSmartly.Switchback.Json) do
      {:ok, %{decoded | "data" => ordered_data(body)}}
    else
      {:ok, decoded} -> {:ok, decoded}
      {:error, _} = error -> error
      false -> Jason.decode(body)
    end
  end

  @doc """
  Decodes a context data body (`{"experiments": [...]}`) as the SDK's fetch
  decodes it, with each experiment's `switchbackClusters` in its JSON order.
  """
  def decode_context_data(body) do
    if Code.ensure_loaded?(ABSmartly.Switchback.Json),
      do: apply(ABSmartly.Switchback.Json, :decode, [body]),
      else: Jason.decode(body)
  end

  # The `data` member, decoded on its own as context data: its experiments
  # keep their cluster order, nothing else in the body is touched.
  defp ordered_data(body) do
    {:ok, %Jason.OrderedObject{values: members}} =
      Jason.decode(body, objects: :ordered_objects)

    {"data", data} = List.keyfind(members, "data", 0)
    {:ok, ordered} = decode_context_data(Jason.encode!(data))
    ordered
  end
end
