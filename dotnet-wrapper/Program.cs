using System.Collections.Concurrent;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
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
var payloadStore = new ConcurrentDictionary<string, ABSmartly.Models.ContextData>();

string TranslateEndpoint(string endpoint)
{
    if (string.IsNullOrEmpty(endpoint)) return endpoint;
    endpoint = Regex.Replace(endpoint, @"localhost:\d+", "127.0.0.1:3000");
    endpoint = Regex.Replace(endpoint, @"[\w-]+-sdk:\d+", "127.0.0.1:3000");
    return endpoint;
}

string TranslateErrorMessage(string msg)
{
    if (string.IsNullOrEmpty(msg)) return msg;
    if (msg.Contains("closed") || msg.Contains("closing"))
        return "Context finalized";
    if (msg.Contains("already set", StringComparison.OrdinalIgnoreCase))
    {
        var m = Regex.Match(msg, @"Unit ['""](?<unit>[^'""]+)['""]");
        if (m.Success)
            return $"Unit '{m.Groups["unit"].Value}' UID already set.";
    }
    return msg;
}

app.MapGet("/health", () => Results.Ok(new
{
    status = "healthy",
    sdk = "dotnet",
    version = "1.0.0"
}));

app.MapGet("/capabilities", () => Results.Ok(new
{
    diagnostics = true,
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

        object? result = op switch
        {
            "hashUnit" => ABSmartly.Internal.Hashing.Md5.Hash(text),
            "base64UrlNoPadding" => Convert.ToBase64String(Encoding.UTF8.GetBytes(text))
                .Replace('+', '-').Replace('/', '_').TrimEnd('='),
            "utf8Bytes" => Encoding.UTF8.GetBytes(text).Select(b => (int)b).ToArray(),
            "isObject" => value.ValueKind == JsonValueKind.Object,
            "isNumeric" => value.ValueKind == JsonValueKind.Number,
            "isPromise" => false,
            _ => null
        };

        if (result == null && op != "isPromise")
        {
            return Results.BadRequest(new { error = $"Unsupported diagnostic operation: {op}" });
        }

        return Results.Ok(new { result, events = Array.Empty<object>() });
    }
    catch (Exception ex)
    {
        return Results.Problem(ex.Message, statusCode: 500);
    }
});

app.MapPut("/context_payload/{payloadId}", async (string payloadId, HttpContext httpContext) =>
{
    try
    {
        using var reader = new StreamReader(httpContext.Request.Body);
        var body = await reader.ReadToEndAsync();
        var requestJson = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(body);

        ABSmartly.Models.ContextData? contextData = null;
        if (requestJson != null && requestJson.ContainsKey("data"))
        {
            var dataJson = requestJson["data"].GetRawText();
            contextData = JsonSerializer.Deserialize<ABSmartly.Models.ContextData>(dataJson, new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true
            });
        }

        payloadStore[payloadId] = contextData ?? new ABSmartly.Models.ContextData();

        return Results.Ok(new { success = true });
    }
    catch (Exception ex)
    {
        return Results.Problem(ex.Message, statusCode: 500);
    }
});

app.MapGet("/context_payload/{payloadId}", (string payloadId, [FromQuery] int throttle = 0) =>
{
    try
    {
        if (throttle > 0)
        {
            Thread.Sleep(throttle);
        }

        var data = payloadStore.GetValueOrDefault(payloadId, new ABSmartly.Models.ContextData
        {
            Experiments = Array.Empty<Experiment>()
        });

        return Results.Ok(data);
    }
    catch (Exception ex)
    {
        return Results.Problem(ex.Message, statusCode: 500);
    }
});

app.MapGet("/context_payload/{payloadId}/context", (string payloadId) =>
{
    var data = payloadStore.GetValueOrDefault(payloadId, new ABSmartly.Models.ContextData
    {
        Experiments = Array.Empty<Experiment>()
    });
    return Results.Ok(data);
});

