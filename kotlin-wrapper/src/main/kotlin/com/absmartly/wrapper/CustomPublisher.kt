package com.absmartly.wrapper

import com.absmartly.sdk.Context
import com.absmartly.sdk.ContextPublisher
import com.absmartly.sdk.PublishEvent
import java.util.concurrent.CompletableFuture

class CustomPublisher : ContextPublisher {
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
        return CompletableFuture.completedFuture(null)
    }
}
