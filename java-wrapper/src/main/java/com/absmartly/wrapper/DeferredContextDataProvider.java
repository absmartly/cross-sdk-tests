package com.absmartly.wrapper;

import com.absmartly.sdk.ContextDataProvider;
import com.absmartly.sdk.json.ContextData;
import java8.util.concurrent.CompletableFuture;

import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * Delays the SDK's own context-data fetch by {@code throttleMs}. The fetch and
 * deserialization are delegated to the wrapped SDK provider (normally the
 * SDK's DefaultContextDataProvider over a real Client), so a throttled context
 * exercises the same HTTP + parse path as a non-throttled one.
 */
public class DeferredContextDataProvider implements ContextDataProvider {
    private final ContextDataProvider delegate;
    private final int throttleMs;

    public DeferredContextDataProvider(ContextDataProvider delegate, int throttleMs) {
        this.delegate = delegate;
        this.throttleMs = throttleMs;
    }

    @Override
    public CompletableFuture<ContextData> getContextData() {
        final CompletableFuture<ContextData> future = new CompletableFuture<>();
        final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor();
        scheduler.schedule(() -> {
            try {
                delegate.getContextData()
                    .thenAccept(data -> future.complete(data))
                    .exceptionally(error -> {
                        future.completeExceptionally(error);
                        return null;
                    });
            } catch (Throwable error) {
                future.completeExceptionally(error);
            } finally {
                scheduler.shutdown();
            }
        }, throttleMs, TimeUnit.MILLISECONDS);
        return future;
    }
}
