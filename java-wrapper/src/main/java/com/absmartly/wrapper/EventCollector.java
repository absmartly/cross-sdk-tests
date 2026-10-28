package com.absmartly.wrapper;

import com.absmartly.sdk.Context;
import com.absmartly.sdk.ContextEventLogger;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class EventCollector implements ContextEventLogger {
    private final List<Map<String, Object>> events = Collections.synchronizedList(new ArrayList<>());
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Override
    public void handleEvent(Context context, EventType type, Object data) {
        Map<String, Object> event = new HashMap<>();
        event.put("type", mapEventType(type));
        event.put("data", deepCopy(data));
        event.put("timestamp", System.currentTimeMillis());
        events.add(event);
    }

    private String mapEventType(EventType type) {
        switch (type) {
            case Ready:
                return "ready";
            case Refresh:
                return "refresh";
            case Publish:
                return "publish";
            case Exposure:
                return "exposure";
            case Goal:
                return "goal";
            case Close:
                return "finalize";
            case Error:
                return "error";
            default:
                return type.name().toLowerCase();
        }
    }

    private Object deepCopy(Object data) {
        if (data == null) {
            return null;
        }
        try {
            String json = objectMapper.writeValueAsString(data);
            return objectMapper.readValue(json, Object.class);
        } catch (Exception e) {
            return data;
        }
    }

    public List<Map<String, Object>> getEvents() {
        synchronized (events) {
            return new ArrayList<>(events);
        }
    }

    // The SDK completes its futures before it logs the matching event (as in 1.6.3), so a
    // caller that just returned from waitUntilReady()/refresh()/close() may be a moment ahead
    // of the logger. Wait for the event itself; nothing is synthesized if it never arrives.
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
