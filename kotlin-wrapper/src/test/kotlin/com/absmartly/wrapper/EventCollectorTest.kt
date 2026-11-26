package com.absmartly.wrapper

import com.absmartly.sdk.ABSmartlyConfig
import com.absmartly.sdk.ABsmartly
import com.absmartly.sdk.Context
import com.absmartly.sdk.ContextConfig
import com.absmartly.sdk.ContextDataProvider
import com.absmartly.sdk.ContextEventLogger
import com.absmartly.sdk.ContextData
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class EventCollectorTest {
    // Pauses inside the SDK's Ready callback, after the context already reports ready, so the window
    // between kotlin-sdk's setData (ready_ = true) and logEvent(Ready) is open on every run, not by chance.
    private class PausedLogger(private val collector: EventCollector) : ContextEventLogger {
        val inReady = CountDownLatch(1)
        val release = CountDownLatch(1)

        override fun handleEvent(context: Context, type: ContextEventLogger.EventType, data: Any?) {
            if (type == ContextEventLogger.EventType.Ready) {
                inReady.countDown()
                release.await(5, TimeUnit.SECONDS)
            }
            collector.handleEvent(context, type, data)
        }
    }

    private fun createFetched(logger: ContextEventLogger, data: CompletableFuture<ContextData>): Context {
        val provider = object : ContextDataProvider {
            override fun getContextData(): CompletableFuture<ContextData> = data
        }
        // A Client is mandatory; the provider supplies the data, so it never fetches.
        val client = com.absmartly.sdk.Client.create(
            com.absmartly.sdk.ClientConfig.create().setEndpoint("http://localhost").setAPIKey("test")
                .setApplication("test-app").setEnvironment("test-env"))
        val sdk = ABsmartly.create(ABSmartlyConfig.create().setClient(client).setContextDataProvider(provider))
        sdks.add(sdk)
        return sdk.createContext(ContextConfig.create().setUnits(mapOf("session_id" to "u")).setEventLogger(logger))
    }

    private val sdks = mutableListOf<ABsmartly>()

    @kotlin.test.AfterTest
    fun closeSdks() {
        sdks.forEach { it.close() }
    }

    @Test
    fun `a context can be ready before its ready event is logged`() {
        val collector = EventCollector()
        val logger = PausedLogger(collector)
        val data = CompletableFuture<ContextData>()
        val context = createFetched(logger, data)
        val pool = Executors.newSingleThreadExecutor()
        try {
            pool.submit { data.complete(ContextData()) }
            assertTrue(logger.inReady.await(5, TimeUnit.SECONDS))
            context.waitUntilReady()
            assertTrue(context.isReady)
            assertEquals(emptyList(), collector.getEvents(), "ready, yet no ready event: the race the wait closes")
            logger.release.countDown()
        } finally {
            pool.shutdownNow()
        }
    }

    @Test
    fun `awaitCreationEvent returns only once the ready event is collected`() {
        val collector = EventCollector()
        val logger = PausedLogger(collector)
        val data = CompletableFuture<ContextData>()
        val context = createFetched(logger, data)
        val pool = Executors.newFixedThreadPool(2)
        try {
            pool.submit { data.complete(ContextData()) }
            assertTrue(logger.inReady.await(5, TimeUnit.SECONDS))
            context.waitUntilReady()
            val answered = CompletableFuture<List<Map<String, Any?>>>()
            val waiter = AtomicReference<Thread>()
            pool.submit {
                waiter.set(Thread.currentThread())
                collector.awaitCreationEvent(30_000)
                answered.complete(collector.getEvents())
            }
            val parkedBy = System.nanoTime() + TimeUnit.SECONDS.toNanos(5)
            while (waiter.get()?.state != Thread.State.TIMED_WAITING && System.nanoTime() < parkedBy) Thread.onSpinWait()
            assertEquals(Thread.State.TIMED_WAITING, waiter.get()?.state)
            assertTrue(!answered.isDone, "must not answer while the ready event is still pending")
            val released = System.nanoTime()
            logger.release.countDown()
            val events = answered.get(5, TimeUnit.SECONDS)
            assertEquals(listOf("ready"), events.map { it["type"] })
            // Woken by the event, not by its own 30s timeout.
            assertTrue(System.nanoTime() - released < TimeUnit.SECONDS.toNanos(5))
        } finally {
            pool.shutdownNow()
        }
    }

    @Test
    fun `awaitCreationEvent returns on the error event of a failed load`() {
        val collector = EventCollector()
        val data = CompletableFuture<ContextData>()
        val context = createFetched(collector, data)
        data.completeExceptionally(RuntimeException("load failed"))
        context.waitUntilReady()
        val started = System.nanoTime()
        collector.awaitCreationEvent(5000)
        assertTrue(System.nanoTime() - started < TimeUnit.SECONDS.toNanos(1))
        assertEquals(listOf("error"), collector.getEvents().map { it["type"] })
    }

    @Test
    fun `awaitCreationEvent synthesizes nothing when no event arrives`() {
        val collector = EventCollector()
        val started = System.nanoTime()
        collector.awaitCreationEvent(50)
        assertTrue(System.nanoTime() - started >= TimeUnit.MILLISECONDS.toNanos(50))
        assertEquals(emptyList(), collector.getEvents())
    }
}
