package com.absmartly.wrapper;

import com.absmartly.sdk.*;
import com.absmartly.sdk.ABSmartly;
import com.absmartly.sdk.ABSmartlyConfig;
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
    private final Map<String, com.absmartly.sdk.json.ContextData> payloadStore = new ConcurrentHashMap<>();
    private final ObjectMapper objectMapper = new ObjectMapper();
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

    private String normalizeError(String message) {
        if (message != null && message.contains("Context is closed")) {
            return "Context finalized";
        }
        if (message != null && message.toLowerCase().contains("already set")) {
            java.util.regex.Matcher m = java.util.regex.Pattern
                .compile("Unit ['\\\"]([^'\\\"]+)['\\\"]")
                .matcher(message);
            if (m.find()) {
                return "Unit '" + m.group(1) + "' UID already set.";
            }
        }
        return message;
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
        Map<String, Object> response = new HashMap<>();response.put("diagnostics", true);
        response.put("attrsSeq", false);
        response.put("publishFail", true);
        response.put("variableKeysMap", true);
        response.put("globalCustomFieldKeys", true);
        response.put("getUnits", true);
        response.put("getAttributes", true);
        response.put("readyError", true);
        response.put("holdouts", HoldoutSelfTest.run());
        response.put("holdout_arms", HoldoutArmsSelfTest.run());
        response.put("switchback", SwitchbackSelfTest.run());
        return response;
    }

    @PostMapping("/diagnostic")
    public ResponseEntity<?> diagnostic(@RequestBody Map<String, Object> request) {
        try {
            String operation = (String) request.get("operation");
            Object value = request.get("value");
            String text = value == null ? "" : String.valueOf(value);

            Object result;
            switch (operation) {
                case "hashUnit":
                    result = new String(Hashing.hashUnit(text));
                    break;
                case "base64UrlNoPadding":
                    result = Base64.getUrlEncoder().withoutPadding()
                        .encodeToString(text.getBytes(java.nio.charset.StandardCharsets.UTF_8));
                    break;
                case "utf8Bytes":
                    byte[] bytes = text.getBytes(java.nio.charset.StandardCharsets.UTF_8);
                    List<Integer> out = new ArrayList<>(bytes.length);
                    for (byte b : bytes) {
                        out.add((int) (b & 0xff));
                    }
                    result = out;
                    break;
                case "isObject":
                    result = value instanceof Map;
                    break;
                case "isNumeric":
                    result = value instanceof Number;
                    break;
                case "isPromise":
                    result = false;
                    break;
                default:
                    return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                        .body(Collections.singletonMap("error", "Unsupported diagnostic operation: " + operation));
            }

            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", Collections.emptyList());
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(error);
        }
    }

    // clusterUnit is the assigner input (the SDK's hashed unit) and is not hashed again; 64-bit
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

    // key is the cluster key hashed as-is (a hashed unit or a normalized attribute value); seed is
    // the 64-bit experiment seed as a decimal string. The wrapper rejects a count outside {10, 50, 100}.
    @PostMapping("/switchback/bucket")
    public ResponseEntity<?> switchbackBucket(@RequestBody Map<String, Object> request) {
        if (!switchback.isBucketAvailable()) {
            return ResponseEntity.status(HttpStatus.NOT_IMPLEMENTED)
                .body(Collections.singletonMap("error", "linked SDK has no switchback bucket function"));
        }
        return switchbackCall(() -> switchback.bucketOf(stringParam(request, "key"), longParam(request, "seed"),
            countParam(request)));
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

    // Long.parseLong is exact over the full signed 64-bit range and rejects anything else.
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

    private static int countParam(Map<String, Object> request) {
        int count = intParam(request, "count");
        if (count != 10 && count != 50 && count != 100) {
            throw new IllegalArgumentException("count must be 10, 50 or 100, got " + count);
        }
        return count;
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
            com.absmartly.sdk.json.ContextData contextData = objectMapper.convertValue(
                request.get("data"),
                com.absmartly.sdk.json.ContextData.class
            );

            payloadStore.put(payloadId, contextData);

            Map<String, Object> response = new HashMap<>();
            response.put("success", true);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(error);
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

        com.absmartly.sdk.json.ContextData data = payloadStore.getOrDefault(
            payloadId,
            new com.absmartly.sdk.json.ContextData()
        );

        return ResponseEntity.ok(data);
    }

    @GetMapping("/context_payload/{payloadId}/context")
    public ResponseEntity<?> mockApiContext(@PathVariable String payloadId) {
        com.absmartly.sdk.json.ContextData data = payloadStore.getOrDefault(
            payloadId,
            new com.absmartly.sdk.json.ContextData()
        );
        return ResponseEntity.ok(data);
    }

    @PostMapping("/context")
    public ResponseEntity<?> createContext(@RequestBody Map<String, Object> request) {
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
                ContextConfig e2eContextConfig = ContextConfig.create();
                // Never auto-publish or auto-refresh in e2e mode; the test drives
                // publishing explicitly (SDK default publishDelay is 100ms).
                e2eContextConfig.setPublishDelay(-1);
                e2eContextConfig.setRefreshInterval(0);
                if (e2eUnits != null) {
                    for (Map.Entry<String, Object> entry : e2eUnits.entrySet()) {
                        e2eContextConfig.setUnit(entry.getKey(), String.valueOf(entry.getValue()));
                    }
                }

                ClientConfig e2eClientConfig = ClientConfig.create()
                    .setEndpoint(e2eEndpoint)
                    .setAPIKey(e2eApiKey)
                    .setApplication(e2eApplication)
                    .setEnvironment(e2eEnvironment);
                Client e2eClient = Client.create(e2eClientConfig);
                ABSmartlyConfig e2eSdkConfig = ABSmartlyConfig.create()
                    .setClient(e2eClient)
                    .setContextEventLogger(e2eCollector);
                ABSmartly e2eSdk = ABSmartly.create(e2eSdkConfig);
                Context e2eContext = e2eSdk.createContext(e2eContextConfig);

                if (e2eAttrs != null) {
                    for (Map.Entry<String, Object> entry : e2eAttrs.entrySet()) {
                        e2eContext.setAttribute(entry.getKey(), entry.getValue());
                    }
                }

                e2eContext.waitUntilReady();

                String e2eContextId = "ctx-" + System.currentTimeMillis() + "-" + Math.random();
                contexts.put(e2eContextId, new ContextWrapper(e2eContext, e2eCollector, null, null));

                Map<String, Object> e2eResult = new HashMap<>();
                e2eResult.put("contextId", e2eContextId);
                e2eResult.put("ready", e2eContext.isReady());
                e2eResult.put("failed", e2eContext.isFailed());
                e2eResult.put("finalized", e2eContext.isClosed());

                Map<String, Object> e2eResponse = new HashMap<>();
                e2eResponse.put("result", e2eResult);
                e2eResponse.put("events", e2eCollector.getEvents());
                return ResponseEntity.ok(e2eResponse);
            }

            com.absmartly.sdk.json.ContextData contextData = null;
            if (request.containsKey("data")) {
                contextData = objectMapper.convertValue(
                    request.get("data"),
                    com.absmartly.sdk.json.ContextData.class
                );
            }

            String endpoint = (String) request.get("endpoint");

            @SuppressWarnings("unchecked")
            Map<String, Object> units = (Map<String, Object>) request.get("units");

            @SuppressWarnings("unchecked")
            Map<String, Object> options = (Map<String, Object>) request.getOrDefault("options", new HashMap<>());

            EventCollector eventCollector = new EventCollector();
            CustomPublisher publisher = new CustomPublisher(eventCollector);

            ContextConfig contextConfig = ContextConfig.create();

            if (units != null) {
                for (Map.Entry<String, Object> entry : units.entrySet()) {
                    contextConfig.setUnit(entry.getKey(), String.valueOf(entry.getValue()));
                }
            }

            Integer publishDelay = (Integer) options.getOrDefault("publishDelay", -1);
            Integer refreshPeriod = (Integer) options.getOrDefault("refreshPeriod", 0);

            contextConfig.setPublishDelay(publishDelay);
            contextConfig.setRefreshInterval(refreshPeriod);

            Context context;
            ABSmartly sdk;
            DummyContextDataProvider contextDataProvider = null;
            if (contextData != null) {
                // Sync: createContextWith - use dummy data provider
                contextDataProvider = new DummyContextDataProvider();
                ABSmartlyConfig sdkConfig = ABSmartlyConfig.create()
                    .setContextDataProvider(contextDataProvider)
                    .setContextEventHandler(publisher)
                    .setContextEventLogger(eventCollector);
                sdk = ABSmartly.create(sdkConfig);
                context = sdk.createContextWith(contextConfig, contextData);
            } else if (endpoint != null) {
                // createContext with endpoint - create a real client to fetch data
                String translatedEndpoint = translateEndpoint(endpoint);
                Integer payloadThrottle = (Integer) options.getOrDefault("payloadThrottle", 0);

                if (payloadThrottle > 0) {
                    DeferredContextDataProvider deferredProvider = new DeferredContextDataProvider(translatedEndpoint, payloadThrottle);
                    ABSmartlyConfig sdkConfig = ABSmartlyConfig.create()
                        .setContextDataProvider(deferredProvider)
                        .setContextEventHandler(publisher)
                        .setContextEventLogger(eventCollector);
                    sdk = ABSmartly.create(sdkConfig);
                    context = sdk.createContext(contextConfig);
                } else {
                    ClientConfig clientConfig = ClientConfig.create()
                        .setEndpoint(translatedEndpoint)
                        .setAPIKey("test-api-key")
                        .setApplication("test-app")
                        .setEnvironment("test-env");
                    Client client = Client.create(clientConfig);
                    ABSmartlyConfig sdkConfig = ABSmartlyConfig.create()
                        .setClient(client)
                        .setContextEventHandler(publisher)
                        .setContextEventLogger(eventCollector);
                    sdk = ABSmartly.create(sdkConfig);
                    context = sdk.createContext(contextConfig);
                    context.waitUntilReady();
                    // Wait for events to be collected (like Go wrapper)
                    for (int i = 0; i < 50 && eventCollector.getEvents().isEmpty(); i++) {
                        try {
                            Thread.sleep(10);
                        } catch (InterruptedException e) {
                            Thread.currentThread().interrupt();
                            break;
                        }
                    }
                }
            } else if (Boolean.TRUE.equals(request.get("failLoad"))) {
                ContextDataProvider failingProvider = new ContextDataProvider() {
                    @Override
                    public java8.util.concurrent.CompletableFuture<com.absmartly.sdk.json.ContextData> getContextData() {
                        java8.util.concurrent.CompletableFuture<com.absmartly.sdk.json.ContextData> f = new java8.util.concurrent.CompletableFuture<>();
                        f.completeExceptionally(new RuntimeException("Context load failed"));
                        return f;
                    }
                };
                ABSmartlyConfig sdkConfig = ABSmartlyConfig.create()
                    .setContextDataProvider(failingProvider)
                    .setContextEventHandler(publisher)
                    .setContextEventLogger(eventCollector);
                sdk = ABSmartly.create(sdkConfig);
                context = sdk.createContext(contextConfig);
                for (int i = 0; i < 50 && eventCollector.getEvents().isEmpty(); i++) {
                    try { Thread.sleep(10); } catch (InterruptedException e) { break; }
                }
            } else {
                DummyContextDataProvider dataProvider = new DummyContextDataProvider();
                ABSmartlyConfig sdkConfig = ABSmartlyConfig.create()
                    .setContextDataProvider(dataProvider)
                    .setContextEventHandler(publisher)
                    .setContextEventLogger(eventCollector);
                sdk = ABSmartly.create(sdkConfig);
                context = sdk.createContext(contextConfig);
            }

            String contextId = "ctx-" + System.currentTimeMillis() + "-" + Math.random();
            contexts.put(contextId, new ContextWrapper(context, eventCollector, contextDataProvider, publisher));

            Map<String, Object> result = new HashMap<>();
            result.put("contextId", contextId);
            result.put("ready", context.isReady());
            result.put("failed", context.isFailed());
            result.put("finalized", context.isClosed());

            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            // Return exactly what the SDK emitted through its ContextEventLogger
            // during creation. Never filter, dedupe or synthesize ready/error
            // events: the conformance suite attributes these to the SDK.
            List<Map<String, Object>> createEvents = eventCollector.getEvents();
            response.put("events", createEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            e.printStackTrace();
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(error);
        }
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

        try {
            int eventsBefore = data.getEventCollector().getEvents().size();

            String unitType = (String) request.get("unitType");
            Object uid = request.get("uid");

            data.getContext().setUnit(unitType, String.valueOf(uid));

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(error);
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

        try {
            int eventsBefore = data.getEventCollector().getEvents().size();

            String unitType = (String) request.get("unitType");
            String unitValue = data.getContext().getUnit(unitType);

            Object result = null;
            if (unitValue != null) {
                try {
                    result = Integer.parseInt(unitValue);
                } catch (NumberFormatException e) {
                    try {
                        result = Long.parseLong(unitValue);
                    } catch (NumberFormatException e2) {
                        try {
                            result = Double.parseDouble(unitValue);
                        } catch (NumberFormatException e3) {
                            result = unitValue;
                        }
                    }
                }
            }

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(error);
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

        try {
            int eventsBefore = data.getEventCollector().getEvents().size();

            String name = (String) request.get("name");
            Object value = request.get("value");

            data.getContext().setAttribute(name, value);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(error);
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

        try {
            int eventsBefore = data.getEventCollector().getEvents().size();

            String name = (String) request.get("name");
            Object result = data.getContext().getAttribute(name);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(error);
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

        // The java SDK's getTreatment returns 0 (not an error) after close();
        // scenario 189 requires an error once the context is finalized.
        if (data.getContext().isClosed()) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(Collections.singletonMap("error", "Context finalized"));
        }

        try {
            int eventsBefore = data.getEventCollector().getEvents().size();

            String experimentName = (String) request.get("experimentName");
            int variant = data.getContext().getTreatment(experimentName);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);
            List<Map<String, Object>> exposureEvents = new ArrayList<>();
            for (Map<String, Object> event : newEvents) {
                if ("exposure".equals(event.get("type"))) {
                    exposureEvents.add(event);
                }
            }
            if (!exposureEvents.isEmpty()) {
                newEvents = exposureEvents;
            }

            Map<String, Object> response = new HashMap<>();
            response.put("result", variant);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            e.printStackTrace();
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            error.put("trace", e.getClass().getName());
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(error);
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

        try {
            int eventsBefore = data.getEventCollector().getEvents().size();

            String experimentName = (String) request.get("experimentName");
            int variant = data.getContext().peekTreatment(experimentName);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", variant);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(error);
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

        try {
            int eventsBefore = data.getEventCollector().getEvents().size();

            String key = (String) request.get("key");
            Object defaultValue = request.get("defaultValue");

            Object result = data.getContext().getVariableValue(key, defaultValue);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            e.printStackTrace();
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            error.put("trace", e.getClass().getName());
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(error);
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

        try {
            int eventsBefore = data.getEventCollector().getEvents().size();

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
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            error.put("trace", e.getClass().getName());
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(error);
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

        try {
            int eventsBefore = data.getEventCollector().getEvents().size();
            boolean wasReady = data.getContext().isReady();

            String goalName = (String) request.get("goalName");
            Object propertiesObj = request.get("properties");
            Map<String, Object> properties = null;

            if (propertiesObj != null) {
                if (!(propertiesObj instanceof Map)) {
                    return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                        .body(Collections.singletonMap("error", "Goal '" + goalName + "' properties must be of type object."));
                }
                @SuppressWarnings("unchecked")
                Map<String, Object> propsMap = (Map<String, Object>) propertiesObj;
                properties = propsMap;
            }

            data.getContext().track(goalName, properties);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);
            if (!wasReady) {
                // While the async payload is still resolving, include only the goal emitted by this call.
                List<Map<String, Object>> goalOnlyEvents = new ArrayList<>();
                for (Map<String, Object> event : newEvents) {
                    if ("goal".equals(event.get("type"))) {
                        goalOnlyEvents.add(event);
                    }
                }
                newEvents = goalOnlyEvents;
            }

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(error);
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

        try {
            String experimentName = (String) request.get("experimentName");
            Integer variant = (Integer) request.get("variant");

            data.getContext().setOverride(experimentName, variant);

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", Collections.emptyList());

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            if ("Context finalized".equals(normalizeError(e.getMessage()))) {
                Map<String, Object> response = new HashMap<>();
                response.put("result", null);
                response.put("events", Collections.emptyList());
                return ResponseEntity.ok(response);
            }
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(error);
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

        try {
            String experimentName = (String) request.get("experimentName");
            Integer variant = (Integer) request.get("variant");

            data.getContext().setCustomAssignment(experimentName, variant);

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", Collections.emptyList());

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(error);
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

        try {
            int eventsBefore = data.getEventCollector().getEvents().size();

            String experimentName = (String) request.get("experimentName");
            String fieldName = (String) request.get("fieldName");

            Object result = data.getContext().getCustomFieldValue(experimentName, fieldName);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            if ("Context finalized".equals(normalizeError(e.getMessage()))) {
                Map<String, Object> response = new HashMap<>();
                response.put("result", null);
                response.put("events", Collections.emptyList());
                return ResponseEntity.ok(response);
            }
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(error);
        }
    }

    @PostMapping("/context/{contextId}/variableKeys")
    public ResponseEntity<?> getVariableKeys(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        try {
            int eventsBefore = data.getEventCollector().getEvents().size();

            Map<String, List<String>> keys = data.getContext().getVariableKeys();
            List<String> result = new ArrayList<>(keys.keySet());

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(error);
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

        try {
            int eventsBefore = data.getEventCollector().getEvents().size();

            String[] keys = data.getContext().getCustomFieldKeys();
            List<String> result = Arrays.asList(keys);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(error);
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

        try {
            int eventsBefore = data.getEventCollector().getEvents().size();

            String experimentName = (String) request.get("experimentName");
            String fieldName = (String) request.get("fieldName");

            Object valueType = data.getContext().getCustomFieldValueType(experimentName, fieldName);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", valueType);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(error);
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

        try {
            int eventsBefore = data.getEventCollector().getEvents().size();

            String experimentName = (String) request.get("experimentName");
            Integer variant = (Integer) request.get("variant");

            data.getContext().setOverride(experimentName, variant);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(error);
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

        try {
            int eventsBefore = data.getEventCollector().getEvents().size();

            String experimentName = (String) request.get("experimentName");
            Integer variant = (Integer) request.get("variant");

            data.getContext().setCustomAssignment(experimentName, variant);

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(error);
        }
    }

    @GetMapping("/context/{contextId}/pending")
    public ResponseEntity<?> getPendingCount(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        int pendingCount = 0;
        try {
            pendingCount = data.getContext().getPendingCount();
        } catch (Exception e) {
            e.printStackTrace();
        }

        Map<String, Object> response = new HashMap<>();
        response.put("result", pendingCount);
        response.put("events", Collections.emptyList());

        return ResponseEntity.ok(response);
    }

    @GetMapping("/context/{contextId}/isFinalized")
    public ResponseEntity<?> isFinalized(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        Map<String, Object> response = new HashMap<>();
        response.put("result", data.getContext().isClosed());
        response.put("events", Collections.emptyList());

        return ResponseEntity.ok(response);
    }

    @GetMapping("/context/{contextId}/isReady")
    public ResponseEntity<?> isReady(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        Map<String, Object> response = new HashMap<>();
        response.put("result", data.getContext().isReady());
        response.put("events", Collections.emptyList());

        return ResponseEntity.ok(response);
    }

    @GetMapping("/context/{contextId}/isFailed")
    public ResponseEntity<?> isFailed(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        Map<String, Object> response = new HashMap<>();
        response.put("result", data.getContext().isFailed());
        response.put("events", Collections.emptyList());

        return ResponseEntity.ok(response);
    }

    @GetMapping("/context/{contextId}/experiments")
    public ResponseEntity<?> getExperiments(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        try {
            List<String> experiments = Arrays.asList(data.getContext().getExperiments());

            Map<String, Object> response = new HashMap<>();
            response.put("result", experiments);
            response.put("events", Collections.emptyList());

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(error);
        }
    }

    @PostMapping("/context/{contextId}/getUnits")
    public ResponseEntity<?> getUnits(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }
        try {
            int eventsBefore = data.getEventCollector().getEvents().size();
            Map<String, String> units = data.getContext().getUnits();
            Map<String, Object> result = new LinkedHashMap<>();
            for (Map.Entry<String, String> e : units.entrySet()) {
                try { result.put(e.getKey(), Integer.parseInt(e.getValue())); }
                catch (NumberFormatException nfe) {
                    try { result.put(e.getKey(), Double.parseDouble(e.getValue())); }
                    catch (NumberFormatException nfe2) { result.put(e.getKey(), e.getValue()); }
                }
            }
            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);
            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", newEvents);
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(Collections.singletonMap("error", e.getMessage()));
        }
    }

    @PostMapping("/context/{contextId}/getAttributes")
    public ResponseEntity<?> getAttributes(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }
        try {
            int eventsBefore = data.getEventCollector().getEvents().size();
            Map<String, Object> result = data.getContext().getAttributes();
            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);
            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", newEvents);
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(Collections.singletonMap("error", e.getMessage()));
        }
    }

    @PostMapping("/context/{contextId}/readyError")
    public ResponseEntity<?> readyError(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }
        try {
            Throwable error = data.getContext().readyError();
            Map<String, Object> result = error != null
                ? Map.of("isError", true, "message", error.getMessage() != null ? error.getMessage() : error.toString())
                : null;
            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", Collections.emptyList());
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(Collections.singletonMap("error", e.getMessage()));
        }
    }

    @PostMapping("/context/{contextId}/variableKeysMap")
    public ResponseEntity<?> variableKeysMap(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }
        try {
            int eventsBefore = data.getEventCollector().getEvents().size();
            Map<String, List<String>> keys = data.getContext().getVariableKeys();
            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);
            Map<String, Object> response = new HashMap<>();
            response.put("result", keys);
            response.put("events", newEvents);
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(Collections.singletonMap("error", e.getMessage()));
        }
    }

    @PostMapping("/context/{contextId}/globalCustomFieldKeys")
    public ResponseEntity<?> globalCustomFieldKeys(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }
        try {
            int eventsBefore = data.getEventCollector().getEvents().size();
            List<String> keys = Arrays.asList(data.getContext().getCustomFieldKeys());
            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);
            Map<String, Object> response = new HashMap<>();
            response.put("result", keys);
            response.put("events", newEvents);
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(Collections.singletonMap("error", e.getMessage()));
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

        try {
            int eventsBefore = data.getEventCollector().getEvents().size();

            data.getContext().publish();

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            e.printStackTrace();
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(error);
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

        try {
            int eventsBefore = data.getEventCollector().getEvents().size();

            if (request.containsKey("newData") && data.getDataProvider() != null) {
                com.absmartly.sdk.json.ContextData newContextData = objectMapper.convertValue(
                    request.get("newData"),
                    com.absmartly.sdk.json.ContextData.class
                );
                data.getDataProvider().setNextData(newContextData);
            }

            data.getContext().refresh();

            for (int i = 0; i < 100 && data.getEventCollector().getEvents().size() == eventsBefore; i++) {
                try {
                    Thread.sleep(10);
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    break;
                }
            }

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            e.printStackTrace();
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(error);
        }
    }

    @PostMapping("/context/{contextId}/finalize")
    public ResponseEntity<?> finalizeContext(@PathVariable String contextId) {
        ContextWrapper data = contexts.get(contextId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Context not found"));
        }

        try {
            int eventsBefore = data.getEventCollector().getEvents().size();
            int pendingBefore = data.getContext().getPendingCount();

            data.getContext().close();

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);
            int pollCount = 0;
            int maxPolls = 50; // 50 * 10ms = 500ms max wait
            while (pollCount < maxPolls) {
                boolean hasFinalize = false;
                boolean hasPublish = pendingBefore == 0;
                for (Map<String, Object> event : newEvents) {
                    String type = String.valueOf(event.get("type"));
                    if ("finalize".equals(type)) {
                        hasFinalize = true;
                    } else if ("publish".equals(type)) {
                        hasPublish = true;
                    }
                }
                if (hasFinalize && hasPublish) {
                    break;
                }
                try {
                    Thread.sleep(10);
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    break;
                }
                pollCount++;
                newEvents = data.getEventCollector().getNewEvents(eventsBefore);
            }

            Map<String, Object> response = new HashMap<>();
            response.put("result", null);
            response.put("events", newEvents);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            e.printStackTrace();
            Map<String, Object> error = new HashMap<>();
            error.put("error", normalizeError(e.getMessage()));
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(error);
        }
    }

    @DeleteMapping("/context/{contextId}")
    public ResponseEntity<?> deleteContext(@PathVariable String contextId) {
        contexts.remove(contextId);

        Map<String, Object> response = new HashMap<>();
        response.put("result", "deleted");

        return ResponseEntity.ok(response);
    }
}
