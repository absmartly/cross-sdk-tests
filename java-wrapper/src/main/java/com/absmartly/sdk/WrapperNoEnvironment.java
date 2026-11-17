package com.absmartly.sdk;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ScheduledExecutorService;

import com.absmartly.sdk.internal.Clock;
import com.absmartly.sdk.internal.json.ContextPayload;

/**
 * Builds a context whose client reports no environment, for the cross-SDK "no environment configured" case
 * (capability `clientNoEnvironment`, scenarios 253 and 302).
 *
 * <p>{@code ABsmartlyClient.builder()} rejects a missing environment, exactly as the SDK intends for a real
 * client, so the public API cannot express "application set, environment absent". This adapter constructs the
 * SDK's own client with a placeholder environment (satisfying its validation) that reports the environment as
 * absent, and creates the context with the SDK's own context constructor, so {@code Context} leaves
 * {@code environment} out of the audience/rule evaluation map: the effect the SDK's unit tests obtain the same way.
 * The context fetches and publishes through that client and the given transport, exactly as a built client's
 * context would. It lives in {@code com.absmartly.sdk} only because those constructors are package-private
 * (Spring Boot loads the wrapper and core-api with one class loader, so they share the runtime package); it uses
 * no reflection.
 */
public final class WrapperNoEnvironment {
    private WrapperNoEnvironment() {}

    /** A context over data the caller already has (ready at once). */
    public static Context context(String endpoint, String application, Transport transport,
            ScheduledExecutorService scheduler, ContextOptions options, ContextData data) {
        return create(endpoint, application, transport, scheduler, options, data);
    }

    /** A context that fetches its data through the client's GET /context, as {@code newContext} does. */
    public static Context newContext(String endpoint, String application, Transport transport,
            ScheduledExecutorService scheduler, ContextOptions options) {
        return create(endpoint, application, transport, scheduler, options, null);
    }

    private static Context create(String endpoint, String application, Transport transport,
            ScheduledExecutorService scheduler, ContextOptions options, ContextData data) {
        Client client = new Client(endpoint, "test-api-key", application, "placeholder", null, transport, null) {
            @Override
            String getEnvironment() {
                return null;
            }
        };
        CompletableFuture<ContextPayload> payload = (data != null)
            ? CompletableFuture.completedFuture(data.payload())
            : client.getContextData();
        return new Context(Clock.systemUTC(), options, scheduler, payload, client, client, null,
            new DefaultVariableParser(), new AudienceMatcher(new DefaultAudienceDeserializer()), client, null);
    }
}
