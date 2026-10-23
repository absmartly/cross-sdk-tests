package com.absmartly.wrapper

import com.absmartly.sdk.ContextData
import com.absmartly.sdk.ContextDataProvider
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.ScheduledThreadPoolExecutor
import java.util.concurrent.TimeUnit

// Delays each fetch by `delayMs`, then delegates to the SDK's own provider so the
// HTTP request and deserialization stay SDK-real (options.payloadThrottle).
class ThrottledContextDataProvider(
    private val delegate: ContextDataProvider,
    private val delayMs: Long
) : ContextDataProvider {
    private val scheduled = ConcurrentHashMap.newKeySet<ScheduledFuture<*>>()

    override fun getContextData(): CompletableFuture<ContextData> {
        val result = CompletableFuture<ContextData>()
        lateinit var task: ScheduledFuture<*>
        task = scheduler.schedule({
            scheduled.remove(task)
            delegate.getContextData().whenComplete { data, error ->
                if (error != null) result.completeExceptionally(error) else result.complete(data)
            }
        }, delayMs, TimeUnit.MILLISECONDS)
        scheduled.add(task)
        return result
    }

    fun cancel() {
        for (task in scheduled) task.cancel(false)
        scheduled.clear()
    }

    companion object {
        private val scheduler = ScheduledThreadPoolExecutor(1) { r ->
            Thread(r, "payload-throttle").apply { isDaemon = true }
        }.apply { removeOnCancelPolicy = true }
    }
}
