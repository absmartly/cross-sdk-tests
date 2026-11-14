package com.absmartly.wrapper

import com.absmartly.sdk.Context
import com.absmartly.sdk.ContextPublisher
import com.absmartly.sdk.PublishEvent
import java.util.concurrent.CompletableFuture

// Test contexts publish nowhere, so a publish succeeds without sending
// anything. With publishToEndpoint the wrapper passes the SDK's own
// DefaultContextPublisher as the delegate, so the publish goes through the
// SDK's real HTTP client (PUT {endpoint}/context with the SDK's own headers).
// Either way, /publishFail arms a one-shot failure of the next publish.
class CustomPublisher(private val delegate: ContextPublisher? = null) : ContextPublisher {
    @Volatile
    private var shouldFail: Boolean = false

    fun setShouldFail(shouldFail: Boolean) {
        this.shouldFail = shouldFail
    }

    override fun publish(context: Context, event: PublishEvent): CompletableFuture<Void> {
        if (shouldFail) {
            shouldFail = false
            val future = CompletableFuture<Void>()
            future.completeExceptionally(RuntimeException("Publish failed"))
            return future
        }
        if (delegate != null) {
            return delegate.publish(context, event)
        }
        return CompletableFuture.completedFuture(null)
    }
}
