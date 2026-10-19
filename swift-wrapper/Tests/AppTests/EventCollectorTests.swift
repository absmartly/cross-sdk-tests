import XCTest
import ABSmartly
@testable import App

// Handlers return each request's events as `events.suffix(from: eventsBefore)`,
// which is only correct if the collector never drops old events.
final class EventCollectorTests: XCTestCase {
    func testEventWindowsStayCorrectPastOneHundredEvents() throws {
        let collector = EventCollector()
        let sdk = try ABSmartlySDK(config: ABSmartlyConfig(
            contextDataProvider: DummyContextDataProvider(),
            contextPublisher: CustomPublisher(),
            contextEventLogger: collector,
            variableParser: nil,
            scheduler: nil,
            client: nil
        ))

        var config = ContextConfig()
        config.setUnits(units: ["session_id": "u1"])
        config.eventLogger = collector
        config.publishDelay = -1
        config.refreshInterval = 0
        let context = sdk.createContextWithData(config: config, contextData: ContextData(experiments: []))

        XCTAssertEqual(collector.events.count, 1)  // ready

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
}
