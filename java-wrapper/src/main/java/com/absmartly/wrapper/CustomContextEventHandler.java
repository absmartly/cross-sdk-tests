package com.absmartly.wrapper;

import com.absmartly.sdk.Context;
import com.absmartly.sdk.ContextEventHandler;
import com.absmartly.sdk.ContextEventLogger;
import com.absmartly.sdk.json.PublishEvent;
import java8.util.concurrent.CompletableFuture;

public class CustomContextEventHandler implements ContextEventHandler {
    private final EventCollector eventCollector;

    public CustomContextEventHandler(EventCollector eventCollector) {
        this.eventCollector = eventCollector;
    }

    @Override
    public CompletableFuture<Void> publish(Context context, PublishEvent event) {
        // Don't log publish event here - SDK's event logger will handle it after this completes
        // Just return resolved future without HTTP call
        return CompletableFuture.completedFuture(null);
    }
}