app.MapPost("/context", async (HttpContext httpContext) =>
{
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
            var e2eEventCollector = new EventCollector();

            var e2eSdk = new ABSdk(
                new DummyHttpClientFactory(),
                new ABSmartlyServiceConfiguration
                {
                    Endpoint = e2eEndpoint,
                    ApiKey = e2eApiKey,
                    Application = e2eApp,
                    Environment = e2eEnv
                },
                new ABSdkConfig
                {
                    ContextEventLogger = e2eEventCollector
                }
            );

            var e2eContextConfig = new ContextConfig
            {
                PublishDelay = TimeSpan.FromMilliseconds(-1),
                RefreshInterval = TimeSpan.FromMilliseconds(0)
            };

            if (requestJson.ContainsKey("units"))
            {
                var units = requestJson["units"];
                foreach (var prop in units.EnumerateObject())
                {
                    if (prop.Value.ValueKind == JsonValueKind.Number)
                    {
                        if (prop.Value.TryGetInt64(out var intVal))
                            e2eContextConfig.Units[prop.Name] = intVal.ToString();
                        else if (prop.Value.TryGetDouble(out var doubleVal))
                            e2eContextConfig.Units[prop.Name] = doubleVal.ToString();
                        else
                            e2eContextConfig.Units[prop.Name] = prop.Value.GetRawText();
                    }
                    else
                    {
                        e2eContextConfig.Units[prop.Name] = prop.Value.GetString();
                    }
                }
            }

            var e2eContext = e2eSdk.CreateContext(e2eContextConfig);
            for (int i = 0; i < 100 && !e2eContext.IsReady(); i++)
            {
                await Task.Delay(10);
            }
            for (int i = 0; i < 50 && e2eEventCollector.GetEventsCount() == 0; i++)
            {
                await Task.Delay(10);
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
                Units = new Dictionary<string, object>(),
                Attributes = new Dictionary<string, object>()
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
            var dataJson = requestJson["data"].GetRawText();
            contextData = JsonSerializer.Deserialize<ABSmartly.Models.ContextData>(dataJson, new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true
            });
        }

        var endpoint = "http://localhost:3000";
        if (requestJson.ContainsKey("endpoint"))
        {
            endpoint = requestJson["endpoint"].GetString() ?? "http://localhost:3000";
            endpoint = TranslateEndpoint(endpoint);
        }

        var contextId = $"ctx-{DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()}-{Guid.NewGuid():N}";
        var eventCollector = new EventCollector();
        var publishFailFlag = new PublishFailFlag();

        var sdk = new ABSdk(
            new DummyHttpClientFactory(),
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

        if (requestJson.ContainsKey("units"))
        {
            var units = requestJson["units"];
            foreach (var prop in units.EnumerateObject())
            {
                // Convert numeric values to strings for the SDK
                if (prop.Value.ValueKind == JsonValueKind.Number)
                {
                    if (prop.Value.TryGetInt64(out var intVal))
                        contextConfig.Units[prop.Name] = intVal.ToString();
                    else if (prop.Value.TryGetDouble(out var doubleVal))
                        contextConfig.Units[prop.Name] = doubleVal.ToString();
                    else
                        contextConfig.Units[prop.Name] = prop.Value.GetRawText();
                }
                else
                {
                    contextConfig.Units[prop.Name] = prop.Value.GetString();
                }
            }
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

        IContext context;
        if (failLoad)
        {
            var failedContext = (Context)sdk.CreateContextWith(contextConfig, null);
            var setDataFailedMethod = typeof(Context).GetMethod("SetDataFailed", BindingFlags.NonPublic | BindingFlags.Instance);
            setDataFailedMethod.Invoke(failedContext, new object[] { new Exception("Context load failed") });
            context = failedContext;
        }
        else if (contextData != null && payloadThrottle > 0)
        {
            var capturedContextData = contextData;
            var capturedPayloadThrottle2 = payloadThrottle;
            var lazyContext2 = new LazyContext(Task.Run(async () =>
            {
                await Task.Delay(capturedPayloadThrottle2);
                return (IContext)sdk.CreateContextWith(contextConfig, capturedContextData);
            }), eventCollector);
            context = lazyContext2;
        }
        else if (contextData != null)
        {
            context = sdk.CreateContextWith(contextConfig, contextData);  // Sync: createContextWith
        }
        else if (payloadThrottle > 0)
        {
            var capturedPayloadThrottle = payloadThrottle;
            var lazyContext = new LazyContext(Task.Run(async () =>
            {
                await Task.Delay(capturedPayloadThrottle);
                var innerContext = sdk.CreateContext(contextConfig);
                for (int i = 0; i < 100 && !innerContext.IsReady(); i++)
                {
                    await Task.Delay(10);
                }
                return (IContext)innerContext;
            }), eventCollector);
            context = lazyContext;
        }
        else
        {
            context = sdk.CreateContext(contextConfig);  // Async: createContext (SDK will fetch from endpoint)

            // Wait until context is ready
            for (int i = 0; i < 100 && !context.IsReady(); i++)
            {
                await Task.Delay(10);
            }
            // Wait for events to be collected (like Go/Java wrappers)
            for (int i = 0; i < 50 && eventCollector.GetEventsCount() == 0; i++)
            {
                await Task.Delay(10);
            }
        }

        var storedData = new ContextData
        {
            Context = context,
            EventCollector = eventCollector,
            Units = new Dictionary<string, object>(),
            Attributes = new Dictionary<string, object>(),
            PublishFailFlag = publishFailFlag
        };

        if (requestJson.ContainsKey("units"))
        {
            var units = requestJson["units"];
            foreach (var prop in units.EnumerateObject())
            {
                if (prop.Value.ValueKind == JsonValueKind.Number)
                {
                    // Store the numeric value for getUnit
                    if (prop.Value.TryGetInt64(out var intVal))
                        storedData.Units[prop.Name] = intVal;
                    else if (prop.Value.TryGetDouble(out var doubleVal))
                        storedData.Units[prop.Name] = doubleVal;
                    else
                        storedData.Units[prop.Name] = prop.Value.GetRawText();
                }
                else
                {
                    storedData.Units[prop.Name] = prop.Value.GetString();
                }
            }
        }

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
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/setUnit", (string contextId, [FromBody] SetUnitRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        var eventsBefore = data.EventCollector.GetEventsCount();

        string uidString;
        object uidValue;

        if (request.Uid is JsonElement jsonElement)
        {
            if (jsonElement.ValueKind == JsonValueKind.Number)
            {
                uidValue = jsonElement.GetInt64();
                uidString = uidValue.ToString();
            }
            else if (jsonElement.ValueKind == JsonValueKind.String)
            {
                uidValue = jsonElement.GetString();
                uidString = (string)uidValue;
            }
            else
            {
                uidValue = jsonElement.ToString();
                uidString = (string)uidValue;
            }
        }
        else
        {
            uidValue = request.Uid;
            uidString = request.Uid?.ToString() ?? "";
        }

        data.Context.SetUnit(request.UnitType, uidString);
        data.Units[request.UnitType] = uidValue;
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = null,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/getUnit", (string contextId, [FromBody] GetUnitRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        var eventsBefore = data.EventCollector.GetEventsCount();
        // Read from the SDK (matching getUnits), not the wrapper-local shadow dict.
        object result = null;
        if (data.Context.Units.TryGetValue(request.UnitType, out var uid))
        {
            if (long.TryParse(uid, out var longVal))
                result = longVal;
            else if (double.TryParse(uid, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var dblVal))
                result = dblVal;
            else
                result = uid;
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
        if (TranslateErrorMessage(ex.Message) == "Context finalized")
        {
            return Results.Ok(new ApiResponse
            {
                Result = null,
                Events = new List<object>()
            });
        }
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/attribute", (string contextId, [FromBody] AttributeRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        var eventsBefore = data.EventCollector.GetEventsCount();
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
        Console.WriteLine($"DEBUG setAttribute: name={request.Name}, value={actualValue}, type={actualValue?.GetType().Name ?? "null"}");
        data.Context.SetAttribute(request.Name, actualValue);
        data.Attributes[request.Name] = actualValue;
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = null,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/getAttribute", (string contextId, [FromBody] GetAttributeRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        var eventsBefore = data.EventCollector.GetEventsCount();
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
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/treatment", async (string contextId, HttpContext httpContext) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        using var reader = new StreamReader(httpContext.Request.Body);
        var body = await reader.ReadToEndAsync();
        var request = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(body);

        var experimentName = request["experimentName"].GetString();

        var eventsBefore = data.EventCollector.GetEventsCount();

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
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/peek", (string contextId, [FromBody] TreatmentRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        var eventsBefore = data.EventCollector.GetEventsCount();
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
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/variableValue", async (string contextId, HttpContext httpContext) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        using var reader = new StreamReader(httpContext.Request.Body);
        var body = await reader.ReadToEndAsync();
        var request = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(body);

        var key = request["key"].GetString();
        object defaultValue = null;
        if (request.ContainsKey("defaultValue"))
        {
            var defVal = request["defaultValue"];
            if (defVal.ValueKind == JsonValueKind.Number)
            {
                defaultValue = defVal.GetInt32();
            }
            else if (defVal.ValueKind == JsonValueKind.String)
            {
                defaultValue = defVal.GetString();
            }
            else if (defVal.ValueKind == JsonValueKind.True || defVal.ValueKind == JsonValueKind.False)
            {
                defaultValue = defVal.GetBoolean();
            }
        }

        var eventsBefore = data.EventCollector.GetEventsCount();

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
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/peekVariableValue", async (string contextId, HttpContext httpContext) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        using var reader = new StreamReader(httpContext.Request.Body);
        var body = await reader.ReadToEndAsync();
        var request = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(body);

        var key = request["key"].GetString();
        object defaultValue = null;
        if (request.ContainsKey("defaultValue"))
        {
            var defVal = request["defaultValue"];
            if (defVal.ValueKind == JsonValueKind.Number)
            {
                defaultValue = defVal.GetInt32();
            }
            else if (defVal.ValueKind == JsonValueKind.String)
            {
                defaultValue = defVal.GetString();
            }
            else if (defVal.ValueKind == JsonValueKind.True || defVal.ValueKind == JsonValueKind.False)
            {
                defaultValue = defVal.GetBoolean();
            }
        }

        var eventsBefore = data.EventCollector.GetEventsCount();

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
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/track", (string contextId, [FromBody] TrackRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        Dictionary<string, object> properties = null;

        if (request.Properties.HasValue)
        {
            var propsJson = request.Properties.Value;

            if (propsJson.ValueKind != JsonValueKind.Object && propsJson.ValueKind != JsonValueKind.Null)
            {
                return Results.BadRequest(new { error = $"Goal '{request.GoalName}' properties must be of type object" });
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

        var eventsBefore = data.EventCollector.GetEventsCount();
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
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/override", (string contextId, [FromBody] OverrideRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        data.Context.SetOverride(request.ExperimentName, request.Variant);

        return Results.Ok(new ApiResponse
        {
            Result = null,
            Events = new List<object>()
        });
    }
    catch (Exception ex)
    {
        if (TranslateErrorMessage(ex.Message) == "Context finalized")
        {
            return Results.Ok(new ApiResponse
            {
                Result = null,
                Events = new List<object>()
            });
        }
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/customAssignment", (string contextId, [FromBody] CustomAssignmentRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        data.Context.SetCustomAssignment(request.ExperimentName, request.Variant);

        return Results.Ok(new ApiResponse
        {
            Result = null,
            Events = new List<object>()
        });
    }
    catch (Exception ex)
    {
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/customFieldValue", (string contextId, [FromBody] CustomFieldRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        var eventsBefore = data.EventCollector.GetEventsCount();
        var context = data.Context as Context;
        var value = context?.GetCustomFieldValue(request.ExperimentName, request.FieldName);
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = value,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/variableKeys", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        var eventsBefore = data.EventCollector.GetEventsCount();
        var context = data.Context as Context;
        var keys = context?.VariableKeys;
        var result = keys?.Keys.ToList() ?? new List<string>();
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = result,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/customFieldKeys", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        var eventsBefore = data.EventCollector.GetEventsCount();
        var context = data.Context as Context;
        var keys = context?.GetCustomFieldKeys();
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = keys ?? new List<string>(),
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/customFieldValueType", (string contextId, [FromBody] CustomFieldRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        var eventsBefore = data.EventCollector.GetEventsCount();
        var context = data.Context as Context;
        var valueType = context?.GetCustomFieldType(request.ExperimentName, request.FieldName);
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = valueType,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/setOverride", (string contextId, [FromBody] OverrideRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        var eventsBefore = data.EventCollector.GetEventsCount();
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
        if (TranslateErrorMessage(ex.Message) == "Context finalized")
        {
            return Results.Ok(new ApiResponse
            {
                Result = null,
                Events = new List<object>()
            });
        }
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/setCustomAssignment", (string contextId, [FromBody] CustomAssignmentRequest request) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        var eventsBefore = data.EventCollector.GetEventsCount();
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
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapGet("/context/{contextId}/pending", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    return Results.Ok(new ApiResponse
    {
        Result = data.Context.PendingCount,
        Events = new List<object>()
    });
});

app.MapGet("/context/{contextId}/isFinalized", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    return Results.Ok(new ApiResponse
    {
        Result = data.Context.IsClosed(),
        Events = new List<object>()
    });
});

app.MapGet("/context/{contextId}/isReady", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    return Results.Ok(new ApiResponse
    {
        Result = data.Context.IsReady(),
        Events = new List<object>()
    });
});

app.MapGet("/context/{contextId}/isFailed", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    return Results.Ok(new ApiResponse
    {
        Result = data.Context.IsFailed(),
        Events = new List<object>()
    });
});

app.MapGet("/context/{contextId}/experiments", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        var context = data.Context as Context;
        var experiments = context?.Experiments ?? Array.Empty<string>();
        return Results.Ok(new ApiResponse
        {
            Result = experiments,
            Events = new List<object>()
        });
    }
    catch (Exception ex)
    {
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/getUnits", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    try
    {
        var eventsBefore = data.EventCollector.GetEventsCount();
        var rawUnits = data.Context.Units;
        var units = new Dictionary<string, object>();
        foreach (var kvp in rawUnits)
        {
            if (long.TryParse(kvp.Value, out var longVal))
                units[kvp.Key] = longVal;
            else if (double.TryParse(kvp.Value, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var dblVal))
                units[kvp.Key] = dblVal;
            else
                units[kvp.Key] = kvp.Value;
        }
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);
        return Results.Ok(new ApiResponse { Result = units, Events = newEvents });
    }
    catch (Exception e)
    {
        return Results.BadRequest(new { error = e.Message });
    }
});

