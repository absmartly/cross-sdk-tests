import Vapor
import ABSmartly
import Foundation
#if canImport(CryptoKit)
import CryptoKit
#endif
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import PromiseKit

typealias HTTPResponse = Vapor.Response
typealias VaporApplication = Vapor.Application
typealias ABSmartlyApplication = ABSmartly.Application

extension Promise {
    func asyncValue() async throws -> T {
        return try await withCheckedThrowingContinuation { continuation in
            firstly {
                self
            }.done(on: DispatchQueue.global()) { value in
                continuation.resume(returning: value)
            }.catch(on: DispatchQueue.global()) { error in
                continuation.resume(throwing: error)
            }
        }
    }
}

// Keeps every event for the context's lifetime. Handlers slice the new events
// with `suffix(from: eventsBefore)`, so the array must only ever grow: evicting
// old events would shift indices and return the wrong window (or trap once
// `eventsBefore` exceeds the count). Memory is bounded by ContextManager, which
// caps live contexts and drops a context's collector with it.
class EventCollector: ContextEventLogger {
    private var storedEvents: [[String: Any]] = []
    private let lock = NSLock()

    var events: [[String: Any]] {
        lock.lock()
        defer { lock.unlock() }
        return storedEvents
    }

    func handleEvent(context: Context, event: ContextEventLoggerEvent) {
        let timestamp = Int64(Date().timeIntervalSince1970 * 1000)
        var eventRecord: [String: Any]

        switch event {
        case .ready(let data):
            eventRecord = [
                "type": "ready",
                "data": convertContextDataToDict(data),
                "timestamp": timestamp
            ]
        case .refresh(let data):
            eventRecord = [
                "type": "refresh",
                "data": convertContextDataToDict(data),
                "timestamp": timestamp
            ]
        case .publish(let publishEvent):
            eventRecord = [
                "type": "publish",
                "data": convertPublishEventToDict(publishEvent),
                "timestamp": timestamp
            ]
        case .exposure(let exposure):
            eventRecord = [
                "type": "exposure",
                "data": convertExposureToDict(exposure),
                "timestamp": timestamp
            ]
        case .goal(let goal):
            eventRecord = [
                "type": "goal",
                "data": convertGoalToDict(goal),
                "timestamp": timestamp
            ]
        case .finalize:
            eventRecord = [
                "type": "finalize",
                "data": NSNull(),
                "timestamp": timestamp
            ]
        case .error(let error):
            eventRecord = [
                "type": "error",
                "data": ["message": error.localizedDescription],
                "timestamp": timestamp
            ]
        }

        lock.lock()
        storedEvents.append(eventRecord)
        lock.unlock()
    }

    private func convertContextDataToDict(_ data: ContextData) -> [String: Any] {
        var dict: [String: Any] = [:]
        dict["experiments"] = data.experiments.map { exp in
            convertExperimentToDict(exp)
        }
        return dict
    }

    private func convertExperimentToDict(_ exp: Experiment) -> [String: Any] {
        var dict: [String: Any] = [:]
        dict["id"] = exp.id
        dict["name"] = exp.name
        dict["unitType"] = exp.unitType
        dict["iteration"] = exp.iteration
        dict["seedHi"] = exp.seedHi
        dict["seedLo"] = exp.seedLo
        dict["split"] = exp.split
        dict["trafficSeedHi"] = exp.trafficSeedHi
        dict["trafficSeedLo"] = exp.trafficSeedLo
        dict["trafficSplit"] = exp.trafficSplit
        dict["fullOnVariant"] = exp.fullOnVariant
        dict["applications"] = exp.applications?.map { app in
            ["name": app.name as Any? ?? NSNull()]
        }
        dict["variants"] = exp.variants.map { variant in
            ["name": variant.name as Any? ?? NSNull(), "config": variant.config as Any? ?? NSNull()]
        }
        dict["audience"] = exp.audience
        dict["audienceStrict"] = exp.audienceStrict
        if let customFieldValues = exp.customFieldValues {
            dict["customFieldValues"] = customFieldValues.map { cfv in
                ["name": cfv.name as Any? ?? NSNull(), "type": cfv.type as Any? ?? NSNull(), "value": cfv.value as Any? ?? NSNull()]
            }
        }
        return dict
    }

