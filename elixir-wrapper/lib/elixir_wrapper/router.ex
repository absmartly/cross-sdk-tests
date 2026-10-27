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
      diagnosticsUtf8: true,
      diagnosticsBase64: true,
      diagnosticsTypeChecks: true,
      diagnosticsIsPromise: true,
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
        "hashUnit" -> ABSmartly.Utils.hash_unit(if is_number(value), do: value, else: text)
        "base64UrlNoPadding" -> ABSmartly.Utils.base64_url_no_padding(text)
        "utf8Bytes" -> :binary.bin_to_list(ABSmartly.Utils.utf8_bytes(text))
        "isObject" -> ABSmartly.Utils.object?(value)
        "isNumeric" -> ABSmartly.Utils.numeric?(value)
        "isPromise" -> ABSmartly.Utils.promise?(value)
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
      %{"mode" => "e2e", "units" => units} = params ->
        attributes = params["attributes"] || %{}
        create_context_e2e(conn, units, attributes)

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
      result = ABSmartly.Context.set_unit(ctx, unit_type, uid)
      send_action_response(conn, ok_to_nil(result), collector, eb)
    end)
  end

  post "/context/:context_id/getUnit" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"unitType" => unit_type} = conn.body_params
      result = ABSmartly.Context.get_unit(ctx, unit_type)
      send_action_response(conn, result, collector, eb)
    end)
  end

  post "/context/:context_id/attribute" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"name" => name, "value" => value} = conn.body_params
      result = ABSmartly.Context.set_attribute(ctx, name, value)
      send_action_response(conn, ok_to_nil(result), collector, eb)
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
      case ABSmartly.Context.variable_keys(ctx) do
        keys when is_map(keys) -> send_action_response(conn, Map.keys(keys), collector, eb)
        error -> send_action_response(conn, error, collector, eb)
      end
    end)
  end

  post "/context/:context_id/track" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"goalName" => goal_name} = conn.body_params
      properties = conn.body_params["properties"]
      result = ABSmartly.Context.track(ctx, goal_name, properties)
      send_action_response(conn, ok_to_nil(result), collector, eb)
    end)
  end

  post "/context/:context_id/override" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"experimentName" => experiment_name, "variant" => variant} = conn.body_params

      result = ABSmartly.Context.set_override(ctx, experiment_name, variant)
      send_action_response(conn, ok_to_nil(result), collector, eb)
    end)
  end

  post "/context/:context_id/setOverride" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"experimentName" => experiment_name, "variant" => variant} = conn.body_params

      result = ABSmartly.Context.set_override(ctx, experiment_name, variant)
      send_action_response(conn, ok_to_nil(result), collector, eb)
    end)
  end

  post "/context/:context_id/customAssignment" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"experimentName" => experiment_name, "variant" => variant} = conn.body_params

      result = ABSmartly.Context.set_custom_assignment(ctx, experiment_name, variant)
      send_action_response(conn, ok_to_nil(result), collector, eb)
    end)
  end

  post "/context/:context_id/setCustomAssignment" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      %{"experimentName" => experiment_name, "variant" => variant} = conn.body_params

      result = ABSmartly.Context.set_custom_assignment(ctx, experiment_name, variant)
      send_action_response(conn, ok_to_nil(result), collector, eb)
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
    with_context_action(conn, fn {ctx, collector, eb} ->
      result = ABSmartly.Context.pending(ctx)
      send_action_response(conn, result, collector, eb)
    end)
  end

  get "/context/:context_id/isFinalized" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      result = ABSmartly.Context.is_finalized?(ctx)
      send_action_response(conn, result, collector, eb)
    end)
  end

  get "/context/:context_id/isReady" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      result = ABSmartly.Context.is_ready?(ctx)
      send_action_response(conn, result, collector, eb)
    end)
  end

  get "/context/:context_id/isFailed" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      result = ABSmartly.Context.is_failed?(ctx)
      send_action_response(conn, result, collector, eb)
    end)
  end

  get "/context/:context_id/experiments" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      result = ABSmartly.Context.experiments(ctx)
      send_action_response(conn, result, collector, eb)
    end)
  end

  post "/context/:context_id/getUnits" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      result = ABSmartly.Context.get_units(ctx)
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
    with_context_action(conn, fn {ctx, collector, eb} ->
      # Spec shape: {"isError": true, "message": "..."} when failed, else null
      # (reference: python-wrapper/server.py, flutter-wrapper/server.dart).
      result =
        case ABSmartly.Context.ready_error(ctx) do
          nil -> nil
          error -> %{isError: true, message: error_message(error)}
        end

      send_action_response(conn, result, collector, eb)
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
    # Arms a one-shot transport failure in the injected publisher
    # (ElixirWrapper.ArmablePublisher). The next /publish runs the REAL
    # ABSmartly.Context.publish/1; whatever the SDK does with the failure is
    # reported as-is.
    with_context_action(conn, fn {ctx, collector, eb} ->
      ContextStore.arm_publish_fail(ctx)
      send_action_response(conn, nil, collector, eb)
    end)
  end

  post "/context/:context_id/publish" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      result = ABSmartly.Context.publish(ctx)
      send_action_response(conn, ok_to_nil(result), collector, eb)
    end)
  end

  post "/context/:context_id/refresh" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      # The orchestrator PUTs newData to this context's payload endpoint first;
      # the SDK re-fetches it through its own client.
      result = ABSmartly.Context.refresh(ctx)
      send_action_response(conn, ok_to_nil(result), collector, eb)
    end)
  end

  post "/context/:context_id/finalize" do
    with_context_action(conn, fn {ctx, collector, eb} ->
      result = ABSmartly.Context.finalize(ctx)
      send_action_response(conn, ok_to_nil(result), collector, eb)
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

  # Contexts must outlive the short-lived HTTP request process that creates them.
  # Context.start_link/3 and start_link_async/3 link the context to the caller, and
  # the context traps exits and shuts down when its parent exits (so it can publish
  # pending events), so a context started from the request process is gone before
  # the next request. Start it under the SDK's own ContextSupervisor instead, exactly
  # like ABSmartly.SDK.create_context*/3 does.
  defp start_supervised_context(sdk_config, start_mfa_args, start_fun) do
    child_spec = %{
      id: ABSmartly.Context,
      start: {ABSmartly.Context, start_fun, start_mfa_args},
      type: :worker,
      restart: :temporary,
      shutdown: ABSmartly.Context.shutdown_timeout(sdk_config)
    }

    DynamicSupervisor.start_child(ABSmartly.ContextSupervisor, child_spec)
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
      environment: "test",
      publisher: ElixirWrapper.ArmablePublisher
    }

    context_config =
      options
      |> Map.put("units", units)
      |> Map.put("publishDelay", -1)
      |> Map.put("refreshPeriod", 0)
      |> Map.put(:event_handler, event_handler)
      |> ABSmartly.Types.ContextConfig.from_options()

    case start_supervised_context(sdk_config, [sdk_config, context_data, context_config], :start_link) do
      {:ok, ctx} ->
        context_id = UUID.uuid4()
        ContextStore.store_context(context_id, ctx, collector)

        result = %{
          contextId: context_id,
          ready: ABSmartly.Context.is_ready?(ctx),
          failed: ABSmartly.Context.is_failed?(ctx),
          finalized: ABSmartly.Context.is_finalized?(ctx)
        }

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
      environment: "test",
      publisher: ElixirWrapper.ArmablePublisher
    }

    context_options =
      options
      |> Map.put("units", units)
      |> Map.put("publishDelay", -1)
      |> Map.put("refreshPeriod", 0)
      |> Map.put(:event_handler, event_handler)

    context_config = ABSmartly.Types.ContextConfig.from_options(context_options)

    # The SDK fetches through its own HTTP client; payloadThrottle only delays
    # that call.
    start_opts =
      if payload_throttle > 0 do
        [
          data_fetcher: fn ->
            Process.sleep(payload_throttle)

            ABSmartly.HTTP.Client.fetch_context(
              sdk_config.endpoint,
              sdk_config.api_key,
              sdk_config.application,
              sdk_config.environment,
              sdk_config.retries,
              sdk_config.timeout
            )
          end
        ]
      else
        []
      end

    case start_supervised_context(sdk_config, [sdk_config, context_config, start_opts], :start_link_async) do
      {:ok, ctx} ->
        if payload_throttle == 0 do
          ABSmartly.Context.wait_until_ready(ctx, 10000)
        end

        context_id = UUID.uuid4()
        ContextStore.store_context(context_id, ctx, collector)

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
      environment: "test",
      publisher: ElixirWrapper.ArmablePublisher
    }

    context_options =
      options
      |> Map.put("units", units)
      |> Map.put("publishDelay", -1)
      |> Map.put("refreshPeriod", 0)
      |> Map.put(:event_handler, event_handler)

    context_config = ABSmartly.Types.ContextConfig.from_options(context_options)

    # Spec: "configure the SDK with ... a data provider that immediately returns
    # an error". The SDK's own async-fetch path (Context.handle_continue
    # :fetch_async -> handle_info {:fetch_async_complete, {:error, _}}) sets
    # failed + failed_reason itself. The wrapper does not touch failed state,
    # does not fabricate events, and reports SDK-observed state verbatim.
    data_fetcher = fn -> {:error, "Context load failed"} end

    case start_supervised_context(sdk_config, [sdk_config, context_config, [data_fetcher: data_fetcher]], :start_link_async) do
      {:ok, ctx} ->
        # wait_until_ready replies {:error, reason} once the SDK processes the
        # failed fetch (or immediately if it already has).
        _ = ABSmartly.Context.wait_until_ready(ctx, 5000)

        context_id = UUID.uuid4()
        ContextStore.store_context(context_id, ctx, collector)

        events = EventCollector.get_all(collector)

        result = %{
          contextId: context_id,
          ready: ABSmartly.Context.is_ready?(ctx),
          failed: ABSmartly.Context.is_failed?(ctx),
          finalized: ABSmartly.Context.is_finalized?(ctx)
        }

        send_json(conn, 200, %{result: result, events: events})

      {:error, reason} ->
        send_error(conn, 500, "Failed to create failed context: #{inspect(reason)}")
    end
  end

  defp create_context_e2e(conn, units, attributes) do
    e2e_endpoint = System.get_env("ABSMARTLY_E2E_ENDPOINT")
    e2e_api_key = System.get_env("ABSMARTLY_E2E_API_KEY")
    e2e_app = System.get_env("ABSMARTLY_E2E_APPLICATION")
    e2e_env = System.get_env("ABSMARTLY_E2E_ENVIRONMENT")

    if is_nil(e2e_endpoint) or is_nil(e2e_api_key) or is_nil(e2e_app) or is_nil(e2e_env) do
      send_error(conn, 501, "e2e mode not configured")
    else
      collector = EventCollector.new()

      event_handler = fn event_type, event_data ->
        EventCollector.push(collector, event_type, event_data)
      end

      sdk_config = %ABSmartly.Types.SDKConfig{
        endpoint: e2e_endpoint,
        api_key: e2e_api_key,
        application: e2e_app,
        environment: e2e_env,
        publisher: ElixirWrapper.ArmableE2EPublisher
      }

      context_options =
        %{}
        |> Map.put("units", units)
        |> Map.put("publishDelay", -1)
        |> Map.put("refreshPeriod", 0)
        |> Map.put(:event_handler, event_handler)

      context_config = ABSmartly.Types.ContextConfig.from_options(context_options)

      case start_supervised_context(sdk_config, [sdk_config, context_config], :start_link_async) do
        {:ok, ctx} ->
          ABSmartly.Context.wait_until_ready(ctx, 10000)

          Enum.each(attributes, fn {name, value} ->
            ABSmartly.Context.set_attribute(ctx, name, value)
          end)

          context_id = UUID.uuid4()
          ContextStore.store_context(context_id, ctx, collector)

          events = EventCollector.get_all(collector)

          result = %{
            contextId: context_id,
            ready: ABSmartly.Context.is_ready?(ctx),
            failed: ABSmartly.Context.is_failed?(ctx),
            finalized: ABSmartly.Context.is_finalized?(ctx)
          }

          send_json(conn, 200, %{result: result, events: events})

        {:error, reason} ->
          send_error(conn, 500, "Failed to create e2e context: #{inspect(reason)}")
      end
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
            send_error(conn, 400, Exception.message(e), EventCollector.get_since(collector, events_before))
        end

      {:error, _} ->
        send_error(conn, 404, "Context not found")
    end
  end

  # SDK read methods return {:error, message} (e.g. "ABsmartly Context is not
  # yet ready.") instead of raising; surface it as the normal 400 error.
  defp send_action_response(conn, {:error, reason}, collector, events_before) do
    send_error(conn, 400, error_message(reason), EventCollector.get_since(collector, events_before))
  end

  defp send_action_response(conn, result, collector, events_before) do
    events = EventCollector.get_since(collector, events_before)
    send_json(conn, 200, %{result: result, events: events})
  end

  defp ok_to_nil(:ok), do: nil
  defp ok_to_nil(other), do: other

  defp send_json(conn, status, data) do
    conn
    |> put_resp_content_type("application/json")
    |> send_resp(status, Jason.encode!(data))
  end

  defp send_error(conn, status, message, events \\ []) do
    conn
    |> put_resp_content_type("application/json")
    |> send_resp(status, Jason.encode!(%{error: message, events: events}))
  end

  # Normalize an SDK {:error, reason} reason into a human-readable string.
  defp error_message(reason) when is_binary(reason), do: reason
  defp error_message(reason) when is_atom(reason), do: to_string(reason)
  defp error_message(reason), do: inspect(reason)

  @impl Plug.ErrorHandler
  def handle_errors(conn, %{kind: _kind, reason: _reason, stack: _stack}) do
    send_resp(conn, conn.status, "Something went wrong")
  end
end
