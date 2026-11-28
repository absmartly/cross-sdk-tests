package com.absmartly.wrapper

import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import com.fasterxml.jackson.module.kotlin.readValue
import io.ktor.server.engine.embeddedServer
import io.ktor.server.netty.Netty
import java.net.ServerSocket
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals

class CreateRouteTest {
    // The race itself (ready before the Ready event is logged) is pinned deterministically in
    // EventCollectorTest. Whether a request hits that window depends on thread timing, so these route tests
    // check the wiring instead: every creation path waits for its creation event, then answers with it.
    private class CountingCollector : EventCollector() {
        val waits = java.util.concurrent.atomic.AtomicInteger()

        override fun awaitCreationEvent(timeoutMs: Long) {
            waits.incrementAndGet()
            super.awaitCreationEvent(timeoutMs)
        }
    }

    private val mapper = jacksonObjectMapper()
    private val http = HttpClient.newHttpClient()
    private val collectors = java.util.concurrent.CopyOnWriteArrayList<CountingCollector>()
    private val port = ServerSocket(0).use { it.localPort }
    private val server = embeddedServer(Netty, port = port, host = "127.0.0.1") {
        configureRouting(
            ownPort = port,
            newCollector = { CountingCollector().also { collectors.add(it) } },
            env = { name ->
                mapOf("ABSMARTLY_E2E_ENDPOINT" to "http://127.0.0.1:$port/context_payload/e2e",
                      "ABSMARTLY_E2E_API_KEY" to "k", "ABSMARTLY_E2E_APPLICATION" to "website",
                      "ABSMARTLY_E2E_ENVIRONMENT" to "test")[name]
            },
        )
    }

    @BeforeTest
    fun start() {
        server.start(wait = false)
        val payload = """{"data": {"experiments": []}}"""
        for (id in listOf("p", "e2e")) send("PUT", "/context_payload/$id", payload)
    }

    @AfterTest
    fun stop() {
        server.stop(100, 1000)
    }

    private fun send(method: String, path: String, body: String): Map<String, Any?> {
        val request = HttpRequest.newBuilder(URI("http://127.0.0.1:$port$path"))
            .header("Content-Type", "application/json")
            .method(method, HttpRequest.BodyPublishers.ofString(body)).build()
        val response = http.send(request, HttpResponse.BodyHandlers.ofString())
        assertEquals(200, response.statusCode(), "$method $path: ${response.body()}")
        return if (response.body().isBlank()) emptyMap() else mapper.readValue(response.body())
    }

    private fun assertAnswersWithItsCreationEvent(body: String, event: String) {
        val response = send("POST", "/context", body)
        val collector = collectors.last()
        assertEquals(1, collector.waits.get(), "the route waits for the creation event exactly once")
        @Suppress("UNCHECKED_CAST")
        val events = response["events"] as List<Map<String, Any?>>
        assertEquals(listOf(event), events.map { it["type"] })
        // Deleting the context closes the SDK client the route created; stopping Netty alone would not.
        @Suppress("UNCHECKED_CAST")
        val contextId = (response["result"] as Map<String, Any?>)["contextId"]
        send("DELETE", "/context/$contextId", "")
    }

    @Test
    fun `inline data answers with its ready event`() {
        assertAnswersWithItsCreationEvent("""{"data": {"experiments": []}, "units": {"session_id": "u"}}""", "ready")
    }

    @Test
    fun `fetched endpoint answers with its ready event`() {
        assertAnswersWithItsCreationEvent(
            """{"endpoint": "http://127.0.0.1:$port/context_payload/p", "units": {"session_id": "u"}}""", "ready")
    }

    @Test
    fun `failed load answers with its error event`() {
        assertAnswersWithItsCreationEvent("""{"failLoad": true, "units": {"session_id": "u"}}""", "error")
    }

    @Test
    fun `e2e mode answers with its ready event`() {
        assertAnswersWithItsCreationEvent("""{"mode": "e2e", "units": {"session_id": "u"}}""", "ready")
    }
}