    private func convertPublishEventToDict(_ event: PublishEvent) -> [String: Any] {
        var dict: [String: Any] = [:]
        dict["hashed"] = event.hashed
        dict["publishedAt"] = event.publishedAt
        dict["units"] = event.units.map { unit in
            ["type": unit.type as Any? ?? NSNull(), "uid": unit.uid as Any? ?? NSNull()]
        }
        dict["exposures"] = event.exposures.map { exp in
            convertExposureToDict(exp)
        }
        dict["goals"] = event.goals.map { goal in
            convertGoalToDict(goal)
        }
        dict["attributes"] = event.attributes.map { attr in
            ["name": attr.name as Any? ?? NSNull(), "value": jsonToAny(attr.value), "setAt": attr.setAt]
        }
        return dict
    }

    private func convertExposureToDict(_ exposure: Exposure) -> [String: Any] {
        var dict: [String: Any] = [:]
        dict["id"] = exposure.id
        dict["name"] = exposure.name
        // An unknown experiment exposes with no unit; the wire contract carries
        // that as an explicit null, not an omitted member.
        dict["unit"] = exposure.unit as Any? ?? NSNull()
        dict["variant"] = exposure.variant
        dict["exposedAt"] = exposure.exposedAt
        dict["assigned"] = exposure.assigned
        dict["eligible"] = exposure.eligible
        dict["overridden"] = exposure.overridden
        dict["fullOn"] = exposure.fullOn
        dict["custom"] = exposure.custom
        dict["audienceMismatch"] = exposure.audienceMismatch
        return dict
    }

    private func convertGoalToDict(_ goal: GoalAchievement) -> [String: Any] {
        var dict: [String: Any] = [:]
        dict["name"] = goal.name
        dict["achievedAt"] = goal.achievedAt
        if let properties = goal.properties {
            var propsDict: [String: Any] = [:]
            for (key, value) in properties {
                propsDict[key] = jsonToAny(value)
            }
            dict["properties"] = propsDict
        }
        return dict
    }

    private func jsonToAny(_ json: JSON?) -> Any {
        guard let json = json else { return NSNull() }

        if let dict = json.dictionary {
            var result: [String: Any] = [:]
            for (key, value) in dict {
                result[key] = jsonToAny(value)
            }
            return result
        } else if let array = json.array {
            return array.map { jsonToAny($0) }
        } else if let string = json.string {
            return string
        } else if let bool = json.bool {
            return bool
        } else if let number = json.number {
            return number
        } else if json.null != nil {
            return NSNull()
        }

        return NSNull()
    }
}

// The ContextPublisher the SDK invokes. `/publishFail` arms a one-shot failure;
// otherwise the publish goes to `delegate` (the SDK's DefaultContextPublisher in
// e2e mode) or, in mock mode where there is no collector, succeeds without
// sending anything.
class CustomPublisher: ContextPublisher {
    private let lock = NSLock()
    private var shouldFail: Bool = false
    private let delegate: ContextPublisher?

    init(delegate: ContextPublisher? = nil) {
        self.delegate = delegate
    }

    func setShouldFail(_ value: Bool) {
        lock.lock()
        defer { lock.unlock() }
        shouldFail = value
    }

    func publish(event: PublishEvent) -> Promise<Void> {
        lock.lock()
        let failNow = shouldFail
        if failNow { shouldFail = false }
        lock.unlock()
        if failNow {
            return Promise(error: NSError(domain: "ABSmartly", code: -1, userInfo: [NSLocalizedDescriptionKey: "Publish failed"]))
        }
        if let delegate = delegate {
            return delegate.publish(event: event)
        }
        return Promise.value(())
    }
}

class DummyContextDataProvider: ContextDataProvider {
    func getContextData() -> Promise<ContextData> {
        return Promise.value(ContextData(experiments: []))
    }
}

// Delays the SDK's own data provider by `throttleMs` for the initial load, then
// lets it fetch. The fetch itself (URL `{endpoint}/context`, query, decoding,
// HTTP errors) is the SDK's DefaultClient path, exactly as in the unthrottled
// endpoint mode. Later fetches (refresh) are not delayed.
class DeferredContextDataProvider: ContextDataProvider {
    private let inner: ContextDataProvider
    private let throttleMs: Int
    private let lock = NSLock()
    private var delayed = false

