package com.absmartly.wrapper

import com.absmartly.sdk.Context
import com.absmartly.sdk.ContextData
import com.absmartly.sdk.ContextEventLogger
import com.absmartly.sdk.ContextOptions
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import io.ktor.http.*
import io.ktor.serialization.jackson.*
import io.ktor.server.application.*
import io.ktor.server.engine.*
import io.ktor.server.netty.*
import io.ktor.server.plugins.contentnegotiation.*
import io.ktor.server.request.*
import io.ktor.server.response.*
import io.ktor.server.routing.*
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.Base64
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

fun main() {
    val port = System.getenv("PORT")?.toIntOrNull() ?: 3000
    embeddedServer(Netty, port = port, host = "0.0.0.0") {
        configureRouting()
    }.start(wait = true)
}

fun Application.configureRouting() {
    install(ContentNegotiation) {
        jackson {}
    }

    val contexts = ConcurrentHashMap<String, ContextWrapper>()
    val payloadStore = ConcurrentHashMap<String, ContextData>()
    val objectMapper = jacksonObjectMapper()
    fun normalizeError(message: String?, unitType: String? = null): String {
        val msg = message ?: "Unknown error"
        if (msg.contains("closed", ignoreCase = true) ||
            msg.contains("closing", ignoreCase = true) ||
            msg.contains("finalized", ignoreCase = true)) {
            return "Context finalized"
        }
        if (msg.contains("already set", ignoreCase = true) && unitType != null) {
            return "Unit '$unitType' UID already set."
        }
        return msg
    }

    routing {
        get("/health") {
            call.respond(mapOf(
                "status" to "healthy",
                "sdk" to "kotlin",
                "version" to "1.0.0"
            ))
        }

        get("/capabilities") {
            call.respond(mapOf("diagnostics" to true
            ))
        }

        post("/diagnostic") {
            try {
                val request = call.receive<Map<String, Any?>>()
                val op = request["operation"] as? String
                val value = request["value"]
                val text = value?.toString() ?: ""

                val result: Any = when (op) {
                    "hashUnit" -> {
                        val md5 = MessageDigest.getInstance("MD5").digest(text.toByteArray(StandardCharsets.UTF_8))
                        Base64.getUrlEncoder().withoutPadding().encodeToString(md5)
                    }
                    "base64UrlNoPadding" ->
                        Base64.getUrlEncoder().withoutPadding().encodeToString(text.toByteArray(StandardCharsets.UTF_8))
                    "utf8Bytes" -> text.toByteArray(StandardCharsets.UTF_8).map { it.toInt() and 0xff }
                    "isObject" -> value is Map<*, *>
                    "isNumeric" -> value is Number
                    "isPromise" -> false
                    else -> return@post call.respond(
                        HttpStatusCode.BadRequest,
                        mapOf("error" to "Unsupported diagnostic operation: $op"),
                    )
                }

                call.respond(mapOf("result" to result, "events" to emptyList<Any>()))
            } catch (e: Exception) {
                call.respond(
                    HttpStatusCode.InternalServerError,
                    mapOf("error" to (e.message ?: "Unknown error")),
                )
            }
        }

        put("/context_payload/{payloadId}") {
            val payloadId = call.parameters["payloadId"]!!
            val request = call.receive<Map<String, Any>>()
            val data = objectMapper.convertValue(request["data"], ContextData::class.java)
            payloadStore[payloadId] = data
            call.respond(mapOf("success" to true))
        }

        get("/context_payload/{payloadId}") {
            val payloadId = call.parameters["payloadId"]!!
            val data = payloadStore[payloadId] ?: ContextData()
            call.respond(data)
        }

        get("/context_payload/{payloadId}/context") {
            val payloadId = call.parameters["payloadId"]!!
            val data = payloadStore[payloadId] ?: ContextData()
            call.respond(data)
        }

        post("/context") {
            try {
                val request = call.receive<Map<String, Any>>()
                val contextId = "ctx-${System.currentTimeMillis()}-${UUID.randomUUID()}"

                val eventCollector = EventCollector()

                @Suppress("UNCHECKED_CAST")
                val units = request["units"] as? Map<String, Any> ?: emptyMap()
                @Suppress("UNCHECKED_CAST")
                val options = request["options"] as? Map<String, Any> ?: emptyMap()

                val publishDelay = (options["publishDelay"] as? Number)?.toLong() ?: -1L
                val refreshPeriod = (options["refreshPeriod"] as? Number)?.toLong() ?: 0L
                val contextOptions = ContextOptions(publishDelay = publishDelay, refreshPeriod = refreshPeriod)

                val unitMap = mutableMapOf<String, String>()
                for ((key, value) in units) {
                    unitMap[key] = value.toString()
                }

                val payloadThrottle = (options["payloadThrottle"] as? Number)?.toLong() ?: 0L
                val isAsync = request.containsKey("endpoint") && !request.containsKey("data")
                val deferReady = isAsync && payloadThrottle > 0

                val contextData = if (request.containsKey("data")) {
                    objectMapper.convertValue(request["data"], ContextData::class.java)
                } else if (isAsync && !deferReady) {
                    val endpoint = request["endpoint"] as String
                    val payloadIdMatch = Regex("context_payload/([^/]+)").find(endpoint)
                    if (payloadIdMatch != null) {
                        payloadStore[payloadIdMatch.groupValues[1]] ?: ContextData()
                    } else {
                        ContextData()
                    }
                } else {
                    ContextData()
                }

                val context = if (deferReady) {
                    Context(contextData, unitMap, contextOptions, eventCollector, startReady = false)
                } else {
                    Context(contextData, unitMap, contextOptions, eventCollector)
                }

                if (!deferReady) {
                    eventCollector.handleEvent(context, ContextEventLogger.EventType.Ready, contextData)
                }

                if (deferReady) {
                    val endpoint = request["endpoint"] as String
                    Thread {
                        try {
                            Thread.sleep(payloadThrottle)
                            val payloadIdMatch = Regex("context_payload/([^/]+)").find(endpoint)
                            val asyncData = if (payloadIdMatch != null) {
                                payloadStore[payloadIdMatch.groupValues[1]] ?: ContextData()
                            } else {
                                ContextData()
                            }
                            context.setDataAndReady(asyncData)
                        } catch (_: Exception) {}
                    }.start()
                }

                val wrapper = ContextWrapper(context, eventCollector)
                contexts[contextId] = wrapper

                call.respond(mapOf(
                    "result" to mapOf(
                        "contextId" to contextId,
                        "ready" to context.isReady,
                        "failed" to context.isFailed,
                        "finalized" to context.isClosed
                    ),
                    "events" to eventCollector.getEvents()
                ))
            } catch (e: Exception) {
                e.printStackTrace()
                call.respond(HttpStatusCode.InternalServerError, mapOf(
                    "error" to (e.message ?: "Unknown error"),
                    "type" to e.javaClass.simpleName
                ))
            }
        }

        delete("/context/{contextId}") {
            val contextId = call.parameters["contextId"]!!
            contexts.remove(contextId)
            call.respond(mapOf("result" to "deleted"))
        }

        post("/context/{contextId}/setUnit") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val request = call.receive<Map<String, Any>>()
            val unitType = request["unitType"] as? String
            try {
                val eventsBefore = wrapper.eventCollector.getEvents().size
                wrapper.context.setUnit(
                    unitType ?: "",
                    request["uid"].toString()
                )
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to null, "events" to newEvents))
            } catch (e: Exception) {
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to normalizeError(e.message, unitType)))
            }
        }

        post("/context/{contextId}/getUnit") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val request = call.receive<Map<String, Any>>()
                val eventsBefore = wrapper.eventCollector.getEvents().size
                val unitValue = wrapper.context.getUnit(request["unitType"] as String)

                val result: Any? = if (unitValue != null) {
                    try { unitValue.toInt() }
                    catch (e: NumberFormatException) {
                        try { unitValue.toLong() }
                        catch (e2: NumberFormatException) {
                            try { unitValue.toDouble() }
                            catch (e3: NumberFormatException) { unitValue }
                        }
                    }
                } else null

                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/attribute") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val request = call.receive<Map<String, Any?>>()
                val eventsBefore = wrapper.eventCollector.getEvents().size
                wrapper.context.setAttribute(request["name"] as String, request["value"])
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to null, "events" to newEvents))
            } catch (e: Exception) {
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/getAttribute") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val request = call.receive<Map<String, Any>>()
                val eventsBefore = wrapper.eventCollector.getEvents().size
                val result = wrapper.context.getAttribute(request["name"] as String)
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/treatment") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val request = call.receive<Map<String, Any>>()
                val eventsBefore = wrapper.eventCollector.getEvents().size
                val variant = wrapper.context.getTreatment(request["experimentName"] as String)
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to variant, "events" to newEvents))
            } catch (e: Exception) {
                e.printStackTrace()
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/peek") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val request = call.receive<Map<String, Any>>()
                val eventsBefore = wrapper.eventCollector.getEvents().size
                val result = wrapper.context.peekTreatment(request["experimentName"] as String)
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/variableValue") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val request = call.receive<Map<String, Any?>>()
                val eventsBefore = wrapper.eventCollector.getEvents().size
                val result = wrapper.context.getVariableValue(request["key"] as String, request["defaultValue"])
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                e.printStackTrace()
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/peekVariableValue") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val request = call.receive<Map<String, Any?>>()
                val eventsBefore = wrapper.eventCollector.getEvents().size
                val result = wrapper.context.peekVariableValue(request["key"] as String, request["defaultValue"])
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                e.printStackTrace()
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/track") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val request = call.receive<Map<String, Any?>>()
                val goalName = request["goalName"] as String
                val propertiesObj = request["properties"]

                if (propertiesObj != null && propertiesObj !is Map<*, *>) {
                    call.respond(HttpStatusCode.BadRequest, mapOf(
                        "error" to "Goal '$goalName' properties must be of type object."
                    ))
                    return@post
                }

                @Suppress("UNCHECKED_CAST")
                val properties = propertiesObj as? Map<String, Any?>

                val eventsBefore = wrapper.eventCollector.getEvents().size
                wrapper.context.track(goalName, properties)
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to null, "events" to newEvents))
            } catch (e: Exception) {
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/override") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val request = call.receive<Map<String, Any>>()
                wrapper.context.setOverride(
                    request["experimentName"] as String,
                    (request["variant"] as Number).toInt()
                )
                call.respond(mapOf("result" to null, "events" to emptyList<Any>()))
            } catch (e: Exception) {
                if (normalizeError(e.message) == "Context finalized") {
                    call.respond(mapOf("result" to null, "events" to emptyList<Any>()))
                    return@post
                }
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to normalizeError(e.message)))
            }
        }

        post("/context/{contextId}/setOverride") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val request = call.receive<Map<String, Any>>()
                val eventsBefore = wrapper.eventCollector.getEvents().size
                wrapper.context.setOverride(
                    request["experimentName"] as String,
                    (request["variant"] as Number).toInt()
                )
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to null, "events" to newEvents))
            } catch (e: Exception) {
                if (normalizeError(e.message) == "Context finalized") {
                    call.respond(mapOf("result" to null, "events" to emptyList<Any>()))
                    return@post
                }
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to normalizeError(e.message)))
            }
        }

        post("/context/{contextId}/customAssignment") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val request = call.receive<Map<String, Any>>()
                wrapper.context.setCustomAssignment(
                    request["experimentName"] as String,
                    (request["variant"] as Number).toInt()
                )
                call.respond(mapOf("result" to null, "events" to emptyList<Any>()))
            } catch (e: Exception) {
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/setCustomAssignment") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val request = call.receive<Map<String, Any>>()
                val eventsBefore = wrapper.eventCollector.getEvents().size
                wrapper.context.setCustomAssignment(
                    request["experimentName"] as String,
                    (request["variant"] as Number).toInt()
                )
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to null, "events" to newEvents))
            } catch (e: Exception) {
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/customFieldValue") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val request = call.receive<Map<String, Any>>()
                val eventsBefore = wrapper.eventCollector.getEvents().size
                val result = wrapper.context.getCustomFieldValue(
                    request["experimentName"] as String,
                    request["fieldName"] as String
                )
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/variableKeys") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val eventsBefore = wrapper.eventCollector.getEvents().size
                val keys = wrapper.context.variableKeys
                val result = keys.keys.toList()
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/customFieldKeys") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val request = call.receive<Map<String, Any>>()
                val eventsBefore = wrapper.eventCollector.getEvents().size
                val keys = wrapper.context.customFieldKeys
                val result = keys.toList()
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/customFieldValueType") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val request = call.receive<Map<String, Any>>()
                val eventsBefore = wrapper.eventCollector.getEvents().size
                val result = wrapper.context.getCustomFieldValueType(
                    request["experimentName"] as String,
                    request["fieldName"] as String
                )
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        get("/context/{contextId}/pending") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@get call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val result = try { wrapper.context.pendingCount } catch (e: Exception) { 0 }
            call.respond(mapOf("result" to result, "events" to emptyList<Any>()))
        }

        get("/context/{contextId}/isFinalized") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@get call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            call.respond(mapOf("result" to wrapper.context.isClosed, "events" to emptyList<Any>()))
        }

        get("/context/{contextId}/isReady") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@get call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            call.respond(mapOf("result" to wrapper.context.isReady, "events" to emptyList<Any>()))
        }

        get("/context/{contextId}/isFailed") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@get call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            call.respond(mapOf("result" to wrapper.context.isFailed, "events" to emptyList<Any>()))
        }

        get("/context/{contextId}/experiments") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@get call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val result = wrapper.context.experiments
                call.respond(mapOf("result" to result, "events" to emptyList<Any>()))
            } catch (e: Exception) {
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/publish") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val eventsBefore = wrapper.eventCollector.getEvents().size
                wrapper.context.publish()
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to null, "events" to newEvents))
            } catch (e: Exception) {
                e.printStackTrace()
                call.respond(HttpStatusCode.InternalServerError, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/refresh") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val request = call.receive<Map<String, Any>>()
                val eventsBefore = wrapper.eventCollector.getEvents().size
                val newData = objectMapper.convertValue(request["newData"], ContextData::class.java)
                wrapper.context.refresh(newData)
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to null, "events" to newEvents))
            } catch (e: Exception) {
                e.printStackTrace()
                call.respond(HttpStatusCode.InternalServerError, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/finalize") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val eventsBefore = wrapper.eventCollector.getEvents().size
                wrapper.context.close()
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to null, "events" to newEvents))
            } catch (e: Exception) {
                e.printStackTrace()
                call.respond(HttpStatusCode.InternalServerError, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }
    }
}
