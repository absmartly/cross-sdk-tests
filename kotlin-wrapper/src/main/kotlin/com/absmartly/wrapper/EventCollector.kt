package com.absmartly.wrapper

import com.absmartly.sdk.Context
import com.absmartly.sdk.ContextEventLogger
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper

class EventCollector : ContextEventLogger {
    private val lock = Object()
    private val events = mutableListOf<Map<String, Any?>>()
    private val objectMapper = jacksonObjectMapper()

    override fun handleEvent(context: Context, type: ContextEventLogger.EventType, data: Any?) {
        val event = mapOf(
            "type" to mapEventType(type),
            "data" to deepCopy(type, data),
            "timestamp" to System.currentTimeMillis()
        )
        synchronized(lock) {
            events.add(event)
            lock.notifyAll()
        }
    }

    private fun mapEventType(type: ContextEventLogger.EventType): String {
        return when (type) {
            ContextEventLogger.EventType.Ready -> "ready"
            ContextEventLogger.EventType.Refresh -> "refresh"
            ContextEventLogger.EventType.Publish -> "publish"
            ContextEventLogger.EventType.Exposure -> "exposure"
            ContextEventLogger.EventType.Goal -> "goal"
            ContextEventLogger.EventType.Close -> "finalize"
            ContextEventLogger.EventType.Error -> "error"
        }
    }

    // The SDK swallows exceptions thrown by an event logger (Context.logEvent),
    // so throwing here would silently drop the event. Keep the original object
    // instead: the route's response serialization then fails with the same
    // error and the operation returns an HTTP error, and stderr records why.
    private fun deepCopy(type: ContextEventLogger.EventType, data: Any?): Any? {
        if (data == null) return null
        return try {
            val json = objectMapper.writeValueAsString(data)
            objectMapper.readValue(json, Any::class.java)
        } catch (e: Exception) {
            System.err.println("EventCollector: cannot serialize $type event data (${data.javaClass.name}): ${e.message}")
            data
        }
    }

    fun getEvents(): List<Map<String, Any?>> {
        synchronized(lock) {
            return events.toList()
        }
    }

    fun getNewEvents(since: Int): List<Map<String, Any?>> {
        synchronized(lock) {
            if (since >= events.size) {
                return emptyList()
            }
            return events.subList(since, events.size).toList()
        }
    }

    // The SDK completes its ready future before it logs the Ready event, so a
    // caller returning from waitUntilReady() can race the event. Block until the
    // SDK has logged at least `count` events, or the timeout elapses.
    fun awaitEventCount(count: Int, timeoutMs: Long): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        synchronized(lock) {
            while (events.size < count) {
                val remaining = deadline - System.currentTimeMillis()
                if (remaining <= 0) return false
                lock.wait(remaining)
            }
            return true
        }
    }
}