    init(inner: ContextDataProvider, throttleMs: Int) {
        self.inner = inner
        self.throttleMs = throttleMs
    }

    func getContextData() -> Promise<ContextData> {
        lock.lock()
        let delayThisCall = !delayed
        delayed = true
        lock.unlock()
        if !delayThisCall {
            return inner.getContextData()
        }
        return Promise { seal in
            DispatchQueue.global().asyncAfter(deadline: .now() + .milliseconds(self.throttleMs)) {
                self.inner.getContextData().done(on: DispatchQueue.global()) { data in
                    seal.fulfill(data)
                }.catch(on: DispatchQueue.global()) { error in
                    seal.reject(error)
                }
            }
        }
    }
}

class ContextStorage {
    let context: Context
    let eventCollector: EventCollector
    let sdk: ABSmartlySDK
    let publisher: CustomPublisher?

    init(context: Context, eventCollector: EventCollector, sdk: ABSmartlySDK, publisher: CustomPublisher? = nil) {
        self.context = context
        self.eventCollector = eventCollector
        self.sdk = sdk
        self.publisher = publisher
    }
}

class ContextManager {
    private var contexts: [String: ContextStorage] = [:]
    private var contextOrder: [String] = []
    private let lock = NSLock()
    private let maxContexts = 50

    func store(contextId: String, context: Context, collector: EventCollector, sdk: ABSmartlySDK, publisher: CustomPublisher? = nil) {
        var toEvict: [ContextStorage] = []

        lock.lock()
        while contexts.count >= maxContexts && !contextOrder.isEmpty {
            let oldestId = contextOrder.removeFirst()
            if let oldStorage = contexts.removeValue(forKey: oldestId) {
                toEvict.append(oldStorage)
            }
        }
        contexts[contextId] = ContextStorage(context: context, eventCollector: collector, sdk: sdk, publisher: publisher)
        contextOrder.append(contextId)
        lock.unlock()

        for storage in toEvict {
            if !storage.context.isClosed() {
                _ = storage.context.close()
            }
            _ = storage.sdk.close()
        }
    }

    func get(contextId: String) -> ContextStorage? {
        lock.lock()
        defer { lock.unlock() }
        return contexts[contextId]
    }

    func remove(contextId: String) {
        lock.lock()
        defer { lock.unlock() }
        contexts.removeValue(forKey: contextId)
        if let index = contextOrder.firstIndex(of: contextId) {
            contextOrder.remove(at: index)
        }
    }

    var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return contexts.count
    }
}

// Vapor serves requests on several event loops, so the payload map needs a lock.
class PayloadStore {
    private var payloads: [String: Data] = [:]
    private let lock = NSLock()

    subscript(id: String) -> Data? {
        get {
            lock.lock()
            defer { lock.unlock() }
            return payloads[id]
        }
        set {
            lock.lock()
            defer { lock.unlock() }
            payloads[id] = newValue
        }
    }
}

let contextManager = ContextManager()
let payloadStore = PayloadStore()

let payloadNotFoundBody: [String: Any] = ["error": "payload not found"]


func requestBody(_ req: Request) throws -> JSON {
    guard let buffer = req.body.data, buffer.readableBytes > 0 else { return JSON([String: Any]()) }
    return try JSON(data: Data(buffer.readableBytesView))
}

func reply(_ object: [String: Any], status: HTTPResponseStatus = .ok) throws -> HTTPResponse {
    return HTTPResponse(status: status, body: .init(data: try JSONSerialization.data(withJSONObject: object, options: [])))
}

func lookup(_ req: Request) throws -> ContextStorage {
    guard let contextId = req.parameters.get("contextId"),
          let storage = contextManager.get(contextId: contextId) else {
        throw Abort(.notFound, reason: "Context not found")
    }
    return storage
}

func call(_ storage: ContextStorage, _ body: () async throws -> Any?) async throws -> HTTPResponse {
    let before = storage.eventCollector.events.count
    do {
        let value = try await body()
        let events = Array(storage.eventCollector.events.suffix(from: before))
        return try reply(["result": sanitizeForJSON(value), "events": sanitizeForJSON(events)])
    } catch {
        let events = Array(storage.eventCollector.events.suffix(from: before))
        return try reply(["error": error.localizedDescription, "events": sanitizeForJSON(events)], status: .badRequest)
    }
}

