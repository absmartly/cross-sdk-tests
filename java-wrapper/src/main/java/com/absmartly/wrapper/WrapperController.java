package com.absmartly.wrapper;

import com.absmartly.sdk.*;
import com.absmartly.sdk.ABSmartly;
import com.absmartly.sdk.ABSmartlyConfig;
import com.absmartly.sdk.internal.Buffers;
import com.absmartly.sdk.internal.hashing.Hashing;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.*;

@RestController
public class WrapperController {
    private final Map<String, ContextWrapper> contexts = new ConcurrentHashMap<>();
    private final Map<String, com.absmartly.sdk.json.ContextData> payloadStore = new ConcurrentHashMap<>();
    private final ObjectMapper objectMapper = new ObjectMapper();

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
     * Unwraps async wrapper exceptions (CompletionException from either the
     * java8 backport or the JDK, ExecutionException) so the reported message is
     * the SDK/publisher's own error, not "java.lang.RuntimeException: ...".
     */
    private static Throwable rootCause(Throwable error) {
        Throwable current = error;
        while ((current instanceof java8.util.concurrent.CompletionException
                || current instanceof java.util.concurrent.CompletionException
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
        response.put("diagnosticsBase64", false);
        response.put("diagnosticsTypeChecks", false);
        response.put("diagnosticsIsPromise", false);
        response.put("attrsSeq", false);
        response.put("publishFail", true);
        response.put("variableKeysMap", true);
        response.put("globalCustomFieldKeys", true);
        response.put("getUnits", true);
        response.put("getAttributes", true);
        response.put("readyError", true);
        response.put("holdouts", HoldoutSelfTest.run());
        response.put("holdout_arms", HoldoutArmsSelfTest.run());
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
                    // base64UrlNoPadding, isObject, isNumeric and isPromise have no
                    // public implementation in the Java SDK.
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

        com.absmartly.sdk.json.ContextData data = payloadStore.getOrDefault(
            payloadId,
            new com.absmartly.sdk.json.ContextData()
        );

        return ResponseEntity.ok(data);
    }

    @GetMapping("/context_payload/{payloadId}/context")
    public ResponseEntity<?> mockApiContext(@PathVariable String payloadId) {
        // A never-stored payload id is a missing collector payload: answer 404
        // so the SDK's own fetch fails the context, instead of serving an
        // empty-but-valid payload that would make it silently ready.
        com.absmartly.sdk.json.ContextData data = payloadStore.get(payloadId);
        if (data == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Collections.singletonMap("error", "Payload not found"));
        }
        return ResponseEntity.ok(data);
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
                ContextConfig e2eContextConfig = ContextConfig.create();
                // Never auto-publish or auto-refresh in e2e mode; the test drives
                // publishing explicitly (SDK default publishDelay is 100ms).
                e2eContextConfig.setPublishDelay(-1);
                e2eContextConfig.setRefreshInterval(0);
                if (e2eUnits != null) {
                    e2eContextConfig.setUnits(e2eUnits);
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
            createCollector = eventCollector;
            CustomPublisher publisher = new CustomPublisher(eventCollector);

            ContextConfig contextConfig = ContextConfig.create();

            if (units != null) {
                contextConfig.setUnits(units);
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
                // createContext with endpoint: a real SDK Client fetches and
                // deserializes the payload from <endpoint>/context.
                String translatedEndpoint = translateEndpoint(endpoint);
                Integer payloadThrottle = (Integer) options.getOrDefault("payloadThrottle", 0);

                ClientConfig clientConfig = ClientConfig.create()
                    .setEndpoint(translatedEndpoint)
                    .setAPIKey("test-api-key")
                    .setApplication("test-app")
                    .setEnvironment("test-env");
                Client client = Client.create(clientConfig);

                if (payloadThrottle > 0) {
                    // Delay the SDK's own fetch; the context stays not-ready until it lands.
                    DeferredContextDataProvider deferredProvider = new DeferredContextDataProvider(
                        new DefaultContextDataProvider(client), payloadThrottle);
                    ABSmartlyConfig sdkConfig = ABSmartlyConfig.create()
                        .setContextDataProvider(deferredProvider)
                        .setContextEventHandler(publisher)
                        .setContextEventLogger(eventCollector);
                    sdk = ABSmartly.create(sdkConfig);
                    context = sdk.createContext(contextConfig);
                } else {
                    ABSmartlyConfig sdkConfig = ABSmartlyConfig.create()
                        .setClient(client)
                        .setContextEventHandler(publisher)
                        .setContextEventLogger(eventCollector);
                    sdk = ABSmartly.create(sdkConfig);
                    context = sdk.createContext(contextConfig);
                    context.waitUntilReady();
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
                context.waitUntilReady();
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
            ContextWrapper wrapper = new ContextWrapper(context, eventCollector, contextDataProvider, publisher);
            contexts.put(contextId, wrapper);

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
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(errorBody(errorMessage(e), createCollector != null ? createCollector.getEvents() : Collections.emptyList()));
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
            Object result = data.getContext().getUnit(unitType);

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
            Object result = data.getContext().getAttribute(name);

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
            int variant = data.getContext().getTreatment(experimentName);

            // Exact slice of everything the SDK logged during this call.
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

            Object result = data.getContext().getVariableValue(key, defaultValue);

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

            // Exact slice of everything the SDK logged during this call.
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

            Object result = data.getContext().getCustomFieldValue(experimentName, fieldName);

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
            Map<String, List<String>> keys = data.getContext().getVariableKeys();
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
            String[] keys = data.getContext().getCustomFieldKeys();
            List<String> result = Arrays.asList(keys);

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

            Object valueType = data.getContext().getCustomFieldValueType(experimentName, fieldName);

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
        int pendingCount = data.getContext().getPendingCount();

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
        Object result = data.getContext().isClosed();
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
        Object result = data.getContext().isReady();
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
        Object result = data.getContext().isFailed();
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
            List<String> experiments = Arrays.asList(data.getContext().getExperiments());

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
            Map<String, Object> result = data.getContext().getUnits();
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
            Map<String, Object> result = data.getContext().getAttributes();
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
            Throwable error = data.getContext().readyError();
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
            Map<String, List<String>> keys = data.getContext().getVariableKeys();
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
            List<String> keys = Arrays.asList(data.getContext().getCustomFieldKeys());
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
            if (request.containsKey("newData") && data.getDataProvider() != null) {
                com.absmartly.sdk.json.ContextData newContextData = objectMapper.convertValue(
                    request.get("newData"),
                    com.absmartly.sdk.json.ContextData.class
                );
                data.getDataProvider().setNextData(newContextData);
            }

            data.getContext().refresh();

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
            data.getContext().close();

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
        contexts.remove(contextId);

        Map<String, Object> response = new HashMap<>();
        response.put("result", "deleted");

        return ResponseEntity.ok(response);
    }
}