app.MapPost("/context/{contextId}/getAttributes", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    try
    {
        var eventsBefore = data.EventCollector.GetEventsCount();
        var attrs = data.Context.Attributes;
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);
        return Results.Ok(new ApiResponse { Result = attrs, Events = newEvents });
    }
    catch (Exception e)
    {
        return Results.BadRequest(new { error = e.Message });
    }
});

app.MapPost("/context/{contextId}/readyError", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    try
    {
        var error = data.Context.ReadyError;
        var result = error != null ? new { isError = true, message = error.Message } : null;
        return Results.Ok(new ApiResponse { Result = result, Events = new List<object>() });
    }
    catch (Exception e)
    {
        return Results.BadRequest(new { error = e.Message });
    }
});

app.MapPost("/context/{contextId}/variableKeysMap", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    try
    {
        var eventsBefore = data.EventCollector.GetEventsCount();
        var keys = data.Context.VariableKeys;
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);
        return Results.Ok(new ApiResponse { Result = keys, Events = newEvents });
    }
    catch (Exception e)
    {
        return Results.BadRequest(new { error = e.Message });
    }
});

app.MapPost("/context/{contextId}/globalCustomFieldKeys", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    try
    {
        var context = data.Context as Context;
        var eventsBefore = data.EventCollector.GetEventsCount();
        var keys = (object)context?.GetCustomFieldKeys() ?? new List<string>();
        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);
        return Results.Ok(new ApiResponse { Result = keys, Events = newEvents });
    }
    catch (Exception e)
    {
        return Results.BadRequest(new { error = e.Message });
    }
});

