import XCTest
import Vapor
import ABsmartly
@testable import App

// Handlers return each request's events as `events.suffix(from: eventsBefore)`,
// which is only correct if the collector never drops old events.
final class EventCollectorTests: XCTestCase {
    func testEventWindowsStayCorrectPastOneHundredEvents() throws {
        let collector = EventCollector()
        let transport = WrapperTransport(publishToEndpoint: false, inlineData: true)
        let client = try makeClient(
            endpoint: "http://127.0.0.1:3000", application: "test-app", environment: "test-env", transport: transport)
        let context = client.context(
            units: ["session_id": "u1"],
            options: Context.Options(publishDelay: -1, refreshInterval: 0, eventLogger: collector.logger),
            data: ContextData(experiments: []))

        XCTAssertEqual(collector.events.count, 1)

        for i in 0..<250 {
            let before = collector.events.count
            context.track("goal_\(i)", properties: nil)
            let window = Array(collector.events.suffix(from: before))
            XCTAssertEqual(window.count, 1, "track #\(i) should yield exactly its own event")
            XCTAssertEqual(window.first?["type"] as? String, "goal")
            XCTAssertEqual((window.first?["data"] as? [String: Any])?["name"] as? String, "goal_\(i)")
        }

        XCTAssertEqual(collector.events.count, 251)
        XCTAssertEqual(collector.events.first?["type"] as? String, "ready")
    }

    // Exposures carry an explicit null unit; goals without properties have no properties member;
    // the close event is reported as "finalize".
    func testEventShapes() async throws {
        let collector = EventCollector()
        let transport = WrapperTransport(publishToEndpoint: false, inlineData: true)
        let client = try makeClient(
            endpoint: "http://127.0.0.1:3000", application: "test-app", environment: "test-env", transport: transport)
        let context = client.context(
            units: ["session_id": "u1"],
            options: Context.Options(publishDelay: -1, refreshInterval: 0, eventLogger: collector.logger),
            data: ContextData(experiments: []))
        _ = context.treatment(for: "unknown")
        context.track("g", properties: nil)
        try await context.close()

        let types = collector.events.map { $0["type"] as? String }
        XCTAssertEqual(types, ["ready", "exposure", "goal", "publish", "finalize"])
        XCTAssertTrue((collector.events[1]["data"] as? [String: Any])?["unit"] is NSNull)
        // The SDK's own encoding passes through unchanged: no properties is an explicit null.
        XCTAssertTrue((collector.events[2]["data"] as? [String: Any])?["properties"] is NSNull)
    }

    // An inline-data context has no endpoint: a refresh fetches an empty context and stays ready.
    func testInlineDataRefreshServesAnEmptyContext() async throws {
        let collector = EventCollector()
        let transport = WrapperTransport(publishToEndpoint: false, inlineData: true)
        let client = try makeClient(
            endpoint: "http://127.0.0.1:1", application: "test-app", environment: "test-env", transport: transport)
        let experiment = Experiment(id: 1, name: "exp", unitType: "session_id", split: [0, 1],
            variants: [ExperimentVariant(name: "A", config: nil), ExperimentVariant(name: "B", config: nil)])
        let context = client.context(
            units: ["session_id": "u1"],
            options: Context.Options(publishDelay: -1, refreshInterval: 0, eventLogger: collector.logger),
            data: ContextData(experiments: [experiment]))
        try await context.refresh()

        XCTAssertEqual(context.state, .ready)
        let refresh = try XCTUnwrap(collector.events.last)
        XCTAssertEqual(refresh["type"] as? String, "refresh")
        XCTAssertEqual(((refresh["data"] as? [String: Any])?["experiments"] as? [Any])?.count, 0)
        XCTAssertEqual(context.experiments, [])
    }

    // payloadThrottle delays only the first context fetch, so the context is still loading meanwhile.
    func testPayloadThrottleDelaysOnlyTheFirstFetch() async throws {
        let served = Data(#"{"experiments":[]}"#.utf8)
        let server = try LocalServer(body: served)
        defer { server.stop() }
        let transport = WrapperTransport(publishToEndpoint: false, throttleMs: 300)
        let client = try makeClient(
            endpoint: server.endpoint, application: "test-app", environment: "test-env", transport: transport)
        let context = client.makeContext(
            units: ["session_id": "u1"], options: Context.Options(publishDelay: -1, refreshInterval: 0))
        try await Task.sleep(nanoseconds: 100_000_000)
        XCTAssertEqual(context.state, .loading)
        try await context.ready()
        XCTAssertEqual(context.state, .ready)

        let start = Date()
        try await context.refresh()
        XCTAssertLessThan(Date().timeIntervalSince(start), 0.25)
    }
}

/// A one-route HTTP server on 127.0.0.1 that answers every request with `body`, for transport tests.
final class LocalServer: @unchecked Sendable {
    private let app: Vapor.Application
    let endpoint: String

    init(body: Data) throws {
        app = Vapor.Application(.testing)
        app.http.server.configuration.hostname = "127.0.0.1"
        app.http.server.configuration.port = 0
        app.get("**") { _ in Vapor.Response(status: .ok, body: .init(data: body)) }
        try app.server.start()
        let port = app.http.server.shared.localAddress?.port ?? 0
        endpoint = "http://127.0.0.1:\(port)"
    }

    func stop() {
        app.server.shutdown()
        app.shutdown()
    }
}
