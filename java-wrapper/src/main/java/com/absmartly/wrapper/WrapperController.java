package com.absmartly.wrapper;

import com.absmartly.sdk.ABsmartlyClient;
import com.absmartly.sdk.Context;
import com.absmartly.sdk.ContextData;
import com.absmartly.sdk.ContextOptions;
import com.absmartly.sdk.ContextState;
import com.absmartly.sdk.WrapperNoEnvironment;
import com.absmartly.sdk.internal.Buffers;
import com.absmartly.sdk.internal.hashing.Hashing;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.*;
import java.util.function.Supplier;

@RestController
public class WrapperController {
    private final Map<String, ContextWrapper> contexts = new ConcurrentHashMap<>();
    // Stored as JSON text, exactly as the scenario sent it (key order included), and served back verbatim.
    private final Map<String, String> payloadStore = new ConcurrentHashMap<>();
    private final ObjectMapper objectMapper = new ObjectMapper();
    private final Map<String, List<Map<String, Object>>> capturedRequests = new ConcurrentHashMap<>();

    private void captureRequest(String payloadId, String method, Map<String, String> headers) {
        Map<String, Object> entry = new HashMap<>();
        entry.put("method", method);
        entry.put("headers", new LinkedHashMap<>(headers));
        capturedRequests.computeIfAbsent(payloadId, k -> Collections.synchronizedList(new ArrayList<>())).add(entry);
    }

    private final SwitchbackFunctions switchback;

    public WrapperController() {
        this(SwitchbackFunctions.LINKED);
    }

    WrapperController(SwitchbackFunctions switchback) {
        this.switchback = switchback;
    }

    private String translateEndpoint(String endpoint) {
        if (endpoint == null) return null;
        try {
            java.net.URI uri = new java.net.URI(endpoint);
            String host = uri.getHost();
            if ("localhost".equals(host) || "127.0.0.1".equals(host)) {
                return new java.net.URI(uri.getScheme(), uri.getUserInfo(), "localhost", 3000,
                    uri.getPath(), uri.getQuery(), uri.getFragment()).toString();
            }
        } catch (Exception e) {
        }
        return endpoint;
    }

    /**
     * Unwraps async wrapper exceptions (CompletionException, ExecutionException)
     * so the reported message is
     * the SDK/publisher's own error, not "java.lang.RuntimeException: ...".
     */
    private static Throwable rootCause(Throwable error) {
        Throwable current = error;
        while ((current instanceof java.util.concurrent.CompletionException
                || current instanceof java.util.concurrent.ExecutionException)
            && current.getCause() != null) {
            current = current.getCause();
        }
        return current;
    }

    private String errorMessage(Throwable error) {
        Throwable root = rootCause(error);
        String message = root.getMessage();
        return message != null ? message : root.toString();
    }

    private static Map<String, Object> errorBody(String message, List<Map<String, Object>> events) {
        Map<String, Object> body = new HashMap<>();
        body.put("error", message);
        body.put("events", events);
        return body;
    }

    @GetMapping("/health")
    public Map<String, Object> health() {
        Map<String, Object> response = new HashMap<>();
        response.put("status", "healthy");
        response.put("sdk", "java");
        response.put("version", "1.0.0");
        return response;
    }

    @GetMapping("/capabilities")
    public Map<String, Object> capabilities() {
        Map<String, Object> response = new HashMap<>();
        response.put("diagnostics", true);
        response.put("diagnosticsUtf8", true);
        response.put("attrsSeq", false);
        response.put("publishFail", true);
        response.put("variableKeysMap", true);
        response.put("globalCustomFieldKeys", true);
        response.put("getUnits", true);
        response.put("getAttributes", true);
        response.put("readyError", true);
        response.put("numericUnitIds", true);
        response.put("publishThrowsOnFailure", true);
        response.put("trackRejectsNonObjectProperties", true);
        response.put("refreshNoOpOnFailedContext", true);
        response.put("throwsOnMisuse", true);
        response.put("singleFinalizeEvent", true);
        response.put("semverNumericOperands", true);
        response.put("clientNoEnvironment", true);
        response.put("clientRejectsBlankApplication", true);
        response.put("clientAcceptsBlankApplication", false);
        response.put("holdouts", HoldoutSelfTest.run());
        response.put("holdout_arms", HoldoutArmsSelfTest.run());
        response.put("switchback", SwitchbackSelfTest.run());
        response.put("assignmentRules", AssignmentRulesSelfTest.runAssignmentRules());
        response.put("semverOperators", AssignmentRulesSelfTest.runSemverOperators());
        response.put("xAgentHeader", true);
        return response;
    }

