package com.absmartly.wrapper;

import com.absmartly.sdk.Context;

public class ContextWrapper {
    private Context context;
    private EventCollector eventCollector;
    private DummyContextDataProvider dataProvider;

    public ContextWrapper(Context context, EventCollector eventCollector, DummyContextDataProvider dataProvider) {
        this.context = context;
        this.eventCollector = eventCollector;
        this.dataProvider = dataProvider;
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
}
