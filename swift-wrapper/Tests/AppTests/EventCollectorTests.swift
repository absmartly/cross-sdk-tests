import XCTest
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
        XCTAssertNil((collector.events[2]["data"] as? [String: Any])?["properties"])
    }
}
