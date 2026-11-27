@_spi(Diagnostics) import ABsmartly
import Foundation

// Behavioural self-test behind the `switchback` and `integralFloatAttributes` capabilities (R33,
// cluster-defined switchbacks), the Swift counterpart of java-wrapper's SwitchbackSelfTest and
// go-wrapper/switchback.go, with the same probe payload and expected values. It checks a sample of
// the platform golden vectors through the SDK's own draw, then serves an R33 payload decoded with
// `ContextData(json:)` (cluster key order intact) through a real context and requires both the
// treatment and the exact exposures each read fires, then the published batch. A swift-sdk without
// switchback serving fails check A and reports false.
enum SwitchbackSelfTest {
    private static let lock = NSLock()
    private static var switchbackResult: Bool?
    private static var integralFloatResult: Bool?

    static func run() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        if let result = switchbackResult { return result }
        let result = battery()
        print("[switchback probe] behavioral self-test \(result ? "PASSED" : "FAILED")")
        switchbackResult = result
        return result
    }

    static func runIntegralFloat() -> Bool {
        guard run() else { return false }
        lock.lock()
        defer { lock.unlock() }
        if let result = integralFloatResult { return result }
        let result = integralFloatBattery()
        print("[integralFloatAttributes probe] \(result ? "PASSED" : "FAILED")")
        integralFloatResult = result
        return result
    }

    // {clusterUnit, periodIndex, seed, armCount, variant}, from fixtures/switchback/golden-vectors.json.
    private static let assignSample: [(String, Int64, Int64, Int, Int)] = [
        ("market_1", 0, 0, 2, 0),
        ("market_1", .min, 1, 2, 1),
        ("market_1", .max, 1, 2, 1),
        ("123456789", 0, 0, 2, 1),
        ("market_1", 10, 0, 3, 2),
        ("123456789", -2, -1, 3, 0),
        ("market_1", 5, 1, 10, 0),
        ("market_2", -11, .min, 10, 3),
        ("market_2", .max, .min, 10, 2),
        ("market_1", 1, .max, 2, 1),
    ]

    private static let permutationSample: [(String, Int64, Int64, [Int])] = [
        ("market_1", 0, 0, [0, 3, 7, 5, 8, 2, 6, 4, 1, 9]),
        ("market_1", .min, 0, [1, 0]),
        ("market_1", .max, 1, [0, 1]),
        ("123456789", 0, 0, [2, 0, 1]),
    ]

    // The scenarios' grid: a 400,000 h period keeps "now" in period 0 until 2069.
    private static let periodMs: Int64 = 400_000 * 3_600_000
    private static let anchor: Int64 = 1_704_067_200_000

    struct Seen: Equatable, CustomStringConvertible {
        let id: Int
        let name: String
        let unit: String?
        let variant: Int
        let assigned: Bool
        let eligible: Bool
        let flags: [Bool]  // overridden, fullOn, custom, audienceMismatch, ruleOverride
        var description: String { "(\(id), \(name), \(unit ?? "nil"), \(variant), \(assigned), \(eligible), \(flags))" }

        init(_ e: Exposure) {
            self.init(
                e.id, e.name, e.unit, e.variant, assigned: e.assigned, eligible: e.eligible,
                flags: [e.overridden, e.fullOn, e.custom, e.audienceMismatch, e.ruleOverride])
        }

        init(
            _ id: Int, _ name: String, _ unit: String?, _ variant: Int, assigned: Bool = true, eligible: Bool = true,
            flags: [Bool] = [false, false, false, false, false]
        ) {
            self.id = id
            self.name = name
            self.unit = unit
            self.variant = variant
            self.assigned = assigned
            self.eligible = eligible
            self.flags = flags
        }
    }

    final class Log: @unchecked Sendable {
        private let lock = NSLock()
        private var seen: [Seen] = []
        var logger: EventLogger {
            EventLogger { [self] _, event in
                if case let .exposure(exposure) = event {
                    lock.lock()
                    seen.append(Seen(exposure))
                    lock.unlock()
                }
            }
        }
        var count: Int { lock.lock(); defer { lock.unlock() }; return seen.count }
        func since(_ n: Int) -> [Seen] { lock.lock(); defer { lock.unlock() }; return Array(seen[n...]) }
    }

    // Records publish batches in memory; the context fetch is never used (the data is installed).
    final class Capture: Transport, @unchecked Sendable {
        private let lock = NSLock()
        private var bodies: [Data] = []
        func send(_ request: HTTPRequest) async throws -> ABsmartly.HTTPResponse {
            if request.method == "PUT", let body = request.body {
                lock.lock()
                bodies.append(body)
                lock.unlock()
                return ABsmartly.HTTPResponse(statusCode: 200, body: Data("{}".utf8))
            }
            return ABsmartly.HTTPResponse(statusCode: 500, body: Data())
        }
        var published: [Data] { lock.lock(); defer { lock.unlock() }; return bodies }
    }

    private static func experiment(
        _ id: Int, _ name: String, seedHi: Int, seedLo: Int, arms: Int, unitType: String, clusters: String?,
        values: String? = nil, extra: String = ""
    ) -> String {
        let split = Array(repeating: "\(1.0 / Double(arms))", count: arms).joined(separator: ",")
        let variants = (0..<arms).map { "{\"name\":\"\(String(UnicodeScalar(65 + $0)!))\",\"config\":null}" }
            .joined(separator: ",")
        var fields = [
            "\"id\":\(id)", "\"name\":\"\(name)\"", "\"iteration\":1", "\"unitType\":\"\(unitType)\"",
            "\"seedHi\":\(seedHi)", "\"seedLo\":\(seedLo)", "\"split\":[\(split)]", "\"trafficSeedHi\":1",
            "\"trafficSeedLo\":2", "\"trafficSplit\":[0,1]", "\"fullOnVariant\":0",
            "\"applications\":[{\"name\":\"website\"}]", "\"variants\":[\(variants)]", "\"audienceStrict\":false",
        ]
        if let clusters {
            fields += [
                "\"randomizationType\":\"switchback\"", "\"switchbackStartAt\":\(anchor)",
                "\"switchbackPeriodMs\":\(periodMs)", "\"switchbackDesignType\":\"balanced_block\"",
                "\"switchbackEstimand\":\"global_average_treatment_effect\"",
            ]
            if !clusters.isEmpty { fields.append("\"switchbackClusters\":\(clusters)") }
        }
        if let values { fields.append("\"switchbackClusterValues\":\(values)") }
        if !extra.isEmpty { fields.append(extra) }
        return "{" + fields.joined(separator: ",") + "}"
    }

    // assignSwitchback("market_1", 0, 42, 10) = 3 (hashed key 5; serving-9 uid 8, sticky 5; probe-user
    // uid 9, sticky 7). assignSwitchback("123456789", 0, 42, 10) = 6 ("123456789.0" 4). The cluster
    // maps are text, so sb_declared_order keeps unit_user before unit_serving.
    private static func probePayload() -> Data {
        let s = "unit_serving"
        let experiments = [
            experiment(521, "sb_agree", seedHi: 0, seedLo: 42, arms: 10, unitType: s,
                clusters: "{\"unit_serving\":\"city\",\"unit_user\":\"profile/city\"}"),
            experiment(522, "sb_declared_order", seedHi: 0, seedLo: 42, arms: 10, unitType: "unit_user",
                clusters: "{\"unit_user\":\"profile/city\",\"unit_serving\":\"city\"}"),
            experiment(523, "sb_disagree", seedHi: 0, seedLo: 42, arms: 10, unitType: s,
                clusters: "{\"unit_serving\":\"city\",\"unit_user\":\"profile/zone\"}"),
            experiment(524, "sb_whole_number", seedHi: 0, seedLo: 42, arms: 10, unitType: s,
                clusters: "{\"unit_serving\":\"store_number\"}"),
            experiment(529, "sb_float_value", seedHi: 0, seedLo: 42, arms: 10, unitType: s,
                clusters: "{\"unit_serving\":\"store_float\"}"),
            experiment(528, "sb_allow_empty", seedHi: 0, seedLo: 42, arms: 10, unitType: s,
                clusters: "{\"unit_serving\":\"city\"}", values: "[]"),
            experiment(525, "sb_value_filtered", seedHi: 0, seedLo: 1, arms: 10, unitType: s,
                clusters: "{\"unit_serving\":\"city\"}", values: "[\"market_2\"]"),
            experiment(526, "sb_phase1e_only", seedHi: 0, seedLo: 1, arms: 10, unitType: s, clusters: "",
                extra: "\"switchbackClusterUnitType\":\"unit_serving\",\"switchbackClusterCount\":10,\"switchbackClusterAttribute\":\"city\""),
            experiment(514, "sb_unit_baseline", seedHi: -559_038_737, seedLo: 1_234_567_890, arms: 3, unitType: s,
                clusters: nil),
        ]
        return Data("{\"experiments\":[\(experiments.joined(separator: ","))]}".utf8)
    }

    private static func context(_ json: Data, _ units: [UnitType: String], _ log: Log, _ capture: Capture) throws
        -> Context
    {
        var configuration = ABsmartlyClient.Configuration(
            endpoint: URL(string: "http://127.0.0.1:3000")!, apiKey: "self-test", application: "website",
            environment: "test")
        configuration.transport = capture
        let client = try ABsmartlyClient(configuration: configuration)
        return client.context(
            units: units, options: Context.Options(publishDelay: -1, refreshInterval: 0, eventLogger: log.logger),
            data: try ContextData(json: json))
    }

    private static func blocking(_ work: @escaping @Sendable () async throws -> Void) throws {
        final class Outcome: @unchecked Sendable { var error: Error? }
        let outcome = Outcome()
        let done = DispatchSemaphore(value: 0)
        Task {
            do { try await work() } catch { outcome.error = error }
            done.signal()
        }
        done.wait()
        if let error = outcome.error { throw error }
    }

    private static func battery() -> Bool {
        let prefix = "[switchback probe]"
        for (unit, period, seed, arms, want) in assignSample {
            let got = Diagnostics.switchbackVariant(clusterUnit: unit, periodIndex: period, seed: seed, armCount: arms)
            if got != want {
                print("\(prefix) golden assign FAILED for \((unit, period, seed, arms)): got \(String(describing: got))")
                return false
            }
        }
        for (unit, cycle, seed, want) in permutationSample {
            let got = Diagnostics.switchbackPermutation(
                clusterUnit: unit, cycleIndex: cycle, seed: seed, armCount: want.count)
            if got != want {
                print("\(prefix) golden permutation FAILED for \((unit, cycle, seed)): got \(String(describing: got))")
                return false
            }
        }
        do {
            let log = Log()
            let capture = Capture()
            // unit_user is set first, so only the declared order can make unit_serving serve sb_agree.
            let ctx = try context(probePayload(), ["unit_user": "probe-user", "unit_serving": "serving-9"], log, capture)
            guard ctx.state == .ready else {
                print("\(prefix) FAILED: context not ready")
                return false
            }
            ctx.setAttributes(try JSONValue(parsing:
                "{\"city\":\"market_1\",\"profile\":{\"city\":\"market_1\",\"zone\":\"market_2\"},\"store_number\":123456789,\"store_float\":123456789.0}"
            ).objectValue ?? [:])
            let checks: [(String, String, Int, [Seen])] = [
                ("agreement", "sb_agree", 3, [Seen(521, "sb_agree", "unit_serving", 3)]),
                ("agreement repeat", "sb_agree", 3, []),
                ("declared order", "sb_declared_order", 3, [Seen(522, "sb_declared_order", "unit_user", 3)]),
                ("disagreement", "sb_disagree", 0, []),
                ("whole number", "sb_whole_number", 6, [Seen(524, "sb_whole_number", "unit_serving", 6)]),
                ("float value", "sb_float_value", 0, []),
                ("value filtered out", "sb_value_filtered", 0, []),
                ("empty allow-list admits all", "sb_allow_empty", 3, [Seen(528, "sb_allow_empty", "unit_serving", 3)]),
                ("Phase 1E fields only", "sb_phase1e_only", 0, []),
                ("unit baseline", "sb_unit_baseline", 1, [Seen(514, "sb_unit_baseline", "unit_serving", 1)]),
            ]
            for (label, name, treatment, exposures) in checks {
                let before = log.count
                let got = ctx.treatment(for: name)
                let fired = log.since(before)
                if got != treatment || fired != exposures {
                    print("\(prefix) check \(label) FAILED: expected \(treatment) \(exposures), got \(got) \(fired)")
                    return false
                }
            }
            try blocking { try await ctx.publish() }
            let logged = log.since(0)
            guard capture.published.count == 1, let body = try? JSONValue(parsing: String(decoding: capture.published[0], as: UTF8.self)),
                case .array(let sent)? = body["exposures"]
            else {
                print("\(prefix) publish FAILED: \(capture.published.count) batches")
                return false
            }
            let published = sent.map { e in
                Seen(
                    e["id"]?.intValue ?? -1, e["name"]?.stringValue ?? "", e["unit"]?.stringValue,
                    e["variant"]?.intValue ?? -1, assigned: e["assigned"]?.boolValue ?? false,
                    eligible: e["eligible"]?.boolValue ?? false,
                    flags: ["overridden", "fullOn", "custom", "audienceMismatch", "ruleOverride"].map {
                        e[$0]?.boolValue ?? true
                    })
            }
            if published != logged {
                print("\(prefix) publish FAILED: published \(published), logged \(logged)")
                return false
            }
            try blocking { try await ctx.close() }
            return true
        } catch {
            print("\(prefix) FAILED with error: \(error)")
            return false
        }
    }

    // Scenario 426: an attribute value 3.0, as POST /context/{id}/attribute passes it, gives no cluster
    // value, while 3 keys "3". Under seed (0, 1) and 10 arms, "3" draws 5 and a lax "3.0" would draw 7.
    private static func integralFloatBattery() -> Bool {
        let prefix = "[integralFloatAttributes probe]"
        do {
            let json = Data(("{\"experiments\":["
                + experiment(531, "sb_float", seedHi: 0, seedLo: 1, arms: 10, unitType: "unit_serving",
                    clusters: "{\"unit_serving\":\"v_float\"}") + ","
                + experiment(532, "sb_integer", seedHi: 0, seedLo: 1, arms: 10, unitType: "unit_serving",
                    clusters: "{\"unit_serving\":\"v_int\"}") + "]}").utf8)
            let log = Log()
            let ctx = try context(json, ["unit_serving": "serving-9"], log, Capture())
            let body = Data("{\"f\": 3.0, \"i\": 3}".utf8)
            ctx.setAttribute("v_float", to: RawJSON.value(body, "f") ?? .null)
            ctx.setAttribute("v_int", to: RawJSON.value(body, "i") ?? .null)
            let float = ctx.treatment(for: "sb_float")
            let floatFired = log.since(0)
            let int = ctx.treatment(for: "sb_integer")
            let intFired = log.since(floatFired.count)
            guard float == 0, floatFired.isEmpty, int == 5, intFired == [Seen(532, "sb_integer", "unit_serving", 5)] else {
                print("\(prefix) FAILED: float \(float) \(floatFired), integer \(int) \(intFired)")
                return false
            }
            return true
        } catch {
            print("\(prefix) FAILED with error: \(error)")
            return false
        }
    }
}