app.MapPost("/context/{contextId}/publishFail", (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });
    if (data.PublishFailFlag != null)
        data.PublishFailFlag.Fail = true;
    return Results.Ok(new { result = (object?)null, events = Array.Empty<object>() });
});

app.MapPost("/context/{contextId}/publish", async (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        var eventsBefore = data.EventCollector.GetEventsCount();
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
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/refresh", async (string contextId, HttpContext httpContext) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        var eventsBefore = data.EventCollector.GetEventsCount();

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
        return Results.BadRequest(new { error = TranslateErrorMessage(ex.Message) });
    }
});

app.MapPost("/context/{contextId}/finalize", async (string contextId) =>
{
    if (!contexts.TryGetValue(contextId, out var data))
        return Results.NotFound(new { error = "Context not found" });

    try
    {
        var eventsBefore = data.EventCollector.GetEventsCount();

        var context = data.Context as Context;
        if (context != null)
        {
            var closeMethod = typeof(Context).GetMethod("CloseAsync", BindingFlags.NonPublic | BindingFlags.Instance);
            if (closeMethod == null)
            {
                return Results.Problem("Context.CloseAsync method not found via reflection; cannot finalize", statusCode: 500);
            }
            await (Task)closeMethod.Invoke(context, null);
        }

        var newEvents = data.EventCollector.GetEventsSince(eventsBefore);

        return Results.Ok(new ApiResponse
        {
            Result = null,
            Events = newEvents
        });
    }
    catch (Exception ex)
    {
        return Results.Problem(ex.Message);
    }
});

