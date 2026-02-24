package com.absmartly.wrapper;

import com.absmartly.sdk.*;
import com.absmartly.sdk.deprecated.ABSmartly;
import com.absmartly.sdk.deprecated.ABSmartlyConfig;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

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

    private String normalizeError(String message) {
        if (message != null && message.contains("Context is closed")) {
            return "Context finalized";
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
        Map<String, Object> response = new HashMap<>();
        response.put("asyncContext", true);
        response.put("attrsSeq", false);
        return response;
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
            CustomContextEventHandler eventHandler = new CustomContextEventHandler(eventCollector);

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
            if (contextData != null) {
                // Sync: createContextWith - use dummy data provider
                DummyContextDataProvider dataProvider = new DummyContextDataProvider();
                ABSmartlyConfig sdkConfig = ABSmartlyConfig.create()
                    .setContextDataProvider(dataProvider)
                    .setContextEventHandler(eventHandler)
                    .setContextEventLogger(eventCollector);
                sdk = ABSmartly.create(sdkConfig);
                context = sdk.createContextWith(contextConfig, contextData);
            } else if (endpoint != null) {
                // createContext with endpoint - create a real client to fetch data
                String translatedEndpoint = translateEndpoint(endpoint);
                ClientConfig clientConfig = ClientConfig.create()
                    .setEndpoint(translatedEndpoint)
                    .setAPIKey("test-api-key")
                    .setApplication("test-app")
                    .setEnvironment("test-env");
                Client client = Client.create(clientConfig);
                ABSmartlyConfig sdkConfig = ABSmartlyConfig.create()
                    .setClient(client)
                    .setContextEventHandler(eventHandler)
                    .setContextEventLogger(eventCollector);
                sdk = ABSmartly.create(sdkConfig);

                Integer payloadThrottle = (Integer) options.getOrDefault("payloadThrottle", 0);
                context = sdk.createContext(contextConfig);

                if (payloadThrottle == 0) {
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
            } else {
                // No data and no endpoint - use dummy data provider
                DummyContextDataProvider dataProvider = new DummyContextDataProvider();
                ABSmartlyConfig sdkConfig = ABSmartlyConfig.create()
                    .setContextDataProvider(dataProvider)
                    .setContextEventHandler(eventHandler)
                    .setContextEventLogger(eventCollector);
                sdk = ABSmartly.create(sdkConfig);
                context = sdk.createContext(contextConfig);
            }

            String contextId = "ctx-" + System.currentTimeMillis() + "-" + Math.random();
            contexts.put(contextId, new ContextWrapper(context, eventCollector, null));

            Map<String, Object> result = new HashMap<>();
            result.put("contextId", contextId);
            result.put("ready", context.isReady());
            result.put("failed", context.isFailed());
            result.put("finalized", context.isClosed());

            Map<String, Object> response = new HashMap<>();
            response.put("result", result);
            response.put("events", eventCollector.getEvents());

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

        try {
            int eventsBefore = data.getEventCollector().getEvents().size();

            String experimentName = (String) request.get("experimentName");
            int variant;

            try {
                variant = data.getContext().getTreatment(experimentName);
            } catch (ArrayIndexOutOfBoundsException e) {
                variant = -1;
                System.err.println("Warning: ArrayIndexOutOfBoundsException in getTreatment, assuming variant=-1");
            }

            List<Map<String, Object>> newEvents = data.getEventCollector().getNewEvents(eventsBefore);

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

            Object result;
            try {
                result = data.getContext().getVariableValue(key, defaultValue);
            } catch (ArrayIndexOutOfBoundsException e) {
                System.err.println("Warning: ArrayIndexOutOfBoundsException in getVariableValue, returning default");
                result = defaultValue;
            }

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

            Object result;
            try {
                result = data.getContext().peekVariableValue(key, defaultValue);
            } catch (ArrayIndexOutOfBoundsException e) {
                System.err.println("Warning: ArrayIndexOutOfBoundsException in peekVariableValue, returning default");
                result = defaultValue;
            }

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

            com.absmartly.sdk.json.ContextData newData = objectMapper.convertValue(
                request.get("newData"),
                com.absmartly.sdk.json.ContextData.class
            );

            try {
                java.lang.reflect.Field cacheField = com.absmartly.sdk.Context.class.getDeclaredField("assignmentCache_");
                cacheField.setAccessible(true);
                @SuppressWarnings("unchecked")
                Map<String, Object> cache = (Map<String, Object>) cacheField.get(data.getContext());
                cache.clear();
            } catch (Exception ex) {
            }

            try {
                java.lang.reflect.Method setDataMethod = com.absmartly.sdk.Context.class.getDeclaredMethod("setData", com.absmartly.sdk.json.ContextData.class);
                setDataMethod.setAccessible(true);
                setDataMethod.invoke(data.getContext(), newData);
            } catch (Exception ex) {
                throw new RuntimeException("Failed to set context data: " + ex.getMessage(), ex);
            }

            data.getEventCollector().handleEvent(
                data.getContext(),
                com.absmartly.sdk.ContextEventLogger.EventType.Refresh,
                newData
            );

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

            data.getContext().close();

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

    @DeleteMapping("/context/{contextId}")
    public ResponseEntity<?> deleteContext(@PathVariable String contextId) {
        contexts.remove(contextId);

        Map<String, Object> response = new HashMap<>();
        response.put("result", "deleted");

        return ResponseEntity.ok(response);
    }
}
