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
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.URI
import java.nio.charset.StandardCharsets
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit

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
    val payloadStore = ConcurrentHashMap<String, Any>()
    val objectMapper = jacksonObjectMapper()
    val ownPort = System.getenv("PORT")?.toIntOrNull() ?: 3000

    // The orchestrator addresses the wrapper by its host-mapped port; inside the
    // container that wrapper listens on its own port.
    fun translateEndpoint(endpoint: String): String {
        return try {
            val uri = URI(endpoint)
            if (uri.host == "localhost" || uri.host == "127.0.0.1") {
                URI(uri.scheme, uri.userInfo, "localhost", ownPort, uri.path, uri.query, uri.fragment).toString()
            } else {
                endpoint
            }
        } catch (e: Exception) {
            endpoint
        }
    }
    // Error responses carry the events the SDK logged during the call.
    suspend fun ApplicationCall.respondError(status: HttpStatusCode, e: Throwable, events: List<Any?>) {
        val cause = if (e is java.util.concurrent.CompletionException && e.cause != null) e.cause!! else e
        respond(status, mapOf("error" to (cause.message ?: cause.toString()), "events" to events))
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
                "diagnosticsUtf8" to true,
                "diagnosticsBase64" to true,
                "diagnosticsTypeChecks" to false,
                "diagnosticsIsPromise" to false,
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
                    "hashUnit" -> String(com.absmartly.sdk.Hashing.hashUnit(value ?: ""), StandardCharsets.US_ASCII)
                    "base64UrlNoPadding" ->
                        com.absmartly.sdk.Hashing.base64UrlNoPadding(com.absmartly.sdk.Hashing.utf8Bytes(text))
                    "utf8Bytes" -> com.absmartly.sdk.Hashing.utf8Bytes(text).map { it.toInt() and 0xff }
                    // isObject, isNumeric and isPromise have no kotlin-sdk implementation.
                    else -> return@post call.respond(
                        HttpStatusCode.BadRequest,
                        mapOf("error" to "Unsupported diagnostic operation: $op", "events" to emptyList<Any>()),
                    )
                }

                call.respond(mapOf("result" to result, "events" to emptyList<Any>()))
            } catch (e: Exception) {
                call.respond(
                    HttpStatusCode.InternalServerError,
                    mapOf("error" to (e.message ?: "Unknown error"), "events" to emptyList<Any>()),
                )
            }
        }

        // Payloads are stored as raw JSON so the SDK's own client deserializes
        // them when it fetches GET {endpoint}/context.
        put("/context_payload/{payloadId}") {
            val payloadId = call.parameters["payloadId"]!!
            val request = call.receive<Map<String, Any?>>()
            val data = request["data"] ?: return@put call.respond(
                HttpStatusCode.BadRequest, mapOf("error" to "Missing data")
            )
            payloadStore[payloadId] = data
            call.respond(mapOf("success" to true))
        }

        get("/context_payload/{payloadId}") {
            val payloadId = call.parameters["payloadId"]!!
            val data = payloadStore[payloadId] ?: return@get call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Payload not found")
            )
            call.respond(data)
        }

        get("/context_payload/{payloadId}/context") {
            val payloadId = call.parameters["payloadId"]!!
            val data = payloadStore[payloadId] ?: return@get call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Payload not found")
            )
            call.respond(data)
        }

        post("/context") {
            val eventCollector = EventCollector()
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
                    e2eContextConfig.setUnits(e2eUnits)

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

                @Suppress("UNCHECKED_CAST")
                val units = request["units"] as? Map<String, Any> ?: emptyMap()
                @Suppress("UNCHECKED_CAST")
                val options = request["options"] as? Map<String, Any> ?: emptyMap()

                val publishDelay = (options["publishDelay"] as? Number)?.toLong() ?: -1L
                val refreshPeriod = (options["refreshPeriod"] as? Number)?.toLong() ?: 0L

                val contextConfig = ContextConfig.create()
                    .setUnits(units)
                    .setPublishDelay(publishDelay)
                    .setRefreshInterval(refreshPeriod)
                    .setEventLogger(eventCollector)

                val payloadThrottle = (options["payloadThrottle"] as? Number)?.toLong() ?: 0L
                val endpoint = request["endpoint"] as? String
                val failLoad = request["failLoad"] == true

                val customPublisher = CustomPublisher()

                var dataProvider: DummyContextDataProvider? = null
                var throttledProvider: ThrottledContextDataProvider? = null
                val sdk: ABsmartly
                val context: com.absmartly.sdk.Context

                if (request.containsKey("data")) {
                    val contextData = objectMapper.convertValue(request["data"], ContextData::class.java)
                    dataProvider = DummyContextDataProvider()
                    val sdkConfig = ABSmartlyConfig.create()
                        .setContextDataProvider(dataProvider)
                        .setContextPublisher(customPublisher)
                    sdk = ABsmartly.create(sdkConfig)
                    context = sdk.createContextWith(contextConfig, contextData)
                } else if (endpoint != null) {
                    // The SDK's own Client fetches GET {endpoint}/context and
                    // deserializes the payload, exactly as against the collector.
                    val clientConfig = com.absmartly.sdk.ClientConfig.create()
                        .setEndpoint(translateEndpoint(endpoint))
                        .setAPIKey("test-api-key")
                        .setApplication("test-app")
                        .setEnvironment("test-env")
                    val client = com.absmartly.sdk.Client.create(clientConfig)
                    val sdkConfig = ABSmartlyConfig.create()
                        .setClient(client)
                        .setContextPublisher(customPublisher)
                    if (payloadThrottle > 0) {
                        throttledProvider = ThrottledContextDataProvider(
                            com.absmartly.sdk.DefaultContextDataProvider(client), payloadThrottle
                        )
                        sdkConfig.setContextDataProvider(throttledProvider)
                    }
                    sdk = ABsmartly.create(sdkConfig)
                    context = sdk.createContext(contextConfig)
                    if (payloadThrottle <= 0) {
                        // Block on an IO thread, not the Netty event loop: the
                        // SDK's fetch is an HTTP request back to this server.
                        withContext(Dispatchers.IO) {
                            context.waitUntilReadyAsync().get(5, TimeUnit.SECONDS)
                        }
                    }
                } else if (failLoad) {
                    val failingProvider = object : com.absmartly.sdk.ContextDataProvider {
                        override fun getContextData(): java.util.concurrent.CompletableFuture<ContextData> {
                            val f = java.util.concurrent.CompletableFuture<ContextData>()
                            f.completeExceptionally(RuntimeException("Context load failed"))
                            return f
                        }
                    }
                    val sdkConfig = ABSmartlyConfig.create()
                        .setContextDataProvider(failingProvider)
                        .setContextPublisher(customPublisher)
                        .setContextEventLogger(eventCollector)
                    sdk = ABsmartly.create(sdkConfig)
                    context = sdk.createContext(contextConfig)
                    context.waitUntilReady()
                } else {
                    call.respond(HttpStatusCode.BadRequest, mapOf(
                        "error" to "One of data, endpoint or failLoad is required"
                    ))
                    return@post
                }

                val wrapper = ContextWrapper(
                    context, eventCollector, dataProvider, customPublisher, sdk, throttledProvider,
                    fetchesFromEndpoint = endpoint != null && !request.containsKey("data")
                )
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
                    "type" to e.javaClass.simpleName,
                    "events" to eventCollector.getEvents()
                ))
            }
        }

        delete("/context/{contextId}") {
            val contextId = call.parameters["contextId"]!!
            val removed = contexts.remove(contextId)
            if (removed != null) {
                // Drop a still-pending throttled fetch, and release the per-context
                // SDK's HTTP client and scheduler (close() may wait on queued tasks).
                removed.throttledProvider?.cancel()
                removed.sdk?.let { sdk -> Thread { sdk.close() }.apply { isDaemon = true }.start() }
            }
            call.respond(mapOf("result" to "deleted"))
        }

        post("/context/{contextId}/setUnit") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            val request = call.receive<Map<String, Any>>()
            val unitType = request["unitType"] as? String
            try {
                when (val uid = request["uid"]) {
                    is Number -> wrapper.context.setUnit(unitType ?: "", uid)
                    else -> wrapper.context.setUnit(unitType ?: "", uid.toString())
                }
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to null, "events" to newEvents))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/getUnit") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val request = call.receive<Map<String, Any>>()
                val result = wrapper.context.getUnit(request["unitType"] as String)

                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/attribute") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val request = call.receive<Map<String, Any?>>()
                wrapper.context.setAttribute(request["name"] as String, request["value"])
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to null, "events" to newEvents))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/getAttribute") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val request = call.receive<Map<String, Any>>()
                val result = wrapper.context.getAttribute(request["name"] as String)
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/treatment") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val request = call.receive<Map<String, Any>>()
                val variant = wrapper.context.getTreatment(request["experimentName"] as String)
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to variant, "events" to newEvents))
            } catch (e: Exception) {
                e.printStackTrace()
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/peek") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val request = call.receive<Map<String, Any>>()
                val result = wrapper.context.peekTreatment(request["experimentName"] as String)
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/variableValue") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val request = call.receive<Map<String, Any?>>()
                val result = wrapper.context.getVariableValue(request["key"] as String, request["defaultValue"])
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                e.printStackTrace()
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/peekVariableValue") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val request = call.receive<Map<String, Any?>>()
                val result = wrapper.context.peekVariableValue(request["key"] as String, request["defaultValue"])
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                e.printStackTrace()
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/track") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val request = call.receive<Map<String, Any?>>()
                val goalName = request["goalName"] as String
                val propertiesObj = request["properties"]

                if (propertiesObj != null && propertiesObj !is Map<*, *>) {
                    // JSON-to-type boundary: the SDK's track() only accepts a map.
                    call.respond(HttpStatusCode.BadRequest, mapOf(
                        "error" to "Goal '$goalName' properties must be of type object.",
                        "events" to wrapper.eventCollector.getNewEvents(eventsBefore)
                    ))
                    return@post
                }

                @Suppress("UNCHECKED_CAST")
                val properties = propertiesObj as? Map<String, Any?>

                wrapper.context.track(goalName, properties)
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to null, "events" to newEvents))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/override") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val request = call.receive<Map<String, Any>>()
                wrapper.context.setOverride(
                    request["experimentName"] as String,
                    (request["variant"] as Number).toInt()
                )
                call.respond(mapOf("result" to null, "events" to wrapper.eventCollector.getNewEvents(eventsBefore)))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/setOverride") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val request = call.receive<Map<String, Any>>()
                wrapper.context.setOverride(
                    request["experimentName"] as String,
                    (request["variant"] as Number).toInt()
                )
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to null, "events" to newEvents))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/customAssignment") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val request = call.receive<Map<String, Any>>()
                wrapper.context.setCustomAssignment(
                    request["experimentName"] as String,
                    (request["variant"] as Number).toInt()
                )
                call.respond(mapOf("result" to null, "events" to wrapper.eventCollector.getNewEvents(eventsBefore)))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/setCustomAssignment") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val request = call.receive<Map<String, Any>>()
                wrapper.context.setCustomAssignment(
                    request["experimentName"] as String,
                    (request["variant"] as Number).toInt()
                )
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to null, "events" to newEvents))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/customFieldValue") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val request = call.receive<Map<String, Any>>()
                val result = wrapper.context.getCustomFieldValue(
                    request["experimentName"] as String,
                    request["fieldName"] as String
                )
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/variableKeys") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val keys = wrapper.context.variableKeys
                val result = keys.keys.toList()
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/customFieldKeys") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val request = call.receive<Map<String, Any>>()
                val keys = wrapper.context.customFieldKeys
                val result = keys.toList()
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/customFieldValueType") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val request = call.receive<Map<String, Any>>()
                val result = wrapper.context.getCustomFieldValueType(
                    request["experimentName"] as String,
                    request["fieldName"] as String
                )
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        get("/context/{contextId}/pending") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@get call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            call.respond(mapOf("result" to wrapper.context.pendingCount, "events" to wrapper.eventCollector.getNewEvents(eventsBefore)))
        }

        get("/context/{contextId}/isFinalized") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@get call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            call.respond(mapOf("result" to wrapper.context.isClosed, "events" to wrapper.eventCollector.getNewEvents(eventsBefore)))
        }

        get("/context/{contextId}/isReady") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@get call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            call.respond(mapOf("result" to wrapper.context.isReady, "events" to wrapper.eventCollector.getNewEvents(eventsBefore)))
        }

        get("/context/{contextId}/isFailed") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@get call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            call.respond(mapOf("result" to wrapper.context.isFailed, "events" to wrapper.eventCollector.getNewEvents(eventsBefore)))
        }

        get("/context/{contextId}/experiments") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@get call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val result = wrapper.context.experiments
                call.respond(mapOf("result" to result, "events" to wrapper.eventCollector.getNewEvents(eventsBefore)))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/getUnits") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val result = wrapper.context.getUnits()
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to result, "events" to newEvents))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/getAttributes") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val attrs = wrapper.context.getAttributes()
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to attrs, "events" to newEvents))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/readyError") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val error = wrapper.context.readyError()
                val result = error?.let { mapOf("isError" to true, "message" to (it.message ?: it.toString())) }
                call.respond(mapOf("result" to result, "events" to wrapper.eventCollector.getNewEvents(eventsBefore)))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/variableKeysMap") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val keys = wrapper.context.variableKeys
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to keys, "events" to newEvents))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/globalCustomFieldKeys") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val keys = wrapper.context.customFieldKeys
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to keys.toList(), "events" to newEvents))
            } catch (e: Exception) {
                call.respondError(HttpStatusCode.BadRequest, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/publishFail") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            wrapper.publisher?.setShouldFail(true)
            call.respond(mapOf("result" to null, "events" to wrapper.eventCollector.getNewEvents(eventsBefore)))
        }

        post("/context/{contextId}/publish") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                // Await the SDK publish so the collector PUT completes before we
                // respond; otherwise e2e publish races the collector verification.
                wrapper.context.publish().join()
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to null, "events" to newEvents))
            } catch (e: Exception) {
                e.printStackTrace()
                call.respondError(HttpStatusCode.InternalServerError, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/refresh") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                val request = call.receive<Map<String, Any>>()
                val newData = if (request.containsKey("newData")) {
                    objectMapper.convertValue(request["newData"], ContextData::class.java)
                } else null
                // An endpoint-backed context re-fetches through the SDK client (the
                // orchestrator has already updated the payload store). An inline-data
                // context has no endpoint, so its test provider serves the next payload.
                if (!wrapper.fetchesFromEndpoint && newData != null) {
                    wrapper.dataProvider?.setNextData(newData)
                }
                withContext(Dispatchers.IO) { wrapper.context.refresh().join() }
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to null, "events" to newEvents))
            } catch (e: Exception) {
                e.printStackTrace()
                call.respondError(HttpStatusCode.InternalServerError, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }

        post("/context/{contextId}/finalize") {
            val contextId = call.parameters["contextId"]!!
            val wrapper = contexts[contextId] ?: return@post call.respond(
                HttpStatusCode.NotFound, mapOf("error" to "Context not found")
            )
            val eventsBefore = wrapper.eventCollector.getEvents().size
            try {
                wrapper.context.close()
                val newEvents = wrapper.eventCollector.getNewEvents(eventsBefore)
                call.respond(mapOf("result" to null, "events" to newEvents))
            } catch (e: Exception) {
                e.printStackTrace()
                call.respondError(HttpStatusCode.InternalServerError, e, wrapper.eventCollector.getNewEvents(eventsBefore))
            }
        }
    }
}
