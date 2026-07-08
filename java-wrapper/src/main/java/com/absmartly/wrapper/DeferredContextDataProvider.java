package com.absmartly.wrapper;

import com.absmartly.sdk.ContextDataProvider;
import com.absmartly.sdk.json.ContextData;
import java8.util.concurrent.CompletableFuture;

import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import com.fasterxml.jackson.databind.ObjectMapper;

public class DeferredContextDataProvider implements ContextDataProvider {
    private final String endpoint;
    private final int throttleMs;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public DeferredContextDataProvider(String endpoint, int throttleMs) {
        this.endpoint = endpoint;
        this.throttleMs = throttleMs;
    }

    @Override
    public CompletableFuture<ContextData> getContextData() {
        CompletableFuture<ContextData> future = new CompletableFuture<>();
        ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor();
        scheduler.schedule(() -> {
            try {
                URL url = new URL(endpoint);
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(5000);
                conn.setReadTimeout(5000);
                int status = conn.getResponseCode();
                if (status == 200) {
                    ContextData data = objectMapper.readValue(conn.getInputStream(), ContextData.class);
                    future.complete(data);
                } else {
                    future.completeExceptionally(new IOException("HTTP " + status));
                }
            } catch (Exception e) {
                future.completeExceptionally(e);
            } finally {
                scheduler.shutdown();
            }
        }, throttleMs, TimeUnit.MILLISECONDS);
        return future;
    }
}
