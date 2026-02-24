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

    public List<Map<String, Object>> getNewEvents(int since) {
        synchronized (events) {
            if (since >= events.size()) {
                return new ArrayList<>();
            }
            return new ArrayList<>(events.subList(since, events.size()));
        }
    }
}
