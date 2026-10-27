using System.Collections.Concurrent;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using ABSmartly.Internal.Hashing;
using ABSmartly;
using ABSmartly.Models;
using Microsoft.AspNetCore.Mvc;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddControllers()
    .AddJsonOptions(options =>
    {
        options.JsonSerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.CamelCase;
        options.JsonSerializerOptions.DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull;
    });

var app = builder.Build();

app.Use(async (context, next) =>
{
    try
    {
        await next();
    }
    catch (Exception ex)
    {
        Console.WriteLine($"Global Exception: {ex.Message}");
        Console.WriteLine($"Type: {ex.GetType().Name}");
        Console.WriteLine($"StackTrace: {ex.StackTrace}");
        if (ex.InnerException != null)
        {
            Console.WriteLine($"Inner Exception: {ex.InnerException.Message}");
            Console.WriteLine($"Inner Type: {ex.InnerException.GetType().Name}");
        }
        throw;
    }
});

var contexts = new ConcurrentDictionary<string, ContextData>();
var payloadStore = new ConcurrentDictionary<string, string>();
const string EmptyPayload = "{\"experiments\":[]}";

string TranslateEndpoint(string endpoint)
{
    if (string.IsNullOrEmpty(endpoint)) return endpoint;
    endpoint = Regex.Replace(endpoint, @"localhost:\d+", "127.0.0.1:3000");
    endpoint = Regex.Replace(endpoint, @"[\w-]+-sdk:\d+", "127.0.0.1:3000");
    return endpoint;
}

IResult Failure(EventCollector collector, int eventsBefore, Exception ex) =>
    Results.BadRequest(new { error = ex.Message, events = collector.GetEventsSince(eventsBefore) });

app.MapGet("/health", () => Results.Ok(new
{
    status = "healthy",
    sdk = "dotnet",
    version = "1.0.0"
}));

app.MapGet("/capabilities", () => Results.Ok(new
{
    diagnostics = true,
    diagnosticsUtf8 = true,
    diagnosticsBase64 = true,
    diagnosticsTypeChecks = false,
    diagnosticsIsPromise = false,
    attrsSeq = true,
    publishFail = true,
    variableKeysMap = true,
    globalCustomFieldKeys = true,
    getUnits = true,
    getAttributes = true,
    readyError = true
}));

app.MapPost("/diagnostic", async (HttpContext httpContext) =>
{
    try
    {
        using var reader = new StreamReader(httpContext.Request.Body);
        var body = await reader.ReadToEndAsync();
        var requestJson = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(body);

        var op = requestJson != null && requestJson.TryGetValue("operation", out var opEl)
            ? opEl.GetString()
            : null;
        var value = requestJson != null && requestJson.TryGetValue("value", out var valEl)
            ? valEl
            : default;

        string text = value.ValueKind switch
        {
            JsonValueKind.String => value.GetString() ?? string.Empty,
            JsonValueKind.Undefined => string.Empty,
            JsonValueKind.Null => string.Empty,
            _ => value.ToString()
        };

        // Only helpers the SDK implements; the SDK has no isObject/isNumeric/isPromise.
        object? result = op switch
        {
            "hashUnit" => Md5.Hash(text),
            "base64UrlNoPadding" => Md5.Base64UrlNoPadding(Md5.Utf8Bytes(text)),
            "utf8Bytes" => Md5.Utf8Bytes(text).Select(b => (int)b).ToArray(),
            _ => null
        };

        if (result == null)
        {
            return Results.BadRequest(new { error = $"Unsupported diagnostic operation: {op}", events = Array.Empty<object>() });
        }

        return Results.Ok(new { result, events = Array.Empty<object>() });
    }
    catch (Exception ex)
    {
        return Results.BadRequest(new { error = ex.Message, events = Array.Empty<object>() });
    }
});

