package com.absmartly.wrapper;

import com.absmartly.sdk.Context;
import com.absmartly.sdk.ContextEventHandler;
import com.absmartly.sdk.ContextEventLogger;
import com.absmartly.sdk.json.PublishEvent;
import java8.util.concurrent.CompletableFuture;

public class CustomContextEventHandler implements ContextEventHandler {
    private final EventCollector eventCollector;
    private volatile boolean shouldFail;

    public CustomContextEventHandler(EventCollector eventCollector) {
        this.eventCollector = eventCollector;
    }

    public void setShouldFail(boolean shouldFail) {
        this.shouldFail = shouldFail;
    }

    @Override
    public CompletableFuture<Void> publish(Context context, PublishEvent event) {
        if (shouldFail) {
            shouldFail = false;
            CompletableFuture<Void> future = new CompletableFuture<>();
            future.completeExceptionally(new RuntimeException("Publish failed"));
            return future;
        }
        return CompletableFuture.completedFuture(null);
    }
}
