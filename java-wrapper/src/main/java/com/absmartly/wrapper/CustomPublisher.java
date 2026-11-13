package com.absmartly.wrapper;

import com.absmartly.sdk.Context;
import com.absmartly.sdk.ContextEventHandler;
import com.absmartly.sdk.json.PublishEvent;
import java8.util.concurrent.CompletableFuture;

// Test contexts publish nowhere, so a publish succeeds without sending
// anything. With publishToEndpoint the wrapper passes the SDK's own
// DefaultContextEventHandler as the delegate, so the publish goes through the
// SDK's real HTTP client (PUT {endpoint}/context with the SDK's own headers).
// Either way, /publishFail arms a one-shot failure of the next publish.
public class CustomPublisher implements ContextEventHandler {
    private final EventCollector eventCollector;
    private final ContextEventHandler delegate;
    private volatile boolean shouldFail;

    public CustomPublisher(EventCollector eventCollector) {
        this(eventCollector, null);
    }

    public CustomPublisher(EventCollector eventCollector, ContextEventHandler delegate) {
        this.eventCollector = eventCollector;
        this.delegate = delegate;
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
        if (delegate != null) {
            return delegate.publish(context, event);
        }
        return CompletableFuture.completedFuture(null);
    }
}
