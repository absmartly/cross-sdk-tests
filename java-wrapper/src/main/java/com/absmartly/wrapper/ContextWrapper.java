package com.absmartly.wrapper;

import com.absmartly.sdk.Context;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

public class ContextWrapper {
    private Context context;
    private EventCollector eventCollector;
    private DummyContextDataProvider dataProvider;
    private CustomPublisher eventHandler;
    private boolean publishFail;
    private final Map<String, Object> unitInputs = new ConcurrentHashMap<>();

    public ContextWrapper(Context context, EventCollector eventCollector, DummyContextDataProvider dataProvider) {
        this.context = context;
        this.eventCollector = eventCollector;
        this.dataProvider = dataProvider;
    }

    public ContextWrapper(Context context, EventCollector eventCollector, DummyContextDataProvider dataProvider, CustomPublisher eventHandler) {
        this.context = context;
        this.eventCollector = eventCollector;
        this.dataProvider = dataProvider;
        this.eventHandler = eventHandler;
    }

    public Context getContext() {
        return context;
    }

    public EventCollector getEventCollector() {
        return eventCollector;
    }

    public DummyContextDataProvider getDataProvider() {
        return dataProvider;
    }

    /**
     * The JSON value the caller supplied for each unit type (at create or via
     * setUnit). The SDK stores every UID as a string; getUnit/getUnits return
     * the caller's original JSON value only when the SDK's stored string is
     * exactly its string form, so a numeric UID round-trips as a number while
     * a string UID that merely looks numeric (e.g. "123") stays a string.
     */
    public Map<String, Object> getUnitInputs() {
        return unitInputs;
    }

    public Object restoreUnitType(String unitType, String sdkValue) {
        if (sdkValue == null) {
            return null;
        }
        Object input = unitInputs.get(unitType);
        if (input instanceof Number && String.valueOf(input).equals(sdkValue)) {
            return input;
        }
        return sdkValue;
    }

    public boolean isPublishFail() {
        return publishFail;
    }

    public void setPublishFail(boolean publishFail) {
        this.publishFail = publishFail;
        if (eventHandler != null) {
            eventHandler.setShouldFail(publishFail);
        }
    }
}
