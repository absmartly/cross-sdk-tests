package com.absmartly.wrapper;

import com.absmartly.sdk.Exposure;
import com.absmartly.sdk.Goal;
import com.absmartly.sdk.event.ContextEvent;
import com.absmartly.sdk.event.ErrorEvent;
import com.absmartly.sdk.event.EventListener;
import com.absmartly.sdk.event.ExposureEvent;
import com.absmartly.sdk.event.GoalEvent;
import com.absmartly.sdk.event.PublishEvent;
import com.absmartly.sdk.event.ReadyEvent;
import com.absmartly.sdk.event.RefreshEvent;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Records the SDK's typed events in the wrapper wire format: the event names of WRAPPER_API_SPEC.md (the 2.0
 * {@code CLOSE} event is reported as {@code finalize}) and the payload each carried.
 */
public class EventCollector implements EventListener {
    private final List<Map<String, Object>> events = Collections.synchronizedList(new ArrayList<>());
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Override
    public void onEvent(ContextEvent event) {
        Map<String, Object> entry = new LinkedHashMap<>();
        entry.put("type", typeName(event.type()));
        entry.put("data", data(event));
        entry.put("timestamp", event.timestamp());
        events.add(entry);
    }

    static String typeName(ContextEvent.Type type) {
        switch (type) {
            case READY:
                return "ready";
            case REFRESH:
                return "refresh";
            case PUBLISH:
                return "publish";
            case EXPOSURE:
                return "exposure";
            case GOAL:
                return "goal";
            case CLOSE:
                return "finalize";
            case ERROR:
            default:
                return "error";
        }
    }

    private Object data(ContextEvent event) {
        switch (event.type()) {
            case READY:
                return parse(((ReadyEvent) event).data().toJson());
            case REFRESH:
                return parse(((RefreshEvent) event).data().toJson());
            case PUBLISH:
                return parse(((PublishEvent) event).toJson());
            case EXPOSURE:
                return exposure(((ExposureEvent) event).exposure());
            case GOAL: {
                Goal goal = ((GoalEvent) event).goal();
                Map<String, Object> data = new LinkedHashMap<>();
                data.put("name", goal.name());
                data.put("achievedAt", goal.achievedAt());
                data.put("properties", goal.properties());
                return data;
            }
            case ERROR: {
                Throwable cause = ((ErrorEvent) event).cause();
                return Collections.singletonMap("message",
                    cause.getMessage() != null ? cause.getMessage() : cause.toString());
            }
            case CLOSE:
            default:
                return null;
        }
    }

    // Preserve the SDK's values, including an empty or null unit, without 1.x normalization.
    static Map<String, Object> exposure(Exposure exposure) {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("id", exposure.id());
        data.put("name", exposure.name());
        data.put("unit", exposure.unit());
        data.put("variant", exposure.variant());
        data.put("exposedAt", exposure.exposedAt());
        data.put("assigned", exposure.assigned());
        data.put("eligible", exposure.eligible());
        data.put("overridden", exposure.overridden());
        data.put("fullOn", exposure.fullOn());
        data.put("custom", exposure.custom());
        data.put("audienceMismatch", exposure.audienceMismatch());
        data.put("ruleOverride", exposure.ruleOverride());
        return data;
    }

    private Object parse(String json) {
        try {
            return objectMapper.readValue(json, Object.class);
        } catch (Exception e) {
            return json;
        }
    }

    public List<Map<String, Object>> getEvents() {
        synchronized (events) {
            return new ArrayList<>(events);
        }
    }

    // The SDK completes its futures before it dispatches the matching event (as 1.x did), so a caller that just
    // returned from context()/refresh()/close() may be a moment ahead of the listener. Wait for the event
    // itself; nothing is synthesized if it never arrives.
    public void awaitEvent(int since, long timeoutMs, String... types) {
        final long deadline = System.nanoTime() + timeoutMs * 1_000_000L;
        while (System.nanoTime() < deadline) {
            synchronized (events) {
                for (int i = since; i < events.size(); i++) {
                    Object type = events.get(i).get("type");
                    for (String t : types) {
                        if (t.equals(type)) {
                            return;
                        }
                    }
                }
            }
            try {
                Thread.sleep(2);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            }
        }
    }

    public List<Map<String, Object>> getNewEvents(int since) {
        synchronized (events) {
            if (since >= events.size()) {
                return new ArrayList<>();
            }
            return new ArrayList<>(events.subList(since, events.size()));
        }
    }
}
