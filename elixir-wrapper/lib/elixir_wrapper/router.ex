defmodule ElixirWrapper.Router do
  use Plug.Router
  use Plug.ErrorHandler

  alias ElixirWrapper.{ContextStore, EventCollector}

  plug(Plug.Logger)
  plug(:match)

  plug(Plug.Parsers,
    parsers: [:json],
    pass: ["application/json"],
    json_decoder: Jason
  )

  plug(:dispatch)

  get "/health" do
    send_json(conn, 200, %{
      status: "healthy",
      sdk: "elixir",
      version: "1.0.0"
    })
  end

  get "/capabilities" do
    send_json(conn, 200, %{
      asyncContext: false,
      attrsSeq: false
    })
  end

  post "/context" do
    case conn.body_params do
      %{"data" => data, "units" => units} = params ->
        options = params["options"] || %{}
        create_context_sync(conn, data, units, options)

      %{"endpoint" => endpoint, "units" => units} = params ->
        options = params["options"] || %{}
        create_context_async(conn, endpoint, units, options)

      _ ->
        send_error(conn, 400, "Missing required parameters")
    end
  end

  put "/context_payload/:payload_id" do
    payload_id = conn.path_params["payload_id"]

    case conn.body_params do
      %{"data" => data} ->
        ContextStore.store_payload(payload_id, data)
        send_json(conn, 200, %{success: true})

      _ ->
        send_error(conn, 400, "Missing data parameter")
    end
  end

  get "/context_payload/:payload_id/context" do
    payload_id = conn.path_params["payload_id"]

    case ContextStore.get_payload(payload_id) do
      {:ok, data} ->
        send_json(conn, 200, data)

      {:error, _} ->
        send_error(conn, 404, "Payload not found")
    end
  end

  post "/context/:context_id/setUnit" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"unitType" => unit_type, "uid" => uid} = conn.body_params
      uid_str = if is_number(uid), do: to_string(uid), else: uid
      ABSmartly.Context.set_unit(ctx, unit_type, uid_str)
      send_action_response(conn, nil, collector, eb)
    end)
  end

  post "/context/:context_id/getUnit" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"unitType" => unit_type} = conn.body_params
      result = ABSmartly.Context.get_unit(ctx, unit_type)
      result = maybe_parse_number(result)
      send_action_response(conn, result, collector, eb)
    end)
  end

  post "/context/:context_id/attribute" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"name" => name, "value" => value} = conn.body_params
      ABSmartly.Context.set_attribute(ctx, name, value)
      send_action_response(conn, nil, collector, eb)
    end)
  end

  post "/context/:context_id/getAttribute" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"name" => name} = conn.body_params
      result = ABSmartly.Context.get_attribute(ctx, name)
      send_action_response(conn, result, collector, eb)
    end)
  end

  post "/context/:context_id/treatment" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"experimentName" => experiment_name} = conn.body_params
      result = ABSmartly.Context.treatment(ctx, experiment_name)
      send_action_response(conn, result, collector, eb)
    end)
  end

  post "/context/:context_id/peek" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"experimentName" => experiment_name} = conn.body_params
      result = ABSmartly.Context.peek(ctx, experiment_name)
      send_action_response(conn, result, collector, eb)
    end)
  end

  post "/context/:context_id/variableValue" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"key" => key, "defaultValue" => default_value} = conn.body_params
      result = ABSmartly.Context.variable_value(ctx, key, default_value)
      send_action_response(conn, result, collector, eb)
    end)
  end

  post "/context/:context_id/peekVariableValue" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"key" => key, "defaultValue" => default_value} = conn.body_params
      result = ABSmartly.Context.peek_variable_value(ctx, key, default_value)
      send_action_response(conn, result, collector, eb)
    end)
  end

  post "/context/:context_id/variableKeys" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      result = ABSmartly.Context.variable_keys(ctx)
      send_action_response(conn, result, collector, eb)
    end)
  end

  post "/context/:context_id/track" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"goalName" => goal_name} = conn.body_params
      properties = conn.body_params["properties"]
      ABSmartly.Context.track(ctx, goal_name, properties)
      send_action_response(conn, nil, collector, eb)
    end)
  end

  post "/context/:context_id/override" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"experimentName" => experiment_name, "variant" => variant} = conn.body_params
      ABSmartly.Context.set_override(ctx, experiment_name, variant)
      send_action_response(conn, nil, collector, eb)
    end)
  end

  post "/context/:context_id/setOverride" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"experimentName" => experiment_name, "variant" => variant} = conn.body_params
      ABSmartly.Context.set_override(ctx, experiment_name, variant)
      send_action_response(conn, nil, collector, eb)
    end)
  end

  post "/context/:context_id/customAssignment" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"experimentName" => experiment_name, "variant" => variant} = conn.body_params
      ABSmartly.Context.set_custom_assignment(ctx, experiment_name, variant)
      send_action_response(conn, nil, collector, eb)
    end)
  end

  post "/context/:context_id/setCustomAssignment" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"experimentName" => experiment_name, "variant" => variant} = conn.body_params
      ABSmartly.Context.set_custom_assignment(ctx, experiment_name, variant)
      send_action_response(conn, nil, collector, eb)
    end)
  end

  post "/context/:context_id/customFieldValue" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"experimentName" => experiment_name, "fieldName" => field_name} = conn.body_params
      result = ABSmartly.Context.custom_field_value(ctx, experiment_name, field_name)
      send_action_response(conn, result, collector, eb)
    end)
  end

  post "/context/:context_id/customFieldKeys" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"experimentName" => experiment_name} = conn.body_params
      result = ABSmartly.Context.custom_field_keys(ctx, experiment_name)
      send_action_response(conn, result, collector, eb)
    end)
  end

  post "/context/:context_id/customFieldValueType" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"experimentName" => experiment_name, "fieldName" => field_name} = conn.body_params
      result = ABSmartly.Context.custom_field_value_type(ctx, experiment_name, field_name)
      send_action_response(conn, result, collector, eb)
    end)
  end

  get "/context/:context_id/pending" do
    with_context_action(conn, fn {ctx, _collector, _eb} ->
      result = ABSmartly.Context.pending(ctx)
      send_json(conn, 200, %{result: result, events: []})
    end)
  end

  get "/context/:context_id/isFinalized" do
    with_context_action(conn, fn {ctx, _collector, _eb} ->
      result = ABSmartly.Context.is_finalized?(ctx)
      send_json(conn, 200, %{result: result, events: []})
    end)
  end

  get "/context/:context_id/isReady" do
    with_context_action(conn, fn {ctx, _collector, _eb} ->
      result = ABSmartly.Context.is_ready?(ctx)
      send_json(conn, 200, %{result: result, events: []})
    end)
  end

  get "/context/:context_id/isFailed" do
    with_context_action(conn, fn {ctx, _collector, _eb} ->
      result = ABSmartly.Context.is_failed?(ctx)
      send_json(conn, 200, %{result: result, events: []})
    end)
  end

  get "/context/:context_id/experiments" do
    with_context_action(conn, fn {ctx, _collector, _eb} ->
      result = ABSmartly.Context.experiments(ctx)
      send_json(conn, 200, %{result: result, events: []})
    end)
  end

  post "/context/:context_id/publish" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      ABSmartly.Context.publish(ctx)
      send_action_response(conn, nil, collector, eb)
    end)
  end

  post "/context/:context_id/refresh" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"newData" => new_data} = conn.body_params
      ABSmartly.Context.refresh(ctx, new_data)
      send_action_response(conn, nil, collector, eb)
    end)
  end

  post "/context/:context_id/finalize" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      ABSmartly.Context.finalize(ctx)
      send_action_response(conn, nil, collector, eb)
    end)
  end

  delete "/context/:context_id" do
    context_id = conn.path_params["context_id"]
    ContextStore.delete_context(context_id)
    send_json(conn, 200, %{result: "deleted"})
  end

  match _ do
    send_resp(conn, 404, "Not found")
  end

  defp create_context_sync(conn, data, units, options) do
    collector = EventCollector.new()

    event_handler = fn event_type, event_data ->
      EventCollector.push(collector, event_type, event_data)
    end

    context_data = ABSmartly.Types.ContextData.from_map(data)

    sdk_config = %ABSmartly.Types.SDKConfig{
      endpoint: "http://localhost:3000",
      api_key: "test-key",
      application: "test-app",
      environment: "test"
    }

    context_config =
      options
      |> Map.put("units", units)
      |> Map.put("publishDelay", -1)
      |> Map.put("refreshPeriod", 0)
      |> Map.put(:event_handler, event_handler)
      |> ABSmartly.Types.ContextConfig.from_options()

    case ABSmartly.Context.start_link(sdk_config, context_data, context_config) do
      {:ok, ctx} ->
        context_id = UUID.uuid4()
        ContextStore.store_context(context_id, ctx, collector)

        result = %{
          contextId: context_id,
          ready: ABSmartly.Context.is_ready?(ctx),
          failed: ABSmartly.Context.is_failed?(ctx),
          finalized: ABSmartly.Context.is_finalized?(ctx)
        }

        Process.sleep(10)
        events = EventCollector.get_all(collector)
        send_json(conn, 200, %{result: result, events: events})

      {:error, reason} ->
        send_error(conn, 500, "Failed to create context: #{inspect(reason)}")
    end
  end

  defp create_context_async(conn, endpoint, units, options) do
    payload_id = endpoint |> String.split("/context_payload/") |> List.last()
    case ContextStore.get_payload(payload_id) do
      {:ok, data} ->
        create_context_sync(conn, data, units, options)
      {:error, _} ->
        send_error(conn, 404, "Payload not found for endpoint: #{endpoint}")
    end
  end

  defp with_context_action(conn, func) do
    context_id = conn.path_params["context_id"]

    case ContextStore.get_context(context_id) do
      {:ok, {ctx, collector}} ->
        events_before = EventCollector.count(collector)
        try do
          func.({ctx, collector, events_before})
        rescue
          e ->
            send_error(conn, 400, Exception.message(e))
        end

      {:error, _} ->
        send_error(conn, 404, "Context not found")
    end
  end

  defp send_action_response(conn, result, collector, events_before) do
    Process.sleep(10)
    events = EventCollector.get_since(collector, events_before)
    send_json(conn, 200, %{result: result, events: events})
  end

  defp send_json(conn, status, data) do
    conn
    |> put_resp_content_type("application/json")
    |> send_resp(status, Jason.encode!(data))
  end

  defp send_error(conn, status, message) do
    conn
    |> put_resp_content_type("application/json")
    |> send_resp(status, Jason.encode!(%{error: message}))
  end

  defp maybe_parse_number(nil), do: nil
  defp maybe_parse_number(str) when is_binary(str) do
    case Integer.parse(str) do
      {int, ""} -> int
      _ ->
        case Float.parse(str) do
          {float, ""} -> float
          _ -> str
        end
    end
  end
  defp maybe_parse_number(other), do: other

  @impl Plug.ErrorHandler
  def handle_errors(conn, %{kind: _kind, reason: _reason, stack: _stack}) do
    send_resp(conn, conn.status, "Something went wrong")
  end
end
