defmodule ElixirWrapper.EventCollector do
  @moduledoc """
  Collects events from Context for test assertions.
  """

  use Agent

  def new do
    {:ok, agent} = Agent.start_link(fn -> [] end)
    agent
  end

  def push(agent, event_type, data) do
    event = %{
      type: to_string(event_type),
      data: deep_copy(data),
      timestamp: System.system_time(:millisecond)
    }

    Agent.update(agent, fn events -> events ++ [event] end)
  end

  def get_all(agent) do
    Agent.get(agent, & &1)
  end

  def count(agent) do
    Agent.get(agent, &length/1)
  end

  def get_since(agent, index) do
    Agent.get(agent, fn events -> Enum.slice(events, index..-1//1) end)
  end

  defp deep_copy(data) do
    converted = convert_structs(data)
    case Jason.encode(converted) do
      {:ok, json} ->
        case Jason.decode(json) do
          {:ok, decoded} -> decoded
          _ -> converted
        end

      _ ->
        converted
    end
  end

  defp convert_structs(%ABSmartly.Types.Exposure{} = e), do: ABSmartly.Types.Exposure.to_map(e)
  defp convert_structs(%ABSmartly.Types.Goal{} = g), do: ABSmartly.Types.Goal.to_map(g)
  defp convert_structs(%ABSmartly.Types.PublishEvent{} = p), do: ABSmartly.Types.PublishEvent.to_map(p)
  defp convert_structs(%{__struct__: _} = s), do: Map.from_struct(s) |> convert_structs()
  defp convert_structs(%{} = map), do: Map.new(map, fn {k, v} -> {k, convert_structs(v)} end)
  defp convert_structs(list) when is_list(list), do: Enum.map(list, &convert_structs/1)
  defp convert_structs(other), do: other
end
