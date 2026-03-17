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
    send_json(conn, 200, %{diagnostics: true,
      attrsSeq: false,
      publishFail: true,
      variableKeysMap: true,
      globalCustomFieldKeys: true,
      getUnits: true,
      getAttributes: true,
      readyError: true
    })
  end

  post "/diagnostic" do
    op = conn.body_params["operation"]
    value = conn.body_params["value"]
    text =
      cond do
        is_nil(value) -> ""
        is_binary(value) -> value
        is_number(value) -> to_string(value)
        is_boolean(value) -> to_string(value)
        true -> Jason.encode!(value)
      end

    result =
      case op do
        "hashUnit" -> ABSmartly.Utils.hash_unit(text)
        "base64UrlNoPadding" -> Base.url_encode64(text, padding: false)
        "utf8Bytes" -> :binary.bin_to_list(text)
        "isObject" -> is_map(value)
        "isNumeric" -> is_number(value)
        "isPromise" -> false
        _ -> :unsupported
      end

    case result do
      :unsupported ->
        send_error(conn, 400, "Unsupported diagnostic operation: #{inspect(op)}")

      _ ->
        send_json(conn, 200, %{result: result, events: []})
    end
  end

  post "/context" do
    case conn.body_params do
      %{"data" => data, "units" => units} = params ->
        options = params["options"] || %{}
        create_context_sync(conn, data, units, options)

      %{"failLoad" => true, "units" => units} = params ->
        options = params["options"] || %{}
        create_context_failed(conn, units, options)

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
      if not ABSmartly.Context.is_ready?(ctx) do
        send_json(conn, 200, %{result: 0, events: []})
      else
        case ABSmartly.Context.treatment(ctx, experiment_name) do
          {:error, :finalized} ->
            send_error(conn, 400, "Context finalized")

          {:error, reason} ->
            send_error(conn, 400, inspect(reason))

          result ->
            send_action_response(conn, result, collector, eb)
        end
      end
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
      if not ABSmartly.Context.is_ready?(ctx) do
        send_json(conn, 200, %{result: default_value, events: []})
      else
        result = ABSmartly.Context.variable_value(ctx, key, default_value)
        send_action_response(conn, result, collector, eb)
      end
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
      if not ABSmartly.Context.is_ready?(ctx) do
        send_json(conn, 200, %{result: [], events: []})
      else
        result = ABSmartly.Context.variable_keys(ctx) |> Map.keys()
        send_action_response(conn, result, collector, eb)
      end
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
      result = ABSmartly.Context.custom_field_keys(ctx)
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

  post "/context/:context_id/getUnits" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      result = ABSmartly.Context.get_units(ctx)
      result = Enum.into(result, %{}, fn {k, v} -> {k, maybe_parse_number(v)} end)
      send_action_response(conn, result, collector, eb)
    end)
  end

  post "/context/:context_id/getAttributes" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      attrs = ABSmartly.Context.get_attributes(ctx)
      result = Enum.into(attrs, %{}, fn entry -> {entry.name, entry.value} end)
      send_action_response(conn, result, collector, eb)
    end)
  end

  post "/context/:context_id/readyError" do
    with_context_action(conn, fn {ctx, _collector, _eb} ->
      result = ABSmartly.Context.ready_error(ctx)
      send_json(conn, 200, %{result: result, events: []})
    end)
  end

  post "/context/:context_id/variableKeysMap" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      result = ABSmartly.Context.variable_keys(ctx)
      send_action_response(conn, result, collector, eb)
    end)
  end

  post "/context/:context_id/globalCustomFieldKeys" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      result = ABSmartly.Context.custom_field_keys(ctx)
      send_action_response(conn, result, collector, eb)
    end)
  end

  post "/context/:context_id/publishFail" do
    context_id = conn.path_params["context_id"]
    ContextStore.set_publish_fail(context_id, true)
    send_json(conn, 200, %{result: nil, events: []})
  end

  post "/context/:context_id/publish" do
    context_id = conn.path_params["context_id"]
    should_fail = ContextStore.get_publish_fail(context_id)

    if should_fail do
      ContextStore.set_publish_fail(context_id, false)

      with_context_action(conn, fn {_ctx, collector, eb} ->
        EventCollector.push(collector, "error", %{message: "Publish failed"})
        send_action_response(conn, nil, collector, eb)
      end)
    else
      with_context_action(conn, fn {ctx, collector, eb} ->
        ABSmartly.Context.publish(ctx)
        send_action_response(conn, nil, collector, eb)
      end)
    end
  end

  post "/context/:context_id/refresh" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      ABSmartly.Context.refresh(ctx)
      send_action_response(conn, nil, collector, eb)
    end)
  end

  post "/context/:context_id/finalize" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      ABSmartly.Context.finalize(ctx)
      case wait_for_finalized(ctx) do
        :ok ->
          send_action_response(conn, nil, collector, eb)

        {:error, :timeout} ->
          send_error(conn, 500, "Context did not finalize within timeout")
      end
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
    collector = EventCollector.new()

    event_handler = fn event_type, event_data ->
      EventCollector.push(collector, event_type, event_data)
    end

    payload_throttle = options["payloadThrottle"] || 0
    translated_endpoint = translate_endpoint(endpoint)

    sdk_config = %ABSmartly.Types.SDKConfig{
      endpoint: translated_endpoint,
      api_key: "test-key",
      application: "test-app",
      environment: "test"
    }

    context_options =
      options
      |> Map.put("units", units)
      |> Map.put("publishDelay", -1)
      |> Map.put("refreshPeriod", 0)
      |> Map.put(:event_handler, event_handler)

    context_config = ABSmartly.Types.ContextConfig.from_options(context_options)

    case ABSmartly.Context.start_link_async(sdk_config, context_config) do
      {:ok, ctx} ->
        Task.start(fn ->
          if payload_throttle > 0, do: Process.sleep(payload_throttle)
          case HTTPoison.get(translated_endpoint <> "/context") do
            {:ok, %{status_code: 200, body: body}} ->
              case Jason.decode(body) do
                {:ok, data} -> ABSmartly.Context.set_data(ctx, data)
                _ -> :error
              end
            _ -> :error
          end
        end)

        if payload_throttle == 0 do
          ABSmartly.Context.wait_until_ready(ctx, 10000)
        end

        context_id = UUID.uuid4()
        ContextStore.store_context(context_id, ctx, collector)

        Process.sleep(10)
        events = EventCollector.get_all(collector)

        result = %{
          contextId: context_id,
          ready: ABSmartly.Context.is_ready?(ctx),
          failed: ABSmartly.Context.is_failed?(ctx),
          finalized: ABSmartly.Context.is_finalized?(ctx)
        }

        send_json(conn, 200, %{result: result, events: events})

      {:error, reason} ->
        send_error(conn, 500, "Failed to create async context: #{inspect(reason)}")
    end
  end

  defp create_context_failed(conn, units, options) do
    collector = EventCollector.new()

    event_handler = fn event_type, event_data ->
      EventCollector.push(collector, event_type, event_data)
    end

    sdk_config = %ABSmartly.Types.SDKConfig{
      endpoint: "http://localhost:3000",
      api_key: "test-key",
      application: "test-app",
      environment: "test"
    }

    context_options =
      options
      |> Map.put("units", units)
      |> Map.put("publishDelay", -1)
      |> Map.put("refreshPeriod", 0)
      |> Map.put(:event_handler, event_handler)

    context_config = ABSmartly.Types.ContextConfig.from_options(context_options)

    case ABSmartly.Context.start_link_async(sdk_config, context_config) do
      {:ok, ctx} ->
        ABSmartly.Context.set_failed(ctx, "Context load failed")
        EventCollector.push(collector, "error", %{message: "Context load failed"})
        Process.sleep(50)

        context_id = UUID.uuid4()
        ContextStore.store_context(context_id, ctx, collector)

        events = EventCollector.get_all(collector)

        result = %{
          contextId: context_id,
          ready: false,
          failed: true,
          finalized: false
        }

        send_json(conn, 200, %{result: result, events: events})

      {:error, reason} ->
        send_error(conn, 500, "Failed to create failed context: #{inspect(reason)}")
    end
  end

  defp translate_endpoint(endpoint) do
    String.replace(endpoint, ~r/localhost:\d+/, "127.0.0.1:3000")
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

  defp wait_for_finalized(ctx, retries \\ 1000)
  defp wait_for_finalized(_ctx, 0), do: {:error, :timeout}
  defp wait_for_finalized(ctx, retries) do
    if ABSmartly.Context.is_finalized?(ctx) do
      :ok
    else
      Process.sleep(5)
      wait_for_finalized(ctx, retries - 1)
    end
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