app.MapDelete("/context/{contextId}", (string contextId) =>
{
    contexts.TryRemove(contextId, out _);
    return Results.Ok(new { result = "deleted" });
});

app.Run();

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

    public CustomPublisher(EventCollector eventCollector, PublishFailFlag publishFailFlag)
    {
        _eventCollector = eventCollector;
        _publishFailFlag = publishFailFlag;
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
        return Task.CompletedTask;
    }
}

public class PublishFailFlag
{
    public volatile bool Fail;
}

public class DummyHttpClientFactory : IABSdkHttpClientFactory
{
    // Must return base interface type for C# compatibility
    IABsmartlyHttpClient IABsmartlyHttpClientFactory.CreateClient()
    {
        return new DummyHttpClient();
    }

    public IABSdkHttpClient CreateClient()
    {
        return new DummyHttpClient();
    }
}

public class DummyHttpClient : IABSdkHttpClient
{
    private readonly HttpClient _client = new HttpClient();

    public Task<HttpResponseMessage> GetAsync(string requestUri)
    {
        return _client.GetAsync(requestUri);
    }

    public Task<HttpResponseMessage> PutAsync(string requestUri, HttpContent content)
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
    public Dictionary<string, object> Units { get; set; }
    public Dictionary<string, object> Attributes { get; set; }
    public PublishFailFlag PublishFailFlag { get; set; }
}

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

