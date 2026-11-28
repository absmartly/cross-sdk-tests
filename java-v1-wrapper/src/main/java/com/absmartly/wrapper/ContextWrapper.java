package com.absmartly.wrapper;

import com.absmartly.sdk.Client;
import com.absmartly.sdk.Context;

import java.io.IOException;

public class ContextWrapper {
    private Context context;
    private EventCollector eventCollector;
    private DummyContextDataProvider dataProvider;
    private CustomPublisher eventHandler;
    private boolean publishFail;
    private Client client;
    private com.absmartly.sdk.ABSmartly sdk;

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

    public void setClient(Client client) {
        this.client = client;
    }

    public void setSdk(com.absmartly.sdk.ABSmartly sdk) {
        this.sdk = sdk;
    }

    /** Close the context, if created, before its scheduler; release the transport even if either close fails. */
    public void closeClient() {
        try {
            if (context != null) {
                context.close();
            }
        } catch (Exception ignored) {
        } finally {
            try {
                if (sdk != null) {
                    sdk.close();
                }
            } catch (Exception ignored) {
            } finally {
                try {
                    if (client != null) {
                        client.close();
                    }
                } catch (IOException ignored) {
                }
            }
        }
    }
}
