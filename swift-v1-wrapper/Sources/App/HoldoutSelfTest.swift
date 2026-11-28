import ABSmartly
import Foundation
import PromiseKit

// Behavioural self-test for the `holdouts` / `holdout_arms` wrapper capabilities, the Swift
// counterpart of javascript-wrapper/holdoutSelfTest.js and java-wrapper's HoldoutSelfTest /
// HoldoutArmsSelfTest. It decodes a real ContextData from fixture values ported from the scenario
// battery (test_scenarios_complete.json), builds a context through the public SDK API, calls
// getTreatment() and requires BOTH the returned variant and the exact ordered id/name/variant
// sequence of exposures that call fires. A pre-holdout swift-sdk decodes the payload but ignores
// `holdouts`/`holdoutIds`, so check A fails and the capability reports false (fails closed).
enum HoldoutSelfTest {
    private static let lock = NSLock()
    private static var holdoutsResult: Bool?
    private static var holdoutArmsResult: Bool?

    static func runHoldouts() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        if let result = holdoutsResult { return result }
        let result = runHoldoutsBattery()
        holdoutsResult = result
        return result
    }

    // Three-arm support cannot exist without basic holdout support, so this is gated on it.
    static func runHoldoutArms() -> Bool {
        guard runHoldouts() else {
            print("[holdout_arms probe] skipped: base holdouts battery did not pass")
            return false
        }
        lock.lock()
        defer { lock.unlock() }
        if let result = holdoutArmsResult { return result }
        let result = runHoldoutArmsBattery()
        holdoutArmsResult = result
        return result
    }

    private struct Seen: Equatable, CustomStringConvertible {
        let id: Int
        let name: String
        let variant: Int
        var description: String { return "(\(id), \(name), \(variant))" }
    }

    private final class ExposureLog: ContextEventLogger {
        private let lock = NSLock()
        private var seen: [Seen] = []

        func handleEvent(context: Context, event: ContextEventLoggerEvent) {
            if case let .exposure(exposure) = event {
                lock.lock()
                seen.append(Seen(id: exposure.id, name: exposure.name, variant: exposure.variant))
                lock.unlock()
            }
        }

        var count: Int {
            lock.lock()
            defer { lock.unlock() }
            return seen.count
        }

        func since(_ index: Int) -> [Seen] {
            lock.lock()
            defer { lock.unlock() }
            return Array(seen[index...])
        }
    }

    private static func experiment(
        _ id: Int, _ name: String, unitType: String, split: [Double] = [0.5, 0.5], fullOnVariant: Int = 0,
        variants: Int = 2, holdoutIds: [Int]?
    ) -> [String: Any] {
        var experiment: [String: Any] = [
            "id": id, "name": name, "iteration": 1, "unitType": unitType, "seedHi": 100, "seedLo": 200,
            "split": split, "trafficSeedHi": 1, "trafficSeedLo": 2, "trafficSplit": [0.0, 1.0],
            "fullOnVariant": fullOnVariant, "applications": [["name": "website"]],
            "variants": (0..<variants).map { ["name": String(UnicodeScalar(65 + $0)!), "config": NSNull()] },
            "audience": NSNull(), "audienceStrict": false, "customFieldValues": NSNull(),
        ]
        experiment["holdoutIds"] = holdoutIds ?? NSNull() as Any
        return experiment
    }

    private static func holdout(
        _ id: Int, _ name: String, unitType: String, seedHi: Int, seedLo: Int, split: [Double] = [0.1, 0.9],
        holdoutType: String = "full"
    ) -> [String: Any] {
        return [
            "id": id, "name": name, "iteration": 1, "unitType": unitType, "seedHi": seedHi, "seedLo": seedLo,
            "split": split, "trafficSeedHi": 0, "trafficSeedLo": 0, "trafficSplit": [0.0, 1.0], "fullOnVariant": 0,
            "applications": [] as [Any], "variants": split.indices.map { ["name": String(UnicodeScalar(65 + $0)!), "config": NSNull()] },
            "audience": NSNull(), "audienceStrict": false, "customFieldValues": NSNull(), "holdoutType": holdoutType,
        ]
    }

    private final class CapturingPublisher: ContextPublisher {
        private let lock = NSLock()
        private var batches: [[Seen]] = []

        func publish(event: PublishEvent) -> Promise<Void> {
            lock.lock()
            batches.append(event.exposures.map { Seen(id: $0.id, name: $0.name, variant: $0.variant) })
            lock.unlock()
            return Promise.value(())
        }

        var published: [[Seen]] {
            lock.lock()
            defer { lock.unlock() }
            return batches
        }
    }

    // Scenario 221 requires the queued experiment then holdout, not just their callbacks.
    static func lateUnitPublishMatches(_ ids: [Int], _ names: [String], _ variants: [Int]) -> Bool {
        return ids == [308, 408] && names == ["probe_g_late_unit", "probe_g_holdout"] && variants == [2, 1]
    }

    private static func makeContext(
        experiments: [[String: Any]], holdouts: [[String: Any]], units: [String: String], log: ExposureLog,
        publisher: ContextPublisher = CustomPublisher()
    ) throws -> Context {
        let payload = try JSONSerialization.data(withJSONObject: ["experiments": experiments, "holdouts": holdouts])
        let data = try JSONDecoder().decode(ContextData.self, from: payload)
        let client = try DefaultClient(config: ClientConfig(
            apiKey: "self-test", application: "test", endpoint: "http://127.0.0.1:3000", environment: "test"))
        let sdk = try ABSmartlySDK(config: ABSmartlyConfig(
            contextDataProvider: DummyContextDataProvider(), contextPublisher: publisher,
            contextEventLogger: log, variableParser: nil, scheduler: nil, client: client))
        let config = ContextConfig()
        for (unitType, uid) in units {
            config.setUnit(unitType: unitType, uid: uid)
        }
        config.publishDelay = -1
        config.refreshInterval = 0
        return sdk.createContextWithData(config: config, contextData: data)
    }

    private static func verify(
        _ prefix: String, _ label: String, _ context: Context, _ log: ExposureLog, _ experimentName: String,
        _ expectedTreatment: Int, _ expected: [Seen]
    ) -> Bool {
        let before = log.count
        let treatment = context.getTreatment(experimentName)
        let fired = log.since(before)
        if treatment != expectedTreatment || fired != expected {
            print("\(prefix) check \(label) FAILED: expected treatment=\(expectedTreatment) with exposures \(expected), got treatment=\(treatment) exposures=\(fired)")
            return false
        }
        return true
    }

    // Two-arm battery, from scenarios 203-208 and 221. Synthetic ids (301-308 experiments, 401-408
    // holdouts) let all checks share one context; every value that drives assignment (unit type,
    // seeds, split, full-on variant, uid) is the scenario's own.
    private static func runHoldoutsBattery() -> Bool {
        let prefix = "[holdouts probe]"
        let heldOutType = "session_id"
        let heldOutUid = "e791e240fcd3df7d238cfc285f475e8152fcc0ec"
        let notHeldOutType = "session_id_b"
        let notHeldOutUid = "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3"
        let lateType = "user_id"

        let experiments = [
            experiment(301, "probe_a_covered", unitType: heldOutType, holdoutIds: [401]),
            experiment(302, "probe_b_covered", unitType: notHeldOutType, holdoutIds: [402]),
            experiment(304, "probe_c_union", unitType: heldOutType, holdoutIds: [404, 405]),
            experiment(305, "probe_d_covered", unitType: heldOutType, holdoutIds: [406]),
            experiment(306, "probe_d_uncovered_sibling", unitType: heldOutType, holdoutIds: nil),
            experiment(307, "probe_e_dangling_plus_valid", unitType: heldOutType, holdoutIds: [999, 407]),
            experiment(303, "probe_f_fullon", unitType: heldOutType, fullOnVariant: 2, variants: 3, holdoutIds: [403]),
            experiment(
                308, "probe_g_late_unit", unitType: lateType, split: [0.5, 0.5, 0.0], fullOnVariant: 2, variants: 3,
                holdoutIds: [408]),
        ]
        let holdouts = [
            holdout(401, "probe_a_holdout", unitType: heldOutType, seedHi: 13, seedLo: 111),
            holdout(402, "probe_b_holdout", unitType: notHeldOutType, seedHi: 13, seedLo: 111),
            holdout(404, "probe_c_holdout_low", unitType: heldOutType, seedHi: 1, seedLo: 222),
            holdout(405, "probe_c_holdout_high", unitType: heldOutType, seedHi: 13, seedLo: 111),
            holdout(406, "probe_d_holdout", unitType: heldOutType, seedHi: 13, seedLo: 111),
            holdout(407, "probe_e_holdout", unitType: heldOutType, seedHi: 13, seedLo: 111),
            holdout(403, "probe_f_holdout", unitType: heldOutType, seedHi: 13, seedLo: 111),
            holdout(408, "probe_g_holdout", unitType: lateType, seedHi: 1, seedLo: 222),
        ]

        do {
            let log = ExposureLog()
            let publisher = CapturingPublisher()
            let context = try makeContext(
                experiments: experiments, holdouts: holdouts,
                units: [heldOutType: heldOutUid, notHeldOutType: notHeldOutUid], log: log, publisher: publisher)
            guard context.isReady(), !context.isFailed() else {
                print("\(prefix) FAILED: context not ready (failed=\(context.isFailed()))")
                return false
            }

            let checks: [(String, String, Int, [Seen])] = [
                ("A (mirrors 203)", "probe_a_covered", 0, [Seen(id: 401, name: "probe_a_holdout", variant: 0)]),
                (
                    "B (mirrors 204)", "probe_b_covered", 0,
                    [
                        Seen(id: 302, name: "probe_b_covered", variant: 0),
                        Seen(id: 402, name: "probe_b_holdout", variant: 1),
                    ]
                ),
                (
                    "C (mirrors 205)", "probe_c_union", 0,
                    [
                        Seen(id: 404, name: "probe_c_holdout_low", variant: 1),
                        Seen(id: 405, name: "probe_c_holdout_high", variant: 0),
                    ]
                ),
                ("D (mirrors 206, covered)", "probe_d_covered", 0, [Seen(id: 406, name: "probe_d_holdout", variant: 0)]),
                (
                    "D (mirrors 206, uncovered sibling)", "probe_d_uncovered_sibling", 1,
                    [Seen(id: 306, name: "probe_d_uncovered_sibling", variant: 1)]
                ),
                (
                    "E (mirrors 207)", "probe_e_dangling_plus_valid", 0,
                    [Seen(id: 407, name: "probe_e_holdout", variant: 0)]
                ),
                ("F (mirrors 208)", "probe_f_fullon", 0, [Seen(id: 403, name: "probe_f_holdout", variant: 0)]),
            ]
            for (label, name, treatment, expected) in checks {
                guard verify(prefix, label, context, log, name, treatment, expected) else { return false }
            }

            // Check G (mirrors 221): peek resolves the full-on experiment before its unit exists and
            // fires nothing; once the unit is set, getTreatment publishes both exposures exactly once.
            let before = log.count
            let peeked = context.peekTreatment("probe_g_late_unit")
            guard peeked == 2, log.since(before).isEmpty else {
                print("\(prefix) check G (mirrors 221, pre-unit peek) FAILED: got treatment=\(peeked) exposures=\(log.since(before))")
                return false
            }
            // Flush A-F so G's queued exposures can be checked on their own.
            try context.publish().wait()
            let batchesBefore = publisher.published.count
            context.setUnit(unitType: lateType, uid: heldOutUid)
            guard verify(
                prefix, "G (mirrors 221, post-unit treatment)", context, log, "probe_g_late_unit", 2,
                [Seen(id: 308, name: "probe_g_late_unit", variant: 2), Seen(id: 408, name: "probe_g_holdout", variant: 1)])
            else { return false }

            try context.publish().wait()
            let batches = publisher.published
            guard batches.count == batchesBefore + 1,
                let batch = batches.last,
                lateUnitPublishMatches(batch.map { $0.id }, batch.map { $0.name }, batch.map { $0.variant })
            else {
                print("\(prefix) check G (mirrors 221, publish) FAILED: \(batches)")
                return false
            }

            print("\(prefix) behavioral self-test PASSED: all 7 checks mirroring scenarios 203-208 and 221 passed")
            return true
        } catch {
            print("\(prefix) behavioral self-test FAILED with error: \(error)")
            return false
        }
    }

    // Three-arm battery, from scenarios 214-216 (same ids as the scenarios).
    private static func runHoldoutArmsBattery() -> Bool {
        let prefix = "[holdout_arms probe]"
        let unitType = "session_id"
        let uid = "e791e240fcd3df7d238cfc285f475e8152fcc0ec"
        let threeArm = [0.3, 0.3, 0.4]

        let experiments = [
            experiment(601, "arms_0_non_fullon", unitType: unitType, holdoutIds: [621]),
            experiment(602, "arms_0_fullon", unitType: unitType, fullOnVariant: 2, variants: 3, holdoutIds: [621]),
            experiment(603, "arms_1_non_fullon", unitType: unitType, holdoutIds: [622]),
            experiment(604, "arms_1_fullon", unitType: unitType, fullOnVariant: 2, variants: 3, holdoutIds: [622]),
            experiment(605, "arms_2_non_fullon", unitType: unitType, holdoutIds: [623]),
            experiment(606, "arms_2_fullon", unitType: unitType, fullOnVariant: 2, variants: 3, holdoutIds: [623]),
        ]
        let holdouts = [
            holdout(621, "arms_holdout_0", unitType: unitType, seedHi: 0, seedLo: 1, split: threeArm, holdoutType: "all_full_on"),
            holdout(622, "arms_holdout_1", unitType: unitType, seedHi: 0, seedLo: 3, split: threeArm, holdoutType: "all_full_on"),
            holdout(623, "arms_holdout_2", unitType: unitType, seedHi: 0, seedLo: 0, split: threeArm, holdoutType: "all_full_on"),
        ]

        do {
            let log = ExposureLog()
            let context = try makeContext(experiments: experiments, holdouts: holdouts, units: [unitType: uid], log: log)
            guard context.isReady(), !context.isFailed() else {
                print("\(prefix) FAILED: context not ready (failed=\(context.isFailed()))")
                return false
            }

            let checks: [(String, String, Int, [Seen])] = [
                ("0 non-full-on (mirrors 214)", "arms_0_non_fullon", 0, [Seen(id: 621, name: "arms_holdout_0", variant: 0)]),
                ("0 full-on (mirrors 214)", "arms_0_fullon", 0, []),
                ("1 non-full-on (mirrors 215)", "arms_1_non_fullon", 0, [Seen(id: 622, name: "arms_holdout_1", variant: 1)]),
                ("1 full-on (mirrors 215)", "arms_1_fullon", 2, [Seen(id: 604, name: "arms_1_fullon", variant: 2)]),
                (
                    "2 non-full-on (mirrors 216)", "arms_2_non_fullon", 1,
                    [Seen(id: 605, name: "arms_2_non_fullon", variant: 1), Seen(id: 623, name: "arms_holdout_2", variant: 2)]
                ),
                ("2 full-on (mirrors 216)", "arms_2_fullon", 2, [Seen(id: 606, name: "arms_2_fullon", variant: 2)]),
            ]
            for (label, name, treatment, expected) in checks {
                guard verify(prefix, label, context, log, name, treatment, expected) else { return false }
            }

            print("\(prefix) behavioral self-test PASSED: all 3 arm checks mirroring scenarios 214-216 passed")
            return true
        } catch {
            print("\(prefix) behavioral self-test FAILED with error: \(error)")
            return false
        }
    }
}