// The payload is stored and served verbatim; the SDK's own client fetches and
// deserializes it.
app.MapPut("/context_payload/{payloadId}", async (string payloadId, HttpContext httpContext) =>
{
    try
    {
        using var reader = new StreamReader(httpContext.Request.Body);
        var body = await reader.ReadToEndAsync();
        var requestJson = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(body);

        payloadStore[payloadId] = requestJson != null && requestJson.TryGetValue("data", out var dataEl)
            && dataEl.ValueKind != JsonValueKind.Null
            ? dataEl.GetRawText()
            : EmptyPayload;

        return Results.Ok(new { success = true });
    }
    catch (Exception ex)
    {
        return Results.BadRequest(new { error = ex.Message, events = Array.Empty<object>() });
    }
});

app.MapGet("/context_payload/{payloadId}", async (string payloadId, [FromQuery] int throttle = 0) =>
{
    if (throttle > 0) await Task.Delay(throttle);
    return Results.Content(payloadStore.GetValueOrDefault(payloadId, EmptyPayload), "application/json");
});

app.MapGet("/context_payload/{payloadId}/context", (string payloadId) =>
    Results.Content(payloadStore.GetValueOrDefault(payloadId, EmptyPayload), "application/json"));

app.MapPost("/context", async (HttpContext httpContext) =>
{
    var eventCollector = new EventCollector();
    try
    {
        using var reader = new StreamReader(httpContext.Request.Body);
        var body = await reader.ReadToEndAsync();
        var requestJson = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(body);

        if (requestJson == null)
        {
            return Results.BadRequest(new { error = "Invalid request format" });
        }

        if (requestJson.ContainsKey("mode") && requestJson["mode"].GetString() == "e2e")
        {
            var e2eEndpoint = Environment.GetEnvironmentVariable("ABSMARTLY_E2E_ENDPOINT");
            var e2eApiKey = Environment.GetEnvironmentVariable("ABSMARTLY_E2E_API_KEY");
            var e2eApp = Environment.GetEnvironmentVariable("ABSMARTLY_E2E_APPLICATION");
            var e2eEnv = Environment.GetEnvironmentVariable("ABSMARTLY_E2E_ENVIRONMENT");

            if (string.IsNullOrEmpty(e2eEndpoint) || string.IsNullOrEmpty(e2eApiKey) ||
                string.IsNullOrEmpty(e2eApp) || string.IsNullOrEmpty(e2eEnv))
            {
                return Results.Json(new { error = "e2e mode not configured" }, statusCode: 501);
            }

            var e2eContextId = $"ctx-{DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()}-{Guid.NewGuid():N}";
            var e2eEventCollector = eventCollector;

            var e2eServiceConfig = new ABSmartlyServiceConfiguration
            {
                Endpoint = e2eEndpoint,
                ApiKey = e2eApiKey,
                Application = e2eApp,
                Environment = e2eEnv
            };
            var e2eHttpClientFactory = new DummyHttpClientFactory();
            // The SDK's real publish transport, wrapped so /publishFail can arm a
            // one-shot failure on e2e contexts too.
            var e2ePublishFailFlag = new PublishFailFlag();
            var e2ePublisher = new CustomPublisher(e2eEventCollector, e2ePublishFailFlag,
                new ABSmartly.Services.DefaultContextPublisher(new ABSmartlyService(
                    e2eServiceConfig,
                    e2eHttpClientFactory,
                    new ABSmartly.Services.DefaultContextDataDeserializer(),
                    new ABSmartly.Services.DefaultContextEventSerializer())));

            var e2eSdk = new ABSdk(
                e2eHttpClientFactory,
                e2eServiceConfig,
                new ABSdkConfig
                {
                    ContextEventLogger = e2eEventCollector,
                    ContextPublisher = e2ePublisher
                }
            );

            var e2eContextConfig = new ContextConfig
            {
                PublishDelay = TimeSpan.FromMilliseconds(-1),
                RefreshInterval = TimeSpan.FromMilliseconds(0)
            };

            var e2ePassedUnits = new Dictionary<string, UnitValue>();
            if (requestJson.ContainsKey("units"))
            {
                foreach (var prop in requestJson["units"].EnumerateObject())
                {
                    var unit = CaptureUnitValue(prop.Value);
                    e2ePassedUnits[prop.Name] = unit;
                    e2eContextConfig.Units[prop.Name] = unit.Sent;
                }
            }

            // Wait for the SDK's own readiness, bounded below the orchestrator's 10s
            // request timeout. A context that is still loading or failed to load is
            // an error, not a usable context.
            const int e2eReadyTimeoutMs = 8000;
            var e2eContext = e2eSdk.CreatePendingContext(e2eContextConfig);
            var e2eReady = e2eContext.WaitUntilReadyAsync();
            if (await Task.WhenAny(e2eReady, Task.Delay(e2eReadyTimeoutMs)) != e2eReady)
            {
                if (e2eContext is IAsyncDisposable pending) await pending.DisposeAsync();
                return Results.Json(new { error = $"e2e context not ready after {e2eReadyTimeoutMs}ms" }, statusCode: 504);
            }
            if (e2eContext.IsFailed() || !e2eContext.IsReady())
            {
                var reason = e2eContext.ReadyError?.Message ?? "context data failed to load";
                return Results.Json(new { error = $"e2e context failed to load: {reason}" }, statusCode: 502);
            }

            if (requestJson.ContainsKey("attributes"))
            {
                foreach (var prop in requestJson["attributes"].EnumerateObject())
                {
                    object attrValue = prop.Value.ValueKind switch
                    {
                        JsonValueKind.String => prop.Value.GetString()!,
                        JsonValueKind.Number when prop.Value.TryGetInt64(out var iv) => (object)iv,
                        JsonValueKind.Number => prop.Value.GetDouble(),
                        JsonValueKind.True => true,
                        JsonValueKind.False => false,
                        _ => prop.Value.GetRawText()
                    };
                    e2eContext.SetAttribute(prop.Name, attrValue);
                }
            }

            contexts[e2eContextId] = new ContextData
            {
                Context = e2eContext,
                EventCollector = e2eEventCollector,
                Units = e2ePassedUnits,
                Attributes = new Dictionary<string, object>(),
                PublishFailFlag = e2ePublishFailFlag
            };

            return Results.Ok(new ApiResponse
            {
                Result = new
                {
                    ContextId = e2eContextId,
                    Ready = e2eContext.IsReady(),
                    Failed = e2eContext.IsFailed(),
                    Finalized = e2eContext.IsClosed()
                },
                Events = e2eEventCollector.GetEvents()
            });
        }

        ABSmartly.Models.ContextData? contextData = null;
        if (requestJson.ContainsKey("data"))
        {
            using var dataStream = new MemoryStream(Encoding.UTF8.GetBytes(requestJson["data"].GetRawText()));
            contextData = new ABSmartly.Services.DefaultContextDataDeserializer().Deserialize(dataStream)
                ?? throw new InvalidOperationException("context data could not be deserialized");
        }

        var endpoint = "http://localhost:3000";
        if (requestJson.ContainsKey("endpoint"))
        {
            endpoint = requestJson["endpoint"].GetString() ?? "http://localhost:3000";
            endpoint = TranslateEndpoint(endpoint);
        }

        int payloadThrottle = 0;
        if (requestJson.ContainsKey("options"))
        {
            var opts = requestJson["options"];
            if (opts.TryGetProperty("payloadThrottle", out var pt))
            {
                payloadThrottle = pt.GetInt32();
            }
        }

        bool failLoad = requestJson.ContainsKey("failLoad") &&
            requestJson["failLoad"].ValueKind == JsonValueKind.True;

        if (failLoad)
        {
            // Genuinely failing fetch: nothing listens on this port, so the SDK's
            // own HTTP data provider fails and the SDK decides how to surface it.
            endpoint = "http://127.0.0.1:9";
        }

        var contextId = $"ctx-{DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()}-{Guid.NewGuid():N}";
        var publishFailFlag = new PublishFailFlag();

        var sdk = new ABSdk(
            // payloadThrottle only delays the HTTP GET of context data (simulated
            // network latency); everything else is the SDK's own fetch path.
            new DummyHttpClientFactory(payloadThrottle),
            new ABSmartlyServiceConfiguration
            {
                Endpoint = endpoint,  // Use provided endpoint or dummy
                ApiKey = "dummy",
                Application = "test",
                Environment = "test"
            },
            new ABSdkConfig
            {
                ContextEventLogger = eventCollector,
                ContextPublisher = new CustomPublisher(eventCollector, publishFailFlag)
            }
        );

        var contextConfig = new ContextConfig
        {
            PublishDelay = TimeSpan.FromMilliseconds(-1),
            RefreshInterval = TimeSpan.FromMilliseconds(0)
        };

        if (requestJson.ContainsKey("options"))
        {
            var options = requestJson["options"];
            if (options.TryGetProperty("publishDelay", out var publishDelay))
            {
                contextConfig.PublishDelay = TimeSpan.FromMilliseconds(publishDelay.GetInt32());
            }
            if (options.TryGetProperty("refreshPeriod", out var refreshPeriod))
            {
                contextConfig.RefreshInterval = TimeSpan.FromMilliseconds(refreshPeriod.GetInt32());
            }
        }

        var passedUnits = new Dictionary<string, UnitValue>();
        if (requestJson.ContainsKey("units"))
        {
            foreach (var prop in requestJson["units"].EnumerateObject())
            {
                var unit = CaptureUnitValue(prop.Value);
                passedUnits[prop.Name] = unit;
                contextConfig.Units[prop.Name] = unit.Sent;
            }
        }

        IContext context;
        if (contextData != null)
        {
            context = sdk.CreateContextWith(contextConfig, contextData);  // Sync: createContextWith
        }
        else if (payloadThrottle > 0)
        {
            // SDK not-ready state: returns immediately; the SDK fetches the endpoint
            // in the background and readies (or fails) the context itself.
            context = sdk.CreatePendingContext(contextConfig);
        }
        else
        {
            // Returns once the SDK has loaded (ready event) or failed (error event).
            context = sdk.CreateContext(contextConfig);
        }

        var storedData = new ContextData
        {
            Context = context,
            EventCollector = eventCollector,
            Units = passedUnits,
            Attributes = new Dictionary<string, object>(),
            PublishFailFlag = publishFailFlag
        };

        contexts[contextId] = storedData;

        return Results.Ok(new ApiResponse
        {
            Result = new
            {
                ContextId = contextId,
                Ready = context.IsReady(),
                Failed = context.IsFailed(),
                Finalized = context.IsClosed()
            },
            Events = eventCollector.GetEvents()
        });
    }
    catch (Exception ex)
    {
        Console.WriteLine($"Error creating context: {ex.Message}");
        Console.WriteLine($"StackTrace: {ex.StackTrace}");
        if (ex.InnerException != null)
        {
            Console.WriteLine($"Inner Exception: {ex.InnerException.Message}");
        }
        return Results.BadRequest(new { error = ex.Message, events = eventCollector.GetEvents() });
    }
});

