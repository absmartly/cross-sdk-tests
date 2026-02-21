package com.absmartly.wrapper

import com.absmartly.sdk.Context
import com.absmartly.sdk.ContextEventLogger
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import java.util.Collections

class EventCollector : ContextEventLogger {
    private val events = Collections.synchronizedList(mutableListOf<Map<String, Any?>>())
    private val objectMapper = jacksonObjectMapper()

    override fun handleEvent(context: Context, type: ContextEventLogger.EventType, data: Any?) {
        val event = mapOf(
            "type" to mapEventType(type),
            "data" to deepCopy(data),
            "timestamp" to System.currentTimeMillis()
        )
        events.add(event)
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

    private fun deepCopy(data: Any?): Any? {
        if (data == null) return null
        return try {
            val json = objectMapper.writeValueAsString(data)
            objectMapper.readValue(json, Any::class.java)
        } catch (e: Exception) {
            data
        }
    }

    fun getEvents(): List<Map<String, Any?>> {
        synchronized(events) {
            return events.toList()
        }
    }

    fun getNewEvents(since: Int): List<Map<String, Any?>> {
        synchronized(events) {
            if (since >= events.size) {
                return emptyList()
            }
            return events.subList(since, events.size).toList()
        }
    }
}