    @PostMapping("/diagnostic")
    public ResponseEntity<?> diagnostic(@RequestBody Map<String, Object> request) {
        try {
            String operation = (String) request.get("operation");
            Object value = request.get("value");
            Object result;
            switch (operation) {
                case "hashUnit":
                    result = new String(Hashing.hashUnit(value), java.nio.charset.StandardCharsets.US_ASCII);
                    break;
                case "utf8Bytes": {
                    String text = (String) value;
                    byte[] buffer = new byte[text.length() * 4];
                    int length = Buffers.encodeUTF8(buffer, 0, text);
                    List<Integer> out = new ArrayList<>(length);
                    for (int i = 0; i < length; i++) {
                        out.add(buffer[i] & 0xff);
                    }
                    result = out;
                    break;
                }
                default:
                    return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                        .body(errorBody("Unsupported diagnostic operation: " + operation, Collections.emptyList()));
            }

            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", Collections.emptyList());
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(errorBody(errorMessage(e), Collections.emptyList()));
        }
    }

    // clusterUnit is the assigner input (the raw R33 cluster key) and is not hashed; 64-bit
    // values arrive as decimal strings. See WRAPPER_API_SPEC.md, capability `switchback`.
    @PostMapping("/switchback/assign")
    public ResponseEntity<?> switchbackAssign(@RequestBody Map<String, Object> request) {
        return switchbackCall(() -> switchback.assign(stringParam(request, "clusterUnit"),
            longParam(request, "periodIndex"), longParam(request, "seed"), armCountParam(request)));
    }

    @PostMapping("/switchback/permutation")
    public ResponseEntity<?> switchbackPermutation(@RequestBody Map<String, Object> request) {
        return switchbackCall(() -> {
            int[] permutation = switchback.permutation(stringParam(request, "clusterUnit"),
                longParam(request, "cycleIndex"), longParam(request, "seed"), armCountParam(request));
            List<Integer> result = new ArrayList<>(permutation.length);
            for (int arm : permutation) {
                result.add(arm);
            }
            return result;
        });
    }

    // Answers whenever the assigner links, independent of the self-test verdict, so a regressed
    // assigner reports its actual outputs rather than a blanket refusal.
    private ResponseEntity<?> switchbackCall(Supplier<Object> call) {
        if (!switchback.isAvailable()) {
            return ResponseEntity.status(HttpStatus.NOT_IMPLEMENTED)
                .body(Collections.singletonMap("error", "linked SDK has no switchback assigner"));
        }
        try {
            Map<String, Object> response = new HashMap<>();
            response.put("result", call.get());
            response.put("events", Collections.emptyList());
            return ResponseEntity.ok(response);
        } catch (IllegalArgumentException e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(Collections.singletonMap("error", String.valueOf(e.getMessage())));
        }
    }

    private static String stringParam(Map<String, Object> request, String name) {
        if (!(request.get(name) instanceof String value)) {
            throw new IllegalArgumentException(name + " must be a string");
        }
        return value;
    }

    private static long longParam(Map<String, Object> request, String name) {
        return Long.parseLong(stringParam(request, name));
    }

    // The 400-on-armCount<2 contract is the wrapper's, so it must not depend on how the linked
    // assigner reacts (a regressed one could return a value or throw something unexpected).
    private static int armCountParam(Map<String, Object> request) {
        int armCount = intParam(request, "armCount");
        if (armCount < 2) {
            throw new IllegalArgumentException("armCount must be at least 2, got " + armCount);
        }
        return armCount;
    }

    private static int intParam(Map<String, Object> request, String name) {
        if (!(request.get(name) instanceof Integer value)) {
            throw new IllegalArgumentException(name + " must be an integer");
        }
        return value;
    }

    @PutMapping("/context_payload/{payloadId}")
    public ResponseEntity<?> storePayload(@PathVariable String payloadId, @RequestBody Map<String, Object> request) {
        try {
            Object data = request.get("data");
            // Validate with the SDK's own decoder, as the 1.x wrapper did by converting to its model.
            String json = objectMapper.writeValueAsString(data != null ? data : Collections.emptyMap());
            ContextData.fromJson(json);
            payloadStore.put(payloadId, json);

            Map<String, Object> response = new HashMap<>();
            response.put("success", true);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(errorBody(errorMessage(e), Collections.emptyList()));
        }
    }

