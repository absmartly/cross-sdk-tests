package com.absmartly.wrapper;

import com.absmartly.sdk.Context;

public class ContextWrapper {
    private Context context;
    private EventCollector eventCollector;
    private DummyContextDataProvider dataProvider;
    private CustomPublisher eventHandler;
    private boolean publishFail;

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