app.MapPost("/context/{contextId}/setUnit", (string contextId, [FromBody] SetUnitRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {

        if (request.Uid is not JsonElement uidElement)
            return Results.BadRequest(new { error = "uid is required" });

        var unit = CaptureUnitValue(uidElement);
        data.Context.SetUnit(request.UnitType, unit.Sent);
        lock (data.Units) data.Units[request.UnitType] = unit;
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = null,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapPost("/context/{contextId}/getUnit", (string contextId, [FromBody] GetUnitRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {
        // Read from the SDK (matching getUnits), not the wrapper-local shadow dict.
        object result = null;
        if (data.Context.Units.TryGetValue(request.UnitType, out var uid))
        {
            lock (data.Units) result = ResolveUnitValue(data.Units, request.UnitType, uid);
        }
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = result,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapPost("/context/{contextId}/attribute", (string contextId, [FromBody] AttributeRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {
        // Convert JsonElement to actual value
        object actualValue = request.Value;
        if (request.Value is JsonElement jsonElement)
        {
            actualValue = jsonElement.ValueKind switch
            {
                JsonValueKind.Number => jsonElement.TryGetInt64(out var l) ? l : jsonElement.GetDouble(),
                JsonValueKind.String => jsonElement.GetString(),
                JsonValueKind.True => true,
                JsonValueKind.False => false,
                JsonValueKind.Null => null,
                _ => request.Value
            };
        }
        data.Context.SetAttribute(request.Name, actualValue);
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = null,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapPost("/context/{contextId}/getAttribute", (string contextId, [FromBody] GetAttributeRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {
        // Read from the SDK (matching getAttributes), not the wrapper-local shadow dict.
        var result = data.Context.GetAttribute(request.Name);
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = result,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapPost("/context/{contextId}/treatment", async (string contextId, HttpContext httpContext) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {
        using var reader = new StreamReader(httpContext.Request.Body);
        var body = await reader.ReadToEndAsync();
        var request = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(body);

        var experimentName = request["experimentName"].GetString();


        var variant = data.Context.GetTreatment(experimentName);

        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = variant,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        Console.WriteLine($"Error in treatment: {ex.Message}");
        Console.WriteLine($"StackTrace: {ex.StackTrace}");
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapPost("/context/{contextId}/peek", (string contextId, [FromBody] TreatmentRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {
        var variant = data.Context.PeekTreatment(request.ExperimentName);
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = variant,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapPost("/context/{contextId}/variableValue", async (string contextId, HttpContext httpContext) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {
        using var reader = new StreamReader(httpContext.Request.Body);
        var body = await reader.ReadToEndAsync();
        var request = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(body);

        var key = request["key"].GetString();
        object defaultValue = request.TryGetValue("defaultValue", out var defVal)
            ? ConvertJsonElement(defVal)
            : null;


        var value = data.Context.GetVariableValue(key, defaultValue);

        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = value,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        Console.WriteLine($"Error in variableValue: {ex.Message}");
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapPost("/context/{contextId}/peekVariableValue", async (string contextId, HttpContext httpContext) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {
        using var reader = new StreamReader(httpContext.Request.Body);
        var body = await reader.ReadToEndAsync();
        var request = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(body);

        var key = request["key"].GetString();
        object defaultValue = request.TryGetValue("defaultValue", out var defVal)
            ? ConvertJsonElement(defVal)
            : null;


        var value = data.Context.PeekVariableValue(key, defaultValue);

        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = value,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        Console.WriteLine($"Error in peekVariableValue: {ex.Message}");
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapPost("/context/{contextId}/track", (string contextId, [FromBody] TrackRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {
        Dictionary<string, object> properties = null;

        if (request.Properties.HasValue)
        {
            var propsJson = request.Properties.Value;

            if (propsJson.ValueKind != JsonValueKind.Object && propsJson.ValueKind != JsonValueKind.Null)
            {
                return Results.BadRequest(new { error = $"Goal '{request.GoalName}' properties must be of type object.", events = data.EventCollector.GetEventsSince(eventsBefore) });
            }

            if (propsJson.ValueKind == JsonValueKind.Object)
            {
                properties = new Dictionary<string, object>();
                foreach (var prop in propsJson.EnumerateObject())
                {
                    properties[prop.Name] = ConvertJsonElement(prop.Value);
                }
            }
        }

        data.Context.Track(request.GoalName, properties);
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = null,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapPost("/context/{contextId}/override", (string contextId, [FromBody] OverrideRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {
        data.Context.SetOverride(request.ExperimentName, request.Variant);
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = null,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapPost("/context/{contextId}/customAssignment", (string contextId, [FromBody] CustomAssignmentRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {
        data.Context.SetCustomAssignment(request.ExperimentName, request.Variant);
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = null,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapPost("/context/{contextId}/customFieldValue", (string contextId, [FromBody] CustomFieldRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {
        var value = ((Context)data.Context).GetCustomFieldValue(request.ExperimentName, request.FieldName);
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = value,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapPost("/context/{contextId}/variableKeys", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {
        var result = data.Context.VariableKeys.Keys.ToList();
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = result,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapPost("/context/{contextId}/customFieldKeys", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {
        var keys = ((Context)data.Context).GetCustomFieldKeys();
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = keys,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapPost("/context/{contextId}/customFieldValueType", (string contextId, [FromBody] CustomFieldRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {
        var valueType = ((Context)data.Context).GetCustomFieldType(request.ExperimentName, request.FieldName);
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = valueType,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapPost("/context/{contextId}/setOverride", (string contextId, [FromBody] OverrideRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {
        data.Context.SetOverride(request.ExperimentName, request.Variant);
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = null,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapPost("/context/{contextId}/setCustomAssignment", (string contextId, [FromBody] CustomAssignmentRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {
        data.Context.SetCustomAssignment(request.ExperimentName, request.Variant);
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = null,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapGet("/context/{contextId}/pending", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    return Results.Ok(new ApiResponse
    {
        Result = data.Context.PendingCount,
        Events = data.EventCollector.GetEventsSince(eventsBefore)
    });
});

app.MapGet("/context/{contextId}/isFinalized", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    return Results.Ok(new ApiResponse
    {
        Result = data.Context.IsClosed(),
        Events = data.EventCollector.GetEventsSince(eventsBefore)
    });
});

app.MapGet("/context/{contextId}/isReady", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    return Results.Ok(new ApiResponse
    {
        Result = data.Context.IsReady(),
        Events = data.EventCollector.GetEventsSince(eventsBefore)
    });
});

app.MapGet("/context/{contextId}/isFailed", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    return Results.Ok(new ApiResponse
    {
        Result = data.Context.IsFailed(),
        Events = data.EventCollector.GetEventsSince(eventsBefore)
    });
});

app.MapGet("/context/{contextId}/experiments", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {
        var experiments = data.Context.Experiments;
        return Results.Ok(new ApiResponse
        {
            Result = experiments,
            Events = data.EventCollector.GetEventsSince(eventsBefore)
        });
    }
    catch (Exception ex)
    {
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapPost("/context/{contextId}/getUnits", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();
    try
    {
        var units = new Dictionary<string, object>();
        lock (data.Units)
        {
            foreach (var kvp in data.Context.Units)
                units[kvp.Key] = ResolveUnitValue(data.Units, kvp.Key, kvp.Value);
        }
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);
        return Results.Ok(new ApiResponse { Result = units, Events = newEvents });
    }
    catch (Exception e)
    {
        return Failure(data.EventCollector, eventsBefore, e);
    }
});

app.MapPost("/context/{contextId}/getAttributes", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();
    try
    {
        var attrs = data.Context.Attributes;
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);
        return Results.Ok(new ApiResponse { Result = attrs, Events = newEvents });
    }
    catch (Exception e)
    {
        return Failure(data.EventCollector, eventsBefore, e);
    }
});

app.MapPost("/context/{contextId}/readyError", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();
    try
    {
        var error = data.Context.ReadyError;
        var result = error != null ? new { isError = true, message = error.Message } : null;
        return Results.Ok(new ApiResponse { Result = result, Events = data.EventCollector.GetEventsSince(eventsBefore) });
    }
    catch (Exception e)
    {
        return Failure(data.EventCollector, eventsBefore, e);
    }
});

app.MapPost("/context/{contextId}/variableKeysMap", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();
    try
    {
        var keys = data.Context.VariableKeys;
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);
        return Results.Ok(new ApiResponse { Result = keys, Events = newEvents });
    }
    catch (Exception e)
    {
        return Failure(data.EventCollector, eventsBefore, e);
    }
});

app.MapPost("/context/{contextId}/globalCustomFieldKeys", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();
    try
    {
        var keys = ((Context)data.Context).GetCustomFieldKeys();
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);
        return Results.Ok(new ApiResponse { Result = keys, Events = newEvents });
    }
    catch (Exception e)
    {
        return Failure(data.EventCollector, eventsBefore, e);
    }
});

app.MapPost("/context/{contextId}/publishFail", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();
    if (data.PublishFailFlag != null)
        data.PublishFailFlag.Fail = true;
    return Results.Ok(new { result = (object?)null, events = data.EventCollector.GetEventsSince(eventsBefore) });
});

app.MapPost("/context/{contextId}/publish", async (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {
        await data.Context.PublishAsync();
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = null,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapPost("/context/{contextId}/refresh", async (string contextId, HttpContext httpContext) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {

        await data.Context.RefreshAsync();

        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = null,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        Console.WriteLine($"Error in refresh: {ex.Message}");
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapPost("/context/{contextId}/finalize", async (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    var eventsBefore = data.EventCollector.GetEventsCount();

    try
    {

        await data.Context.FinalizeAsync();

        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = null,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Failure(data.EventCollector, eventsBefore, ex);
    }
});

app.MapDelete("/context/{contextId}", (string contextId) =>
{
    contexts.TryRemove(contextId, out _);
    return Results.Ok(new { result = "deleted" });
});

app.Run();

// The SDK stores unit UIDs as strings; numbers become their invariant string form.
static string UnitUidString(JsonElement element)
{
    return element.ValueKind switch
    {
        JsonValueKind.String => element.GetString(),
        JsonValueKind.Null => null,
        JsonValueKind.Number when element.TryGetInt64(out var l) => l.ToString(System.Globalization.CultureInfo.InvariantCulture),
        JsonValueKind.Number when element.TryGetDouble(out var d) => d.ToString(System.Globalization.CultureInfo.InvariantCulture),
        _ => element.GetRawText()
    };
}

// The value the caller originally passed for a unit, kept so getUnit/getUnits can
// return it with its original JSON type (e.g. a number) instead of the SDK's string.
static UnitValue CaptureUnitValue(JsonElement element)
{
    object original = element.ValueKind switch
    {
        JsonValueKind.String => element.GetString(),
        JsonValueKind.Number when element.TryGetInt64(out var l) => l,
        JsonValueKind.Number => element.GetDouble(),
        _ => element.Clone()
    };
    return new UnitValue(original, UnitUidString(element));
}

// Returns the caller's original value only while the SDK still holds exactly the
// string the wrapper passed for it; otherwise the SDK's own string.
static object ResolveUnitValue(Dictionary<string, UnitValue> passed, string unitType, string sdkUid)
{
    if (passed.TryGetValue(unitType, out var unit) && unit.Sent == sdkUid)
        return unit.Original;
    return sdkUid;
}

static object ConvertJsonElement(JsonElement element)
{
    return element.ValueKind switch
    {
        JsonValueKind.Object => element.EnumerateObject()
            .ToDictionary(p => p.Name, p => ConvertJsonElement(p.Value)),
        JsonValueKind.Array => element.EnumerateArray()
            .Select(ConvertJsonElement).ToList(),
        JsonValueKind.String => element.GetString(),
        JsonValueKind.Number => element.TryGetInt64(out var l) ? l : element.GetDouble(),
        JsonValueKind.True => true,
        JsonValueKind.False => false,
        JsonValueKind.Null => null,
        _ => element.GetRawText()
    };
}

public class EventCollector : IContextEventLogger
{
    private readonly List<EventData> _events = new();
    private readonly object _lock = new();

    public void HandleEvent(IContext context, EventType eventType, object data)
    {
        lock (_lock)
        {
            _events.Add(new EventData
            {
                Type = EventTypeToString(eventType),
                Data = data,
                Timestamp = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
            });
        }
    }

    public int GetEventsCount()
    {
        lock (_lock)
        {
            return _events.Count;
        }
    }

    public List<object> GetEvents()
    {
        lock (_lock)
        {
            return _events.Select(e => new
            {
                type = e.Type,
                data = e.Data,
                timestamp = e.Timestamp
            }).Cast<object>().ToList();
        }
    }

    public List<object> GetEventsSince(int since)
    {
        lock (_lock)
        {
            return _events.Skip(since).Select(e => new
            {
                type = e.Type,
                data = e.Data,
                timestamp = e.Timestamp
            }).Cast<object>().ToList();
        }
    }

    private string EventTypeToString(EventType eventType)
    {
        return eventType switch
        {
            EventType.Error => "error",
            EventType.Ready => "ready",
            EventType.Refresh => "refresh",
            EventType.Publish => "publish",
            EventType.Exposure => "exposure",
            EventType.Goal => "goal",
            EventType.Close => "finalize",
            _ => eventType.ToString().ToLower()
        };
    }
}

public class CustomPublisher : IContextPublisher
{
    private readonly EventCollector _eventCollector;
    private readonly PublishFailFlag _publishFailFlag;
    private readonly IContextPublisher _delegate;

    public CustomPublisher(EventCollector eventCollector, PublishFailFlag publishFailFlag,
        IContextPublisher delegatePublisher = null)
    {
        _eventCollector = eventCollector;
        _publishFailFlag = publishFailFlag;
        _delegate = delegatePublisher;
    }

    public Task PublishAsync(IContext context, PublishEvent publishEvent)
    {
        // When armed via /publishFail, fail this publish so the SDK's failure path
        // runs (pending events are preserved). One-shot: the next publish succeeds.
        if (_publishFailFlag != null && _publishFailFlag.Fail)
        {
            _publishFailFlag.Fail = false;
            throw new Exception("publish failed");
        }
        return _delegate?.PublishAsync(context, publishEvent) ?? Task.CompletedTask;
    }
}

public class PublishFailFlag
{
    public volatile bool Fail;
}

public class DummyHttpClientFactory : IABsmartlyHttpClientFactory
{
    private readonly int _getDelayMs;

    public DummyHttpClientFactory(int getDelayMs = 0)
    {
        _getDelayMs = getDelayMs;
    }

    public IABsmartlyHttpClient CreateClient()
    {
        return new DummyHttpClient(_getDelayMs);
    }
}

public class DummyHttpClient : IABsmartlyHttpClient
{
    private readonly HttpClient _client = new HttpClient();
    private readonly int _getDelayMs;

    public DummyHttpClient(int getDelayMs = 0)
    {
        _getDelayMs = getDelayMs;
    }

    public async Task<HttpResponseMessage> GetAsync(string? requestUri)
    {
        // Simulated network latency for the context-data fetch (payloadThrottle).
        if (_getDelayMs > 0) await Task.Delay(_getDelayMs);
        return await _client.GetAsync(requestUri);
    }

    public Task<HttpResponseMessage> PutAsync(string? requestUri, HttpContent content)
    {
        return _client.PutAsync(requestUri, content);
    }

    public void AddHeader(string name, string value)
    {
        _client.DefaultRequestHeaders.Add(name, value);
    }

    public void Dispose()
    {
        _client?.Dispose();
    }
}

public class EventData
{
    public string Type { get; set; }
    public object Data { get; set; }
    public long Timestamp { get; set; }
}

public class ContextData
{
    public IContext Context { get; set; }
    public EventCollector EventCollector { get; set; }
    public Dictionary<string, UnitValue> Units { get; set; }
    public Dictionary<string, object> Attributes { get; set; }
    public PublishFailFlag PublishFailFlag { get; set; }
}

public record UnitValue(object Original, string Sent);

public class ApiResponse
{
    public object Result { get; set; }
    public List<object> Events { get; set; }
}

public class CreateContextRequest
{
    public ABSmartly.Models.ContextData Data { get; set; }
    public Dictionary<string, string> Units { get; set; }
    public ContextOptions Options { get; set; }
}

public class ContextOptions
{
    public int? PublishDelay { get; set; }
    public int? RefreshPeriod { get; set; }
}

public class SetUnitRequest
{
    public string UnitType { get; set; }
    public object Uid { get; set; }
}

public class GetUnitRequest
{
    public string UnitType { get; set; }
}

public class AttributeRequest
{
    public string Name { get; set; }
    public object Value { get; set; }
}

public class GetAttributeRequest
{
    public string Name { get; set; }
}

public class TreatmentRequest
{
    public string ExperimentName { get; set; }
}

public class VariableRequest
{
    public string Key { get; set; }
    public object DefaultValue { get; set; }
}

public class TrackRequest
{
    public string GoalName { get; set; }
    public JsonElement? Properties { get; set; }
}

public class OverrideRequest
{
    public string ExperimentName { get; set; }
    public int Variant { get; set; }
}

public class CustomAssignmentRequest
{
    public string ExperimentName { get; set; }
    public int Variant { get; set; }
}

public class CustomFieldRequest
{
    public string ExperimentName { get; set; }
    public string FieldName { get; set; }
}

public class RefreshRequest
{
    public ABSmartly.Models.ContextData NewData { get; set; }
}