    @GetMapping("/context_payload/{payloadId}")
    public ResponseEntity<?> getPayload(
        @PathVariable String payloadId,
        @RequestParam(defaultValue = "0") int throttle
    ) {
        if (throttle > 0) {
            try {
                Thread.sleep(throttle);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }

        String data = payloadStore.getOrDefault(payloadId, WrapperTransport.EMPTY_CONTEXT);

        return ResponseEntity.ok().contentType(org.springframework.http.MediaType.APPLICATION_JSON).body(data);
    }

    @GetMapping("/context_payload/{payloadId}/context")
    public ResponseEntity<?> mockApiContext(@PathVariable String payloadId, @RequestHeader Map<String, String> headers) {
        captureRequest(payloadId, "GET", headers);
        // A never-stored payload id is a missing collector payload: answer 404
        // so the SDK's own fetch fails the context, instead of serving an
        // empty-but-valid payload that would make it silently ready.
        String data = payloadStore.get(payloadId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Payload not found"));
        }
        return ResponseEntity.ok().contentType(org.springframework.http.MediaType.APPLICATION_JSON).body(data);
    }

    // Mock collector - with publishToEndpoint the SDK publishes PUT {endpoint}/context here.
    @PutMapping("/context_payload/{payloadId}/context")
    public Map<String, Object> mockApiPublish(@PathVariable String payloadId, @RequestHeader Map<String, String> headers) {
        captureRequest(payloadId, "PUT", headers);
        return Collections.emptyMap();
    }

    @GetMapping("/context_payload/{payloadId}/requests")
    public Map<String, Object> capturedRequests(@PathVariable String payloadId) {
        List<Map<String, Object>> requests = capturedRequests.get(payloadId);
        List<Map<String, Object>> snapshot = new ArrayList<>();
        if (requests != null) {
            synchronized (requests) {
                snapshot.addAll(requests);
            }
        }
        return Collections.singletonMap("requests", snapshot);
    }

    @PostMapping("/context")
    public ResponseEntity<?> createContext(@RequestBody Map<String, Object> request) {
        EventCollector createCollector = null;
        try {
            String mode = (String) request.get("mode");
            if ("e2e".equals(mode)) {
                String e2eEndpoint = System.getenv("ABSMARTLY_E2E_ENDPOINT");
                String e2eApiKey = System.getenv("ABSMARTLY_E2E_API_KEY");
                String e2eApplication = System.getenv("ABSMARTLY_E2E_APPLICATION");
                String e2eEnvironment = System.getenv("ABSMARTLY_E2E_ENVIRONMENT");
                if (e2eEndpoint == null || e2eApiKey == null || e2eApplication == null || e2eEnvironment == null) {
                    return ResponseEntity.status(501).body(Collections.singletonMap("error", "e2e mode not configured"));
                }

                @SuppressWarnings("unchecked")
                Map<String, Object> e2eUnits = (Map<String, Object>) request.get("units");
                @SuppressWarnings("unchecked")
                Map<String, Object> e2eAttrs = (Map<String, Object>) request.get("attributes");

                EventCollector e2eCollector = new EventCollector();
                createCollector = e2eCollector;
                // Never auto-publish or auto-refresh in e2e mode; the test drives
                // publishing explicitly (SDK default publishDelay is 100ms).
                ContextOptions.Builder e2eOptions = ContextOptions.builder()
                    .publishDelay(java.time.Duration.ofMillis(-1))
                    .eventListener(e2eCollector);
                if (e2eUnits != null) {
                    e2eOptions.units(e2eUnits);
                }

                ABsmartlyClient e2eClient = ABsmartlyClient.builder()
                    .endpoint(java.net.URI.create(e2eEndpoint))
                    .apiKey(e2eApiKey)
                    .application(e2eApplication)
                    .environment(e2eEnvironment)
                    .build();
                Context e2eContext = e2eClient.newContext(e2eOptions.build());

                if (e2eAttrs != null) {
                    for (Map.Entry<String, Object> entry : e2eAttrs.entrySet()) {
                        e2eContext.setAttribute(entry.getKey(), entry.getValue());
                    }
                }

                awaitLoad(e2eContext);

                String e2eContextId = "ctx-" + System.currentTimeMillis() + "-" + Math.random();
                contexts.put(e2eContextId, new ContextWrapper(e2eContext, e2eCollector, null, e2eClient, false));

                Map<String, Object> e2eResponse = new HashMap<>();
                e2eResponse.put("result", status(e2eContext, e2eContextId));
                e2eResponse.put("events", e2eCollector.getEvents());
                return ResponseEntity.ok(e2eResponse);
            }

            String contextJson = null;
            if (request.containsKey("data")) {
                contextJson = objectMapper.writeValueAsString(request.get("data"));
            }

            String endpoint = (String) request.get("endpoint");

            @SuppressWarnings("unchecked")
            Map<String, Object> units = (Map<String, Object>) request.get("units");

            @SuppressWarnings("unchecked")
            Map<String, Object> options = (Map<String, Object>) request.getOrDefault("options", new HashMap<>());

            // Optional client config (the 2026-11 system-attributes round): a scenario
            // can set the application/environment names so audiences can target them.
            // When the `client` field is omitted, the fixed test-app/test-env defaults
            // keep every pre-2026-11 scenario unchanged. An explicitly supplied
            // application is forwarded as-is so the SDK validates it; only an omitted
            // one falls back to test-app. A blank/absent environment means
            // "no environment configured" (see WrapperNoEnvironment).
            @SuppressWarnings("unchecked")
            Map<String, Object> clientConfigRequest = (Map<String, Object>) request.get("client");
            String clientApplication = "test-app";
            String clientEnvironment = "test-env";
            if (clientConfigRequest != null) {
                if (clientConfigRequest.containsKey("application")) {
                    Object app = clientConfigRequest.get("application");
                    if (!(app instanceof String)) {
                        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                            .body(errorBody("Invalid 'application' in client argument", Collections.emptyList()));
                    }
                    clientApplication = (String) app;
                }
                Object env = clientConfigRequest.get("environment");
                clientEnvironment = (env instanceof String && !((String) env).isEmpty()) ? (String) env : null;
            }

            EventCollector eventCollector = new EventCollector();
            createCollector = eventCollector;

            Integer publishDelay = (Integer) options.getOrDefault("publishDelay", -1);
            Integer refreshPeriod = (Integer) options.getOrDefault("refreshPeriod", 0);

            ContextOptions.Builder contextOptions = ContextOptions.builder()
                .publishDelay(java.time.Duration.ofMillis(publishDelay))
                .refreshInterval(java.time.Duration.ofMillis(refreshPeriod))
                .eventListener(eventCollector);
            if (units != null) {
                contextOptions.units(units);
            }

            // publishToEndpoint: publish through the SDK's own client to {endpoint}/context.
            boolean publishToEndpoint = Boolean.TRUE.equals(request.get("publishToEndpoint")) && endpoint != null;
            boolean failLoad = contextJson == null && endpoint == null && Boolean.TRUE.equals(request.get("failLoad"));
            Integer payloadThrottle = (Integer) options.getOrDefault("payloadThrottle", 0);
            boolean fetchOverHttp = contextJson == null && endpoint != null;
            WrapperTransport transport = new WrapperTransport(fetchOverHttp, publishToEndpoint || fetchOverHttp,
                failLoad, fetchOverHttp ? payloadThrottle : 0);
            String clientEndpoint = endpoint != null ? translateEndpoint(endpoint) : "http://localhost";

            ABsmartlyClient client;
            try {
                client = ABsmartlyClient.builder()
                    .endpoint(java.net.URI.create(clientEndpoint))
                    .apiKey("test-api-key")
                    .application(clientApplication)
                    .environment(clientEnvironment != null ? clientEnvironment : "placeholder")
                    .transport(transport)
                    .build();
            } catch (IllegalArgumentException e) {
                transport.close();
                return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                    .body(errorBody(e.getMessage(), Collections.emptyList()));
            }
            // The client does not close a transport it was given, so the wrapper closes both together.
            AutoCloseable owner = () -> {
                client.close();
                transport.close();
            };

            // No environment configured: the SDK's own client and context, with the environment reported absent.
            java.util.concurrent.ScheduledExecutorService noEnvScheduler = null;
            if (clientEnvironment == null) {
                noEnvScheduler = java.util.concurrent.Executors.newSingleThreadScheduledExecutor();
                java.util.concurrent.ScheduledExecutorService scheduler = noEnvScheduler;
                AutoCloseable inner = owner;
                owner = () -> {
                    inner.close();
                    scheduler.shutdownNow();
                };
            }

            Context context;
            if (contextJson != null) {
                // Sync: inline data, ready at once; refresh(newData) then serves from memory.
                ContextData data = ContextData.fromJson(contextJson);
                context = (noEnvScheduler != null)
                    ? WrapperNoEnvironment.context(clientEndpoint, clientApplication, transport, noEnvScheduler,
                        contextOptions.build(), data)
                    : client.context(contextOptions.build(), data);
                eventCollector.awaitEvent(0, 2000, "ready", "error");
            } else {
                // The SDK's own client fetches and decodes the payload from <endpoint>/context (or fails it, with
                // failLoad); a throttled fetch leaves the context loading.
                context = (noEnvScheduler != null)
                    ? WrapperNoEnvironment.newContext(clientEndpoint, clientApplication, transport, noEnvScheduler,
                        contextOptions.build())
                    : client.newContext(contextOptions.build());
                if (!(fetchOverHttp && payloadThrottle > 0)) {
                    awaitLoad(context);
                    eventCollector.awaitEvent(0, 2000, "ready", "error");
                }
            }

            String contextId = "ctx-" + System.currentTimeMillis() + "-" + Math.random();
            ContextWrapper wrapper = new ContextWrapper(context, eventCollector, transport, owner, contextJson != null);
            contexts.put(contextId, wrapper);

            Map<String, Object> response = new HashMap<>();
            response.put("result", status(context, contextId));
            // Return exactly what the SDK emitted through its event listener
            // during creation. Never filter, dedupe or synthesize ready/error
            // events: the conformance suite attributes these to the SDK.
            List<Map<String, Object>> createEvents = eventCollector.getEvents();
            response.put("events", createEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            e.printStackTrace();
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(errorBody(errorMessage(e), createCollector != null ? createCollector.getEvents() : Collections.emptyList()));
        }
    }

    // Waits until the load finished, successfully or not; a failed load leaves the context FAILED, not thrown.
    private static void awaitLoad(Context context) {
        context.readyAsync().handle((ready, error) -> null).join();
    }

    // The wire status of a context. ready mirrors 1.x isReady(): true once the load finished, failed or not.
    private static Map<String, Object> status(Context context, String contextId) {
        Map<String, Object> result = new HashMap<>();
        result.put("contextId", contextId);
        result.put("ready", isReady(context));
        result.put("failed", isFailed(context));
        result.put("finalized", context.state() == ContextState.CLOSED);
        return result;
    }

    static boolean isReady(Context context) {
        return context.state() != ContextState.LOADING;
    }

    static boolean isFailed(Context context) {
        return context.failure() != null;
    }

    @PostMapping("/context/{contextId}/setUnit")
    public ResponseEntity<?> setUnit(
        @PathVariable String contextId,
        @RequestBody Map<String, Object> request
    ) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            String unitType = (String) request.get("unitType");
            Object uid = request.get("uid");

            if (uid instanceof Number) {
                data.getContext().setUnit(unitType, (Number) uid);
            } else {
                data.getContext().setUnit(unitType, (String) uid);
            }

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/getUnit")
    public ResponseEntity<?> getUnit(
        @PathVariable String contextId,
        @RequestBody Map<String, Object> request
    ) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            String unitType = (String) request.get("unitType");
            Object result = data.getContext().unitValue(unitType);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/attribute")
    public ResponseEntity<?> setAttribute(
        @PathVariable String contextId,
        @RequestBody Map<String, Object> request
    ) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            String name = (String) request.get("name");
            Object value = request.get("value");

            data.getContext().setAttribute(name, value);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/getAttribute")
    public ResponseEntity<?> getAttribute(
        @PathVariable String contextId,
        @RequestBody Map<String, Object> request
    ) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            String name = (String) request.get("name");
            Object result = data.getContext().attribute(name);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/treatment")
    public ResponseEntity<?> getTreatment(
        @PathVariable String contextId,
        @RequestBody Map<String, Object> request
    ) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            String experimentName = (String) request.get("experimentName");
            int variant = data.getContext().treatment(experimentName);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", variant);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            e.printStackTrace();
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/peek")
    public ResponseEntity<?> peekTreatment(
        @PathVariable String contextId,
        @RequestBody Map<String, Object> request
    ) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            String experimentName = (String) request.get("experimentName");
            int variant = data.getContext().peekTreatment(experimentName);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", variant);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/variableValue")
    public ResponseEntity<?> getVariableValue(
        @PathVariable String contextId,
        @RequestBody Map<String, Object> request
    ) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            String key = (String) request.get("key");
            Object defaultValue = request.get("defaultValue");

            Object result = data.getContext().variableValue(key, defaultValue);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            e.printStackTrace();
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/peekVariableValue")
    public ResponseEntity<?> peekVariableValue(
        @PathVariable String contextId,
        @RequestBody Map<String, Object> request
    ) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            String key = (String) request.get("key");
            Object defaultValue = request.get("defaultValue");

            Object result = data.getContext().peekVariableValue(key, defaultValue);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            e.printStackTrace();
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/track")
    public ResponseEntity<?> track(
        @PathVariable String contextId,
        @RequestBody Map<String, Object> request
    ) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            String goalName = (String) request.get("goalName");
            Object propertiesObj = request.get("properties");
            Map<String, Object> properties = null;

            if (propertiesObj != null) {
                if (!(propertiesObj instanceof Map)) {
                    return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                        .body(errorBody("Goal '" + goalName + "' properties must be of type object.", Collections.emptyList()));
                }
                @SuppressWarnings("unchecked")
                Map<String, Object> propsMap = (Map<String, Object>) propertiesObj;
                properties = propsMap;
            }

            data.getContext().track(goalName, properties);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/override")
    public ResponseEntity<?> setOverride(
        @PathVariable String contextId,
        @RequestBody Map<String, Object> request
    ) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            String experimentName = (String) request.get("experimentName");
            Integer variant = (Integer) request.get("variant");

            data.getContext().setOverride(experimentName, variant);

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", data.getEventCollector().getNewEvents(eventsBefore));

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/customAssignment")
    public ResponseEntity<?> setCustomAssignment(
        @PathVariable String contextId,
        @RequestBody Map<String, Object> request
    ) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            String experimentName = (String) request.get("experimentName");
            Integer variant = (Integer) request.get("variant");

            data.getContext().setCustomAssignment(experimentName, variant);

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", data.getEventCollector().getNewEvents(eventsBefore));

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/customFieldValue")
    public ResponseEntity<?> getCustomFieldValue(
        @PathVariable String contextId,
        @RequestBody Map<String, Object> request
    ) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            String experimentName = (String) request.get("experimentName");
            String fieldName = (String) request.get("fieldName");

            Object result = data.getContext().customFieldValue(experimentName, fieldName);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/variableKeys")
    public ResponseEntity<?> getVariableKeys(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            Map<String, List<String>> keys = data.getContext().variableKeys();
            List<String> result = new ArrayList<>(keys.keySet());

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/customFieldKeys")
    public ResponseEntity<?> getCustomFieldKeys(
        @PathVariable String contextId,
        @RequestBody Map<String, Object> request
    ) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            List<String> result = data.getContext().customFieldKeys();

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/customFieldValueType")
    public ResponseEntity<?> getCustomFieldValueType(
        @PathVariable String contextId,
        @RequestBody Map<String, Object> request
    ) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            String experimentName = (String) request.get("experimentName");
            String fieldName = (String) request.get("fieldName");

            Object valueType = data.getContext().customFieldType(experimentName, fieldName);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", valueType);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/setOverride")
    public ResponseEntity<?> setOverrideEndpoint(
        @PathVariable String contextId,
        @RequestBody Map<String, Object> request
    ) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            String experimentName = (String) request.get("experimentName");
            Integer variant = (Integer) request.get("variant");

            data.getContext().setOverride(experimentName, variant);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/setCustomAssignment")
    public ResponseEntity<?> setCustomAssignmentEndpoint(
        @PathVariable String contextId,
        @RequestBody Map<String, Object> request
    ) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            String experimentName = (String) request.get("experimentName");
            Integer variant = (Integer) request.get("variant");

            data.getContext().setCustomAssignment(experimentName, variant);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @GetMapping("/context/{contextId}/pending")
    public ResponseEntity<?> getPendingCount(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        int pendingCount = data.getContext().pendingEventCount();

        Map<String, Object> response = new HashMap<>();
        response.put("result", pendingCount);
        response.put("events", data.getEventCollector().getNewEvents(eventsBefore));

        return ResponseEntity.ok(response);
    }

    @GetMapping("/context/{contextId}/isFinalized")
    public ResponseEntity<?> isFinalized(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        Object result = data.getContext().state() == ContextState.CLOSED;
        Map<String, Object> response = new HashMap<>();
        response.put("result", result);
        response.put("events", data.getEventCollector().getNewEvents(eventsBefore));

        return ResponseEntity.ok(response);
    }

    @GetMapping("/context/{contextId}/isReady")
    public ResponseEntity<?> isReady(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        Object result = isReady(data.getContext());
        Map<String, Object> response = new HashMap<>();
        response.put("result", result);
        response.put("events", data.getEventCollector().getNewEvents(eventsBefore));

        return ResponseEntity.ok(response);
    }

    @GetMapping("/context/{contextId}/isFailed")
    public ResponseEntity<?> isFailed(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        Object result = isFailed(data.getContext());
        Map<String, Object> response = new HashMap<>();
        response.put("result", result);
        response.put("events", data.getEventCollector().getNewEvents(eventsBefore));

        return ResponseEntity.ok(response);
    }

    @GetMapping("/context/{contextId}/experiments")
    public ResponseEntity<?> getExperiments(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            List<String> experiments = data.getContext().experiments();

            Map<String, Object> response = new HashMap<>();
            response.put("result", experiments);
            response.put("events", data.getEventCollector().getNewEvents(eventsBefore));

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/getUnits")
    public ResponseEntity<?> getUnits(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }
        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            Map<String, Object> result = data.getContext().unitValues();
            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);
            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", newEvents);
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/getAttributes")
    public ResponseEntity<?> getAttributes(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }
        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            Map<String, Object> result = data.getContext().attributes();
            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);
            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", newEvents);
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/readyError")
    public ResponseEntity<?> readyError(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }
        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            Throwable error = data.getContext().failure();
            Map<String, Object> result = error != null
                ? Map.of("isError", true, "message", error.getMessage() != null ? error.getMessage() : error.toString())
                : null;
            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", data.getEventCollector().getNewEvents(eventsBefore));
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/variableKeysMap")
    public ResponseEntity<?> variableKeysMap(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }
        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            Map<String, List<String>> keys = data.getContext().variableKeys();
            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);
            Map<String, Object> response = new HashMap<>();
            response.put("result", keys);
            response.put("events", newEvents);
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/globalCustomFieldKeys")
    public ResponseEntity<?> globalCustomFieldKeys(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }
        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            List<String> keys = data.getContext().customFieldKeys();
            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);
            Map<String, Object> response = new HashMap<>();
            response.put("result", keys);
            response.put("events", newEvents);
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/publishFail")
    public ResponseEntity<?> publishFail(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }
        data.setPublishFail(true);
        Map<String, Object> response = new HashMap<>();
        response.put("result", null);
        response.put("events", Collections.emptyList());
        return ResponseEntity.ok(response);
    }

    @PostMapping("/context/{contextId}/publish")
    public ResponseEntity<?> publish(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            data.getContext().publish();

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            e.printStackTrace();
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/refresh")
    public ResponseEntity<?> refresh(
        @PathVariable String contextId,
        @RequestBody Map<String, Object> request
    ) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            if (request.containsKey("newData") && data.acceptsNewData()) {
                data.getTransport().queuePayload(objectMapper.writeValueAsString(request.get("newData")));
            }

            boolean wasFailed = isFailed(data.getContext());
            data.getContext().refresh();
            if (!wasFailed) {
                data.getEventCollector().awaitEvent(eventsBefore, 2000, "refresh", "error");
            }

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            e.printStackTrace();
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @PostMapping("/context/{contextId}/finalize")
    public ResponseEntity<?> finalizeContext(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int eventsBefore = data.getEventCollector().getEvents().size();
        try {
            boolean wasClosed = data.getContext().state() == ContextState.CLOSED;
            data.getContext().close();
            if (!wasClosed) {
                data.getEventCollector().awaitEvent(eventsBefore, 2000, "finalize", "error");
            }

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            e.printStackTrace();
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(errorBody(errorMessage(e), data.getEventCollector().getNewEvents(eventsBefore)));
        }
    }

    @DeleteMapping("/context/{contextId}")
    public ResponseEntity<?> deleteContext(@PathVariable String contextId) {
        ContextWrapper removed = contexts.remove(contextId);
        if (removed != null) {
            // Release the per-context client (its scheduler and HTTP transport).
            removed.closeClient();
        }

        Map<String, Object> response = new HashMap<>();
        response.put("result", "deleted");

        return ResponseEntity.ok(response);
    }
}