func errorReply(_ message: String, status: HTTPResponseStatus = .badRequest) throws -> HTTPResponse {
    return try reply(["error": message, "events": [] as [Any]], status: status)
}

func makeConfig(_ request: JSON, logger: EventCollector) -> ContextConfig {
    let config = ContextConfig()
    for (unitType, uid) in request["units"].dictionaryValue {
        config.setUnit(unitType: unitType, value: uid)
    }
    config.eventLogger = logger
    config.publishDelay = request["options"]["publishDelay"].double ?? -1
    config.refreshInterval = request["options"]["refreshPeriod"].double ?? 0
    return config
}

func contextResult(_ contextId: String, _ context: Context, _ collector: EventCollector) throws -> HTTPResponse {
    return try reply([
        "result": [
            "contextId": contextId,
            "ready": context.isReady(),
            "failed": context.isFailed(),
            "finalized": context.isFinalized()
        ],
        "events": sanitizeForJSON(collector.events)
    ])
}

func routes(_ app: VaporApplication) throws {
    app.get("health") { _ -> HTTPResponse in
        return try reply(["status": "healthy", "sdk": "swift", "version": "1.0.0"])
    }

    app.get("capabilities") { _ -> HTTPResponse in
        return try reply([
            "diagnostics": true,
            "diagnosticsUtf8": true,
            "diagnosticsBase64": true,
            "diagnosticsTypeChecks": false,
            "diagnosticsIsPromise": false,
            "attrsSeq": true,
            "publishFail": true,
            "variableKeysMap": true,
            "globalCustomFieldKeys": true,
            "getUnits": true,
            "getAttributes": true,
            "readyError": true
        ])
    }

    // hashUnit, utf8Bytes and base64UrlNoPadding call the SDK's Hashing helpers.
    // The SDK has no isObject/isNumeric/isPromise (JavaScript type checks), so
    // those are not answered.
    app.post("diagnostic") { req -> HTTPResponse in
        let request = try requestBody(req)
        let value = request["value"]
        let result: Any
        switch request["operation"].stringValue {
        case "hashUnit":
            result = value.type == .number ? Hashing.hash(value.doubleValue) : Hashing.hash(value.stringValue)
        case "base64UrlNoPadding":
            result = Hashing.base64UrlNoPadding(Data(value.stringValue.utf8))
        case "utf8Bytes":
            result = Hashing.utf8Bytes(value.stringValue).map(Int.init)
        default:
            return try errorReply("Unsupported diagnostic operation: \(request["operation"].stringValue)")
        }
        return try reply(["result": result, "events": [] as [Any]])
    }

    app.put("context_payload", ":payloadId") { req -> HTTPResponse in
        guard let payloadId = req.parameters.get("payloadId") else { throw Abort(.badRequest) }
        let request = try requestBody(req)
        payloadStore[payloadId] = try request["data"].rawData()
        return try reply(["success": true])
    }

    let servePayload: @Sendable (Request) async throws -> HTTPResponse = { req in
        guard let payloadId = req.parameters.get("payloadId") else { throw Abort(.badRequest) }
        if let throttle = req.query[Int.self, at: "throttle"], throttle > 0 {
            try await Task.sleep(nanoseconds: UInt64(throttle) * 1_000_000)
        }
        guard let data = payloadStore[payloadId] else {
            return try reply(payloadNotFoundBody, status: .notFound)
        }
        return HTTPResponse(status: .ok, body: .init(data: data))
    }
    app.get("context_payload", ":payloadId", use: servePayload)
    app.get("context_payload", ":payloadId", "context", use: servePayload)

    app.post("context") { req async throws -> HTTPResponse in
        let request = try requestBody(req)
        let contextId = "ctx-\(Date().timeIntervalSince1970)-\(UUID().uuidString)"
        let eventCollector = EventCollector()

        if request["mode"].string == "e2e" {
            let env = ProcessInfo.processInfo.environment
            guard let e2eEndpoint = env["ABSMARTLY_E2E_ENDPOINT"],
                  let e2eApiKey = env["ABSMARTLY_E2E_API_KEY"],
                  let e2eApp = env["ABSMARTLY_E2E_APPLICATION"],
                  let e2eEnv = env["ABSMARTLY_E2E_ENVIRONMENT"] else {
                return try errorReply("e2e mode not configured", status: .notImplemented)
            }
            let client = try DefaultClient(config: ClientConfig(
                apiKey: e2eApiKey, application: e2eApp, endpoint: e2eEndpoint, environment: e2eEnv))
            let publisher = CustomPublisher(delegate: DefaultContextPublisher(client: client))
            let sdk = try ABSmartlySDK(config: ABSmartlyConfig(
                contextDataProvider: nil, contextPublisher: publisher, contextEventLogger: eventCollector,
                variableParser: nil, scheduler: nil, client: client))
            let config = makeConfig(request, logger: eventCollector)
            config.publishDelay = -1
            config.refreshInterval = 0
            let context = sdk.createContext(config: config)
            _ = try await context.waitUntilReady().asyncValue()
            for (key, value) in request["attributes"].dictionaryValue {
                do { try context.setAttribute(name: key, value: value) } catch {
                    return try reply(["error": error.localizedDescription, "events": sanitizeForJSON(eventCollector.events)], status: .badRequest)
                }
            }
            contextManager.store(contextId: contextId, context: context, collector: eventCollector, sdk: sdk, publisher: publisher)
            return try contextResult(contextId, context, eventCollector)
        }

        let publisher = CustomPublisher()
        let config = makeConfig(request, logger: eventCollector)
        let payloadThrottle = request["options"]["payloadThrottle"].intValue
        let context: Context
        let sdk: ABSmartlySDK

        if request["data"].exists() && request["data"].type != .null {
            if payloadThrottle > 0 {
                return try errorReply("payloadThrottle needs an endpoint, not inline data")
            }
            let contextData: ContextData
            do {
                contextData = try JSONDecoder().decode(ContextData.self, from: try request["data"].rawData())
            } catch {
                return try errorReply("Invalid context data: \(error.localizedDescription)")
            }
            sdk = try ABSmartlySDK(config: ABSmartlyConfig(
                contextDataProvider: DummyContextDataProvider(), contextPublisher: publisher,
                contextEventLogger: eventCollector, variableParser: nil, scheduler: nil, client: nil))
            context = sdk.createContextWithData(config: config, contextData: contextData)
        } else if var endpoint = request["endpoint"].string {
            if let range = endpoint.range(of: "localhost:\\d+", options: .regularExpression) {
                endpoint = endpoint.replacingCharacters(in: range, with: "127.0.0.1:3000")
            }
            // The SDK's DefaultClient fetches `GET {endpoint}/context`; a throttle only delays that fetch.
            let client = try DefaultClient(config: ClientConfig(
                apiKey: "test-api-key", application: "test-app", endpoint: endpoint, environment: "test-env"))
            let provider: ContextDataProvider? = payloadThrottle > 0
                ? DeferredContextDataProvider(inner: DefaultContextDataProvider(client: client), throttleMs: payloadThrottle)
                : nil
            sdk = try ABSmartlySDK(config: ABSmartlyConfig(
                contextDataProvider: provider, contextPublisher: publisher, contextEventLogger: eventCollector,
                variableParser: nil, scheduler: nil, client: client))
            context = sdk.createContext(config: config)
            if payloadThrottle == 0 {
                _ = try await context.waitUntilReady().asyncValue()
            }
        } else if request["failLoad"].boolValue {
            // A real fetch failure: the SDK's own client against a port nothing listens on.
            let client = try DefaultClient(config: ClientConfig(
                apiKey: "test-api-key", application: "test-app", endpoint: "http://127.0.0.1:1", environment: "test-env"))
            sdk = try ABSmartlySDK(config: ABSmartlyConfig(
                contextDataProvider: nil, contextPublisher: publisher, contextEventLogger: eventCollector,
                variableParser: nil, scheduler: nil, client: client))
            context = sdk.createContext(config: config)
            _ = try await context.waitUntilReady().asyncValue()
        } else {
            return try errorReply("Either data or endpoint must be provided")
        }

        contextManager.store(contextId: contextId, context: context, collector: eventCollector, sdk: sdk, publisher: publisher)
        return try contextResult(contextId, context, eventCollector)
    }

    app.post("context", ":contextId", "setUnit") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) {
            try storage.context.setUnit(unitType: request["unitType"].stringValue, value: request["uid"])
            return nil
        }
    }

    app.post("context", ":contextId", "getUnit") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) {
            storage.context.getUnitValue(unitType: request["unitType"].stringValue).map(jsonToAnyHelper)
        }
    }

    app.post("context", ":contextId", "getUnits") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) { storage.context.getUnitValues().mapValues(jsonToAnyHelper) }
    }

    app.post("context", ":contextId", "attribute") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) {
            try storage.context.setAttribute(name: request["name"].stringValue, value: request["value"])
            return nil
        }
    }

    app.post("context", ":contextId", "getAttribute") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) { storage.context.getAttribute(name: request["name"].stringValue).map(jsonToAnyHelper) }
    }

    app.post("context", ":contextId", "getAttributes") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) { storage.context.getAttributes().mapValues(jsonToAnyHelper) }
    }

    app.post("context", ":contextId", "treatment") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) { try storage.context.getTreatment(request["experimentName"].stringValue) }
    }

    app.post("context", ":contextId", "peek") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) { try storage.context.peekTreatment(request["experimentName"].stringValue) }
    }

    app.post("context", ":contextId", "variableValue") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        let defaultValue: JSON? = request["defaultValue"].exists() ? request["defaultValue"] : nil
        return try await call(storage) {
            try storage.context.getVariableValue(request["key"].stringValue, defaultValue: defaultValue).map(jsonToAnyHelper)
        }
    }

    app.post("context", ":contextId", "peekVariableValue") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        let defaultValue: JSON? = request["defaultValue"].exists() ? request["defaultValue"] : nil
        return try await call(storage) {
            try storage.context.peekVariableValue(request["key"].stringValue, defaultValue: defaultValue).map(jsonToAnyHelper)
        }
    }

    app.post("context", ":contextId", "track") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        let goalName = request["goalName"].stringValue
        let properties = request["properties"]
        var props: [String: JSON]? = nil
        switch properties.type {
        case .dictionary:
            props = properties.dictionaryValue
        case .null, .unknown:
            props = nil
        default:
            // track(_:properties: [String: JSON]?) cannot take a non-object; the JSON
            // value is rejected at the type boundary with the SDK-wide message.
            return try errorReply("Goal '\(goalName)' properties must be of type object.")
        }
        return try await call(storage) {
            try storage.context.track(goalName, properties: props)
            return nil
        }
    }

    let overrideHandler: @Sendable (Request) async throws -> HTTPResponse = { req in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) {
            storage.context.setOverride(experimentName: request["experimentName"].stringValue, variant: request["variant"].intValue)
            return nil
        }
    }
    app.post("context", ":contextId", "override", use: overrideHandler)
    app.post("context", ":contextId", "setOverride", use: overrideHandler)

    let customAssignmentHandler: @Sendable (Request) async throws -> HTTPResponse = { req in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) {
            try storage.context.setCustomAssignment(experimentName: request["experimentName"].stringValue, variant: request["variant"].intValue)
            return nil
        }
    }
    app.post("context", ":contextId", "customAssignment", use: customAssignmentHandler)
    app.post("context", ":contextId", "setCustomAssignment", use: customAssignmentHandler)

    app.post("context", ":contextId", "customFieldValue") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) {
            try storage.context.getCustomFieldValue(experimentName: request["experimentName"].stringValue, key: request["fieldName"].stringValue)
        }
    }

    app.post("context", ":contextId", "customFieldValueType") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) {
            try storage.context.getCustomFieldValueType(experimentName: request["experimentName"].stringValue, key: request["fieldName"].stringValue)
        }
    }

    app.post("context", ":contextId", "variableKeys") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) { Array(try storage.context.getVariableKeys().keys) }
    }

    app.post("context", ":contextId", "variableKeysMap") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) { try storage.context.getVariableKeys() }
    }

    let customFieldKeysHandler: @Sendable (Request) async throws -> HTTPResponse = { req in
        let storage = try lookup(req)
        return try await call(storage) { Array(try storage.context.getCustomFieldKeys()).sorted() }
    }
    app.post("context", ":contextId", "customFieldKeys", use: customFieldKeysHandler)
    app.post("context", ":contextId", "globalCustomFieldKeys", use: customFieldKeysHandler)

    app.get("context", ":contextId", "pending") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) { storage.context.getPendingCount() }
    }

    app.get("context", ":contextId", "isFinalized") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) { storage.context.isFinalized() }
    }

    app.get("context", ":contextId", "isReady") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) { storage.context.isReady() }
    }

    app.get("context", ":contextId", "isFailed") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) { storage.context.isFailed() }
    }

    app.get("context", ":contextId", "experiments") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) { try storage.context.getExperiments() }
    }

    app.post("context", ":contextId", "readyError") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) {
            storage.context.readyError().map { ["isError": true, "message": $0.localizedDescription] as [String: Any] }
        }
    }

    app.post("context", ":contextId", "publishFail") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) {
            storage.publisher?.setShouldFail(true)
            return nil
        }
    }

    app.post("context", ":contextId", "publish") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) {
            try await storage.context.publish().asyncValue()
            return nil
        }
    }

    app.post("context", ":contextId", "refresh") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) {
            try await storage.context.refresh().asyncValue()
            return nil
        }
    }

    app.post("context", ":contextId", "finalize") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) {
            try await storage.context.finalize().asyncValue()
            return nil
        }
    }

    app.delete("context", ":contextId") { req async throws -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }
        let before = storage.eventCollector.events.count
        var closeError: Error?
        if !storage.context.isFinalized() {
            do { try await storage.context.finalize().asyncValue() } catch { closeError = error }
        }
        _ = try? await storage.sdk.close().asyncValue()
        contextManager.remove(contextId: contextId)
        let events = sanitizeForJSON(Array(storage.eventCollector.events.suffix(from: before)))
        if let closeError = closeError {
            return try reply(["error": closeError.localizedDescription, "events": events], status: .badRequest)
        }
        return try reply(["result": "deleted", "events": events])
    }
}

