package com.absmartly.wrapper;

import com.absmartly.sdk.Context;

/** One wrapper context: the SDK context, the client and transport behind it, and its event log. */
public class ContextWrapper {
    private final Context context;
    private final EventCollector eventCollector;
    private final WrapperTransport transport;
    private final AutoCloseable owner;
    // Only inline-data contexts take refresh(newData), as the 1.x wrapper's in-memory provider did.
    private final boolean acceptsNewData;

    public ContextWrapper(Context context, EventCollector eventCollector, WrapperTransport transport,
            AutoCloseable owner, boolean acceptsNewData) {
        this.context = context;
        this.eventCollector = eventCollector;
        this.transport = transport;
        this.owner = owner;
        this.acceptsNewData = acceptsNewData && transport != null && transport.fetchesFromMemory();
    }

    public Context getContext() {
        return context;
    }

    public EventCollector getEventCollector() {
        return eventCollector;
    }

    WrapperTransport getTransport() {
        return transport;
    }

    boolean acceptsNewData() {
        return acceptsNewData;
    }

    public void setPublishFail(boolean publishFail) {
        if (transport != null) {
            transport.failNextPublish(publishFail);
        }
    }

    /** Releases the per-context client (its scheduler and HTTP transport). */
    public void closeClient() {
        if (owner == null) {
            return;
        }
        try {
            owner.close();
        } catch (Exception ignored) {
        }
    }
}
