package com.absmartly.wrapper;

import com.absmartly.sdk.ABsmartlyClient;
import com.absmartly.sdk.Context;
import com.absmartly.sdk.ContextData;
import com.absmartly.sdk.ContextOptions;
import com.absmartly.sdk.event.EventListener;
import com.absmartly.sdk.internal.Json;

import java.net.URI;
import java.time.Duration;
import java.util.Map;

/** Shared helpers for the behavioral self-tests: an in-memory client and a context over a fixture payload. */
final class SdkSupport {
    private SdkSupport() {}

    /** A client whose contexts never leave the process; each self-test closes it with its context. */
    static ABsmartlyClient inMemoryClient() {
        return ABsmartlyClient.builder()
            .endpoint(URI.create("http://localhost/v1"))
            .apiKey("self-test")
            .application("website")
            .environment("self-test")
            .transport(WrapperTransport.inMemory())
            .build();
    }

    /** A ready context over a fixture built with the SDK's wire model (com.absmartly.sdk.internal.json). */
    static Context context(ABsmartlyClient client, Object payload, Map<String, ?> units, EventListener listener) {
        return context(client, ContextData.fromJson(Json.encode(payload)), units, listener);
    }

    static Context context(ABsmartlyClient client, ContextData data, Map<String, ?> units, EventListener listener) {
        ContextOptions.Builder options = ContextOptions.builder()
            .units(units)
            .publishDelay(Duration.ofMillis(-1));
        if (listener != null) {
            options.eventListener(listener);
        }
        return client.context(options.build(), data);
    }

}