func sanitizeForJSON(_ value: Any?) -> Any {
    guard let v = value else { return NSNull() }

    if v is NSNull {
        return NSNull()
    }

    if let num = v as? NSNumber {
        // Check if NSNumber is actually a boolean
        let objCType = String(cString: num.objCType)
        if objCType == "c" || objCType == "B" {
            return num.boolValue
        }
        return num
    }

    if let str = v as? String {
        return str
    }

    if let nsStr = v as? NSString {
        return nsStr as String
    }

    if let bool = v as? Bool {
        return bool
    }

    if let int = v as? Int {
        return int
    }

    if let double = v as? Double {
        return double
    }

    if let arr = v as? [Any] {
        return arr.map { sanitizeForJSON($0) }
    }

    if let nsArr = v as? NSArray {
        var result: [Any] = []
        for item in nsArr {
            result.append(sanitizeForJSON(item))
        }
        return result
    }

    if let dict = v as? [String: Any] {
        return dict.mapValues { sanitizeForJSON($0) }
    }

    if let nsDict = v as? NSDictionary {
        var result: [String: Any] = [:]
        for (key, value) in nsDict {
            if let keyStr = key as? String {
                result[keyStr] = sanitizeForJSON(value)
            }
        }
        return result
    }

    if let json = v as? JSON {
        return jsonToAnyHelper(json)
    }

    let mirror = Mirror(reflecting: v)
    if mirror.displayStyle == .optional {
        if let unwrapped = mirror.children.first?.value {
            return sanitizeForJSON(unwrapped)
        }
        return NSNull()
    }

    let typeName = String(describing: type(of: v))
    if typeName.contains("JSON") {
        if let json = v as? JSON {
            return jsonToAnyHelper(json)
        }
    }

    return String(describing: v)
}

func jsonToAnyHelper(_ json: JSON) -> Any {
    if let dict = json.dictionary {
        var result: [String: Any] = [:]
        for (key, value) in dict {
            result[key] = jsonToAnyHelper(value)
        }
        return sanitizeForJSON(result)
    } else if let array = json.array {
        return sanitizeForJSON(array.map { jsonToAnyHelper($0) })
    } else if let string = json.string {
        return string
    } else if let bool = json.bool {
        return bool
    } else if let number = json.number {
        return number
    } else if json.null != nil {
        return NSNull()
    }

    return NSNull()
}