public class LazyContext : IContext
{
    private volatile IContext _inner;
    private readonly Task<IContext> _creationTask;
    private readonly EventCollector _eventCollector;
    private readonly List<(string goalName, Dictionary<string, object> properties)> _queuedGoals = new();
    private readonly List<(string name, object value)> _queuedAttributes = new();
    private readonly object _lock = new();

    public LazyContext(Task<IContext> creationTask, EventCollector eventCollector)
    {
        _creationTask = creationTask;
        _eventCollector = eventCollector;
        _creationTask.ContinueWith(t =>
        {
            if (t.IsCompletedSuccessfully)
            {
                lock (_lock)
                {
                    _inner = t.Result;
                    foreach (var (goalName, properties) in _queuedGoals)
                        _inner.Track(goalName, properties);
                    _queuedGoals.Clear();
                    foreach (var (name, value) in _queuedAttributes)
                        _inner.SetAttribute(name, value);
                    _queuedAttributes.Clear();
                }
            }
        });
    }

    public void WaitForReady(int timeoutMs = 5000)
    {
        _creationTask.Wait(timeoutMs);
    }

    public int PendingCount
    {
        get
        {
            lock (_lock)
            {
                if (_inner != null) return _inner.PendingCount;
                return _queuedGoals.Count;
            }
        }
    }

