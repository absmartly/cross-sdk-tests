package com.absmartly.wrapper;

import com.absmartly.sdk.JdkHttpTransport;
import com.absmartly.sdk.Transport;

import java.nio.charset.StandardCharsets;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * The {@link Transport} behind every wrapper context. Fetches (GET /context) and publishes (PUT /context) are
 * routed separately: in memory, a fetch answers with the next payload queued by refresh(newData), or an empty
 * experiment list, and a publish answers 200, so the SDK's real client, JSON decoding and publish path run
 * without a server; otherwise they go through the SDK's default {@link JdkHttpTransport} to the configured
 * endpoint. /publishFail arms a one-shot 500 "Publish failed" for the next publish; failLoad fails every fetch;
 * throttleMs delays every fetch.
 */
final class WrapperTransport implements Transport {
    static final String EMPTY_CONTEXT = "{\"experiments\":[]}";

    // The one HTTP transport both routes share, or null when neither goes over HTTP; closed once.
    private final Transport http;
    private final Transport fetchDelegate;
    private final Transport publishDelegate;
    private final boolean failLoad;
    private final long throttleMs;
    private final ConcurrentLinkedQueue<String> nextPayloads = new ConcurrentLinkedQueue<>();
    private final AtomicBoolean failNextPublish = new AtomicBoolean();

    /**
     * @param fetchOverHttp  fetch from the endpoint instead of from memory
     * @param publishOverHttp publish to the endpoint instead of answering 200 in memory
     */
    WrapperTransport(boolean fetchOverHttp, boolean publishOverHttp, boolean failLoad, long throttleMs) {
        this.http = (fetchOverHttp || publishOverHttp) ? JdkHttpTransport.create() : null;
        this.fetchDelegate = fetchOverHttp ? http : null;
        this.publishDelegate = publishOverHttp ? http : null;
        this.failLoad = failLoad;
        this.throttleMs = throttleMs;
    }

    static WrapperTransport inMemory() {
        return new WrapperTransport(false, false, false, 0);
    }

    boolean fetchesFromMemory() {
        return fetchDelegate == null;
    }

    void queuePayload(String json) {
        nextPayloads.add(json);
    }

    void failNextPublish(boolean fail) {
        failNextPublish.set(fail);
    }

    @Override
    public CompletableFuture<Response> send(Request request) {
        if ("GET".equals(request.method())) {
            if (failLoad) {
                return CompletableFuture.failedFuture(new RuntimeException("Context load failed"));
            }
            CompletableFuture<Void> delay = (throttleMs > 0)
                ? CompletableFuture.runAsync(() -> {}, CompletableFuture.delayedExecutor(throttleMs, TimeUnit.MILLISECONDS))
                : CompletableFuture.completedFuture(null);
            return delay.thenCompose(ignored -> fetch(request));
        }
        if (failNextPublish.compareAndSet(true, false)) {
            return CompletableFuture.completedFuture(new Response(500, "Publish failed", null));
        }
        if (publishDelegate == null) {
            return CompletableFuture.completedFuture(new Response(200, "OK", new byte[0]));
        }
        return publishDelegate.send(request);
    }

    private CompletableFuture<Response> fetch(Request request) {
        if (fetchDelegate != null) {
            return fetchDelegate.send(request);
        }
        String next = nextPayloads.poll();
        return CompletableFuture.completedFuture(new Response(200, "OK",
            (next != null ? next : EMPTY_CONTEXT).getBytes(StandardCharsets.UTF_8)));
    }

    @Override
    public void close() {
        if (http != null) {
            http.close();
        }
    }
}
