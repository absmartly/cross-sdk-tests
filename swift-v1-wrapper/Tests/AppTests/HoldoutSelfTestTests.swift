import XCTest
@testable import App

final class HoldoutSelfTestTests: XCTestCase {
    func testLateUnitPublishRequiresBothExposuresOnceAndInOrder() {
        let matches = HoldoutSelfTest.lateUnitPublishMatches
        XCTAssertTrue(matches([308, 408], ["probe_g_late_unit", "probe_g_holdout"], [2, 1]))
        XCTAssertFalse(matches([308], ["probe_g_late_unit"], [2]))
        XCTAssertFalse(matches([408, 308], ["probe_g_holdout", "probe_g_late_unit"], [1, 2]))
        XCTAssertFalse(matches([308, 408, 408], ["probe_g_late_unit", "probe_g_holdout", "probe_g_holdout"], [2, 1, 1]))
        XCTAssertFalse(matches([308, 408], ["probe_g_late_unit", "probe_g_holdout"], [2, 0]))
    }

    func testLinkedSDKQueuesLateUnitHoldout() {
        XCTAssertTrue(HoldoutSelfTest.runHoldouts())
    }
}