    public bool IsReady() => _inner?.IsReady() ?? false;
    public bool IsFailed() => _inner?.IsFailed() ?? false;
    public bool IsClosed() => _inner?.IsClosed() ?? false;
    public bool IsClosing() => _inner?.IsClosing() ?? false;
    public bool IsFinalized => _inner?.IsFinalized ?? false;
    public bool IsFinalizing => _inner?.IsFinalizing ?? false;
    public Exception ReadyError => _inner?.ReadyError;
    public Dictionary<string, string> Units => _inner?.Units ?? new Dictionary<string, string>();
    [Obsolete("Use the Units property instead.")]
    public Dictionary<string, string> GetUnits() => Units;
    public object GetAttribute(string name) => _inner?.GetAttribute(name);
    public Dictionary<string, object> Attributes => _inner?.Attributes ?? new Dictionary<string, object>();
    [Obsolete("Use the Attributes property instead.")]
    public Dictionary<string, object> GetAttributes() => Attributes;
    public void Close() => _inner?.Close();

    public string[] Experiments => _inner?.Experiments ?? Array.Empty<string>();
    [Obsolete("Use the Experiments property instead.")]
    public string[] GetExperiments() => Experiments;
    public ABSmartly.Models.ContextData GetContextData() => _inner?.GetContextData();

    public void SetAttribute(string name, object value)
    {
        lock (_lock)
        {
            if (_inner != null) _inner.SetAttribute(name, value);
            else _queuedAttributes.Add((name, value));
        }
    }

    public void SetAttributes(Dictionary<string, object> attributes)
    {
        lock (_lock)
        {
            if (_inner != null) _inner.SetAttributes(attributes);
            else foreach (var kvp in attributes) _queuedAttributes.Add((kvp.Key, kvp.Value));
        }
    }

    public void SetCustomAssignment(string experimentName, int variant) => _inner?.SetCustomAssignment(experimentName, variant);
    public int? GetCustomAssignment(string experimentName) => _inner?.GetCustomAssignment(experimentName);
    public void SetCustomAssignments(Dictionary<string, int> customAssignments) => _inner?.SetCustomAssignments(customAssignments);
    public void SetOverride(string experimentName, int variant) => _inner?.SetOverride(experimentName, variant);
    public int? GetOverride(string experimentName) => _inner?.GetOverride(experimentName);
    public void SetOverrides(Dictionary<string, int> overrides) => _inner?.SetOverrides(overrides);
    public int GetTreatment(string experimentName) => _inner?.GetTreatment(experimentName) ?? 0;
    public int PeekTreatment(string experimentName) => _inner?.PeekTreatment(experimentName) ?? 0;
    public void SetUnit(string unitType, string uid) => _inner?.SetUnit(unitType, uid);
    public void SetUnits(Dictionary<string, string> units) => _inner?.SetUnits(units);
    public Dictionary<string, List<string>> VariableKeys => _inner?.VariableKeys ?? new Dictionary<string, List<string>>();
    public Dictionary<string, string> GetVariableKeys() => _inner?.GetVariableKeys() ?? new Dictionary<string, string>();
    public Dictionary<string, List<string>> GetVariableExperimentKeys() => _inner?.GetVariableExperimentKeys() ?? new Dictionary<string, List<string>>();
    public object GetVariableValue(string key, object defaultValue) => _inner?.GetVariableValue(key, defaultValue) ?? defaultValue;
    public object PeekVariableValue(string key, object defaultValue) => _inner?.PeekVariableValue(key, defaultValue) ?? defaultValue;
    public void Publish() => _inner?.Publish();
    public Task PublishAsync() => _inner?.PublishAsync() ?? Task.CompletedTask;
    public void Refresh() => _inner?.Refresh();
    public Task RefreshAsync() => _inner?.RefreshAsync() ?? Task.CompletedTask;

    public void Track(string goalName, Dictionary<string, object> properties)
    {
        lock (_lock)
        {
            if (_inner != null)
            {
                _inner.Track(goalName, properties);
            }
            else
            {
                _queuedGoals.Add((goalName, properties));
                _eventCollector.HandleEvent(this, EventType.Goal, new { name = goalName, properties });
            }
        }
    }
}

