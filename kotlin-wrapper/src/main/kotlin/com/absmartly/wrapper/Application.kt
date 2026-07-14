package com.absmartly.wrapper

import com.absmartly.sdk.ABSmartlyConfig
import com.absmartly.sdk.ABsmartly
import com.absmartly.sdk.ContextConfig
import com.absmartly.sdk.ContextData
import com.absmartly.sdk.ContextEventLogger
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
            call.respond(mapOf("diagnostics" to true,
                "publishFail" to true,
                "variableKeysMap" to true,
                "globalCustomFieldKeys" to true,
                "getUnits" to true,
                "getAttributes" to true,
                "readyError" to true
            ))
        }

        post("/diagnostic") {
            try {
                val request = call.receive<Map<String, Any?>>()
                val op = request["operation"] as? String
                val value = request["value"]
                val text = value?.toString() ?: ""

                val result: Any = when (op) {
                    "hashUnit" -> String(com.absmartly.sdk.Hashing.hashUnit(text), StandardCharsets.US_ASCII)
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

                if (request["mode"] == "e2e") {
                    val e2eEndpoint = System.getenv("ABSMARTLY_E2E_ENDPOINT")
                    val e2eApiKey = System.getenv("ABSMARTLY_E2E_API_KEY")
                    val e2eApplication = System.getenv("ABSMARTLY_E2E_APPLICATION")
                    val e2eEnvironment = System.getenv("ABSMARTLY_E2E_ENVIRONMENT")
                    if (e2eEndpoint == null || e2eApiKey == null || e2eApplication == null || e2eEnvironment == null) {
                        call.respond(HttpStatusCode.NotImplemented, mapOf("error" to "e2e mode not configured"))
                        return@post
                    }

                    @Suppress("UNCHECKED_CAST")
                    val e2eUnits = request["units"] as? Map<String, Any> ?: emptyMap()
                    @Suppress("UNCHECKED_CAST")
                    val e2eAttrs = request["attributes"] as? Map<String, Any>

                    val e2eCollector = EventCollector()
                    val e2eContextConfig = ContextConfig.create()
                        .setPublishDelay(-1)
                        .setRefreshInterval(0)
                    for ((key, value) in e2eUnits) {
                        e2eContextConfig.setUnit(key, value.toString())
                    }

                    val e2eClientConfig = com.absmartly.sdk.ClientConfig.create()
                        .setEndpoint(e2eEndpoint)
                        .setAPIKey(e2eApiKey)
                        .setApplication(e2eApplication)
                        .setEnvironment(e2eEnvironment)
                    val e2eClient = com.absmartly.sdk.Client.create(e2eClientConfig)
                    val e2eSdkConfig = ABSmartlyConfig.create()
                        .setClient(e2eClient)
                        .setContextEventLogger(e2eCollector)
                    val e2eSdk = ABsmartly.create(e2eSdkConfig)
                    val e2eContext = e2eSdk.createContext(e2eContextConfig)

                    if (e2eAttrs != null) {
                        for ((key, value) in e2eAttrs) {
                            e2eContext.setAttribute(key, value)
                        }
                    }

                    e2eContext.waitUntilReady()

                    val e2eContextId = "ctx-${System.currentTimeMillis()}-${UUID.randomUUID()}"
                    contexts[e2eContextId] = ContextWrapper(e2eContext, e2eCollector, null)
                    call.respond(mapOf(
                        "result" to mapOf(
                            "contextId" to e2eContextId,
                            "ready" to e2eContext.isReady,
                            "failed" to e2eContext.isFailed,
                            "finalized" to e2eContext.isClosed
                        ),
                        "events" to e2eCollector.getEvents()
                    ))
                    return@post
                }

                val contextId = "ctx-${System.currentTimeMillis()}-${UUID.randomUUID()}"

                val eventCollector = EventCollector()

                @Suppress("UNCHECKED_CAST")
                val units = request["units"] as? Map<String, Any> ?: emptyMap()
                @Suppress("UNCHECKED_CAST")
                val options = request["options"] as? Map<String, Any> ?: emptyMap()

                val publishDelay = (options["publishDelay"] as? Number)?.toLong() ?: -1L
                val refreshPeriod = (options["refreshPeriod"] as? Number)?.toLong() ?: 0L

                val unitMap = mutableMapOf<String, String>()
                for ((key, value) in units) {
                    unitMap[key] = value.toString()
                }

                val contextConfig = ContextConfig.create()
                    .setUnits(unitMap)
                    .setPublishDelay(publishDelay)
                    .setRefreshInterval(refreshPeriod)
                    .setEventLogger(eventCollector)

                val payloadThrottle = (options["payloadThrottle"] as? Number)?.toLong() ?: 0L
                val isAsync = request.containsKey("endpoint") && !request.containsKey("data")
                val deferReady = isAsync && payloadThrottle > 0

                val failLoad = request["failLoad"] == true
                var loadFailure: RuntimeException? = null

                val contextData = if (request.containsKey("data")) {
                    objectMapper.convertValue(request["data"], ContextData::class.java)
                } else if (isAsync && !deferReady) {
                    val endpoint = request["endpoint"] as String
                    val payloadIdMatch = Regex("context_payload/([^/]+)").find(endpoint)
                    val storedData = if (payloadIdMatch != null) {
                        payloadStore[payloadIdMatch.groupValues[1]]
                    } else {
                        null
                    }
                    if (storedData != null) {
                        storedData
                    } else {
                        loadFailure = RuntimeException("Context load failed")
                        ContextData()
                    }
                } else {
                    ContextData()
                }

                val customPublisher = CustomPublisher()

                val dataProvider: DummyContextDataProvider?
                val context: com.absmartly.sdk.Context

                if (failLoad || loadFailure != null) {
                    dataProvider = null
                    val failure = loadFailure ?: RuntimeException("Context load failed")
                    val failingProvider = object : com.absmartly.sdk.ContextDataProvider {
                        override fun getContextData(): java.util.concurrent.CompletableFuture<ContextData> {
                            val f = java.util.concurrent.CompletableFuture<ContextData>()
                            f.completeExceptionally(failure)
                            return f
                        }
                    }
                    val sdkConfig = ABSmartlyConfig.create()
                        .setContextDataProvider(failingProvider)
                        .setContextPublisher(customPublisher)
                        .setContextEventLogger(eventCollector)
                    val failingSdk = ABsmartly.create(sdkConfig)
                    context = failingSdk.createContext(contextConfig)
                    Thread.sleep(50)
                } else if (deferReady) {
                    dataProvider = null
                    val dataFuture = java.util.concurrent.CompletableFuture<ContextData>()
                    val deferredProvider = object : com.absmartly.sdk.ContextDataProvider {
                        override fun getContextData(): java.util.concurrent.CompletableFuture<ContextData> = dataFuture
                    }
                    val sdkConfig = ABSmartlyConfig.create()
                        .setContextDataProvider(deferredProvider)
                        .setContextPublisher(customPublisher)
                        .setContextEventLogger(eventCollector)
                    val sdk = ABsmartly.create(sdkConfig)
                    context = sdk.createContext(contextConfig)
                    val endpoint = request["endpoint"] as String
                    Thread {
                        try {
                            Thread.sleep(payloadThrottle)
                            val payloadIdMatch = Regex("context_payload/([^/]+)").find(endpoint)
                            val asyncData = if (payloadIdMatch != null) {
                                payloadStore[payloadIdMatch.groupValues[1]]
                            } else {
                                null
                            }
                            if (asyncData != null) {
                                dataFuture.complete(asyncData)
                            } else {
                                dataFuture.completeExceptionally(RuntimeException("Context load failed"))
                            }
                        } catch (e: Exception) {
                            if (e is InterruptedException) {
                                Thread.currentThread().interrupt()
                            }
                            dataFuture.completeExceptionally(e)
                        }
                    }.start()
                } else {
                    dataProvider = DummyContextDataProvider()
                    val sdkConfig = ABSmartlyConfig.create()
                        .setContextDataProvider(dataProvider)
                        .setContextPublisher(customPublisher)
                    val sdk = ABsmartly.create(sdkConfig)
                    context = sdk.createContextWith(contextConfig, contextData)
                }

                val wrapper = ContextWrapper(context, eventCollector, dataProvider, customPublisher)
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
            // The kotlin SDK's getTreatment returns 0 (not an error) after
            // close(); scenario 189 requires an error once finalized.
            if (wrapper.context.isClosed) {
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to "Context finalized"))
                return@post
            }
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

        post("/context/{contextId}/getUnits") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val eventsBefore = wrapper.eventCollector.getEvents().size
                val units = wrapper.context.getUnits()
                val result = mutableMapOf<String, Any?>()
                for ((key, value) in units) {
                    result[key] = try { value.toInt() }
                    catch (e: NumberFormatException) {
                        try { value.toDouble() }
                        catch (e2: NumberFormatException) { value }
                    }
                }
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/getAttributes") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val eventsBefore = wrapper.eventCollector.getEvents().size
                val attrs = wrapper.context.getAttributes()
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to attrs, "events" to newEvents))
            } catch (e: Exception) {
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/readyError") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val error = wrapper.context.readyError()
                val result = error?.let { mapOf("isError" to true, "message" to (it.message ?: it.toString())) }
                call.respond(mapOf("result" to result, "events" to emptyList<Any>()))
            } catch (e: Exception) {
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/variableKeysMap") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val eventsBefore = wrapper.eventCollector.getEvents().size
                val keys = wrapper.context.variableKeys
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to keys, "events" to newEvents))
            } catch (e: Exception) {
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/globalCustomFieldKeys") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val eventsBefore = wrapper.eventCollector.getEvents().size
                val keys = wrapper.context.customFieldKeys
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to keys.toList(), "events" to newEvents))
            } catch (e: Exception) {
                call.respond(HttpStatusCode.BadRequest, mapOf("error" to (e.message ?: "Unknown error")))
            }
        }

        post("/context/{contextId}/publishFail") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            wrapper.publisher?.setShouldFail(true)
            call.respond(mapOf("result" to null, "events" to emptyList<Any>()))
        }

        post("/context/{contextId}/publish") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            try {
                val eventsBefore = wrapper.eventCollector.getEvents().size
                // Await the SDK publish so the collector PUT completes before we
                // respond; otherwise e2e publish races the collector verification.
                wrapper.context.publish().join()
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to null, "events" to newEvents))
            } catch (e: Exception) {
                e.printStackTrace()
                val cause = e.cause ?: e
                call.respond(HttpStatusCode.InternalServerError, mapOf("error" to (cause.message ?: "Unknown error")))
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
                val newData = if (request.containsKey("newData")) {
                    objectMapper.convertValue(request["newData"], ContextData::class.java)
                } else null
                if (wrapper.dataProvider != null && newData != null) {
                    wrapper.dataProvider.setNextData(newData)
                    wrapper.context.refresh().join()
                } else if (newData != null) {
                    wrapper.context.refresh(newData)
                } else {
                    wrapper.context.refresh().join()
                }
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
