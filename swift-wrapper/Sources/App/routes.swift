import Vapor
@_spi(Diagnostics) import ABsmartly
import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

typealias HTTPResponse = Vapor.Response
typealias VaporApplication = Vapor.Application

// MARK: - JSON helpers

/// A JSON value as Foundation objects, for JSONSerialization.
func jsonToAny(_ value: JSONValue?) -> Any {
    guard let value = value else { return NSNull() }
    switch value {
    case .null: return NSNull()
    case .bool(let b): return b
    case .int(let i): return i
    case .double(let d): return d
    case .string(let s): return s
    case .array(let items): return items.map { jsonToAny($0) }
    case .object(let object): return object.mapValues { jsonToAny($0) }
    }
}

/// An SDK model as Foundation objects, through its own Encodable conformance.
func encodedToAny<T: Encodable>(_ value: T) -> Any {
    guard let data = try? JSONEncoder().encode(value),
          let object = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]) else {
        return NSNull()
    }
    return object
}

// An unknown experiment exposes with no unit; the wire contract carries that as an explicit null,
// not an omitted member. A goal without properties carries no `properties` member, as in 1.x.
func exposureToAny(_ exposure: Exposure) -> Any {
    var dict = encodedToAny(exposure) as? [String: Any] ?? [:]
    if dict["unit"] == nil { dict["unit"] = NSNull() }
    return dict
}

func goalToAny(_ goal: Goal) -> Any {
    var dict = encodedToAny(goal) as? [String: Any] ?? [:]
    if dict["properties"] is NSNull { dict.removeValue(forKey: "properties") }
    return dict
}

func publishToAny(_ event: PublishEvent) -> Any {
    var dict = encodedToAny(event) as? [String: Any] ?? [:]
    dict["units"] = event.units.map { encodedToAny($0) }
    dict["exposures"] = event.exposures.map { exposureToAny($0) }
    dict["goals"] = event.goals.map { goalToAny($0) }
    dict["attributes"] = event.attributes.map { encodedToAny($0) }
    return dict
}

// MARK: - Event collection

// Keeps every event for the context's lifetime. Handlers slice the new events with
// `suffix(from: eventsBefore)`, so the array must only ever grow. Memory is bounded by
// ContextManager, which caps live contexts and drops a context's collector with it.
final class EventCollector: @unchecked Sendable {
    private var storedEvents: [[String: Any]] = []
    private let lock = NSLock()

    var events: [[String: Any]] {
        lock.lock()
        defer { lock.unlock() }
        return storedEvents
    }

    var logger: EventLogger {
        EventLogger { [self] _, event in self.handle(event) }
    }

    func handle(_ event: Context.Event) {
        let timestamp = Int64(Date().timeIntervalSince1970 * 1000)
        let record: [String: Any]
        switch event {
        case .ready(let data):
            record = ["type": "ready", "data": encodedToAny(data), "timestamp": timestamp]
        case .refresh(let data):
            record = ["type": "refresh", "data": encodedToAny(data), "timestamp": timestamp]
        case .publish(let publishEvent):
            record = ["type": "publish", "data": publishToAny(publishEvent), "timestamp": timestamp]
        case .exposure(let exposure):
            record = ["type": "exposure", "data": exposureToAny(exposure), "timestamp": timestamp]
        case .goal(let goal):
            record = ["type": "goal", "data": goalToAny(goal), "timestamp": timestamp]
        case .close:
            // The SDK's close event is the cross-SDK "finalize" event.
            record = ["type": "finalize", "data": NSNull(), "timestamp": timestamp]
        case .error(let error):
            record = ["type": "error", "data": ["message": error.localizedDescription], "timestamp": timestamp]
        }
        lock.lock()
        storedEvents.append(record)
        lock.unlock()
    }
}

// MARK: - Transport

/// The SDK's own URLSessionTransport, plus the wrapper's test controls:
/// - `/publishFail` arms a one-shot publish failure: the next publish fails, including the SDK's own
///   retries of it (they resend the same body), so the SDK sees one failed publish, as 1.x's
///   publisher hook did;
/// - without `publishToEndpoint` there is no collector, so publishes succeed without being sent;
/// - `payloadThrottle` delays the first context fetch;
/// - inline-data contexts have no endpoint, so a refresh fetches an empty context, as 1.x's
///   DummyContextDataProvider did.
final class WrapperTransport: Transport, @unchecked Sendable {
    private let inner: URLSessionTransport
    private let lock = NSLock()
    private var shouldFail = false
    private var failingBody: Data?
    private var throttled: Bool
    private let throttleMs: Int
    private let publishToEndpoint: Bool
    private let inlineData: Bool

    init(publishToEndpoint: Bool, throttleMs: Int = 0, inlineData: Bool = false) {
        self.inner = URLSessionTransport(timeout: 3)
        self.publishToEndpoint = publishToEndpoint
        self.throttleMs = throttleMs
        self.throttled = throttleMs > 0
        self.inlineData = inlineData
    }

    func setShouldFail(_ value: Bool) {
        lock.lock()
        defer { lock.unlock() }
        shouldFail = value
    }

    func send(_ request: HTTPRequest) async throws -> ABsmartly.HTTPResponse {
        if request.method == "GET" {
            if inlineData {
                return ABsmartly.HTTPResponse(statusCode: 200, body: Data(#"{"experiments":[]}"#.utf8))
            }
            let delay = lock.withLock { () -> Bool in
                let delay = throttled
                throttled = false
                return delay
            }
            if delay {
                try await Task.sleep(nanoseconds: UInt64(throttleMs) * 1_000_000)
            }
            return try await inner.send(request)
        }

        let failNow = lock.withLock { () -> Bool in
            if shouldFail {
                shouldFail = false
                failingBody = request.body
                return true
            }
            if let failing = failingBody {
                if failing == request.body {
                    return true
                }
                failingBody = nil
            }
            return false
        }
        if failNow {
            throw NSError(domain: "ABsmartly", code: -1, userInfo: [NSLocalizedDescriptionKey: "Publish failed"])
        }
        if publishToEndpoint {
            return try await inner.send(request)
        }
        return ABsmartly.HTTPResponse(statusCode: 200, body: Data("{}".utf8))
    }
}

// MARK: - Storage

final class ContextStorage: @unchecked Sendable {
    let context: Context
    let eventCollector: EventCollector
    let transport: WrapperTransport

    init(context: Context, eventCollector: EventCollector, transport: WrapperTransport) {
        self.context = context
        self.eventCollector = eventCollector
        self.transport = transport
    }
}

final class ContextManager: @unchecked Sendable {
    private var contexts: [String: ContextStorage] = [:]
    private var contextOrder: [String] = []
    private let lock = NSLock()
    private let maxContexts = 50

    func store(contextId: String, storage: ContextStorage) {
        var toEvict: [ContextStorage] = []

        lock.lock()
        while contexts.count >= maxContexts && !contextOrder.isEmpty {
            let oldestId = contextOrder.removeFirst()
            if let oldStorage = contexts.removeValue(forKey: oldestId) {
                toEvict.append(oldStorage)
            }
        }
        contexts[contextId] = storage
        contextOrder.append(contextId)
        lock.unlock()

        for storage in toEvict where storage.context.state != .closed {
            let context = storage.context
            Task { try? await context.close() }
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
}

// Vapor serves requests on several event loops, so the payload map needs a lock.
final class PayloadStore: @unchecked Sendable {
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

// Locked like PayloadStore.
final class CapturedRequests: @unchecked Sendable {
    private var requests: [String: [[String: Any]]] = [:]
    private let lock = NSLock()

    func record(_ req: Request, payloadId: String) {
        var headers: [String: String] = [:]
        for (name, value) in req.headers {
            headers[name] = headers[name].map { "\($0), \(value)" } ?? value
        }
        lock.lock()
        defer { lock.unlock() }
        requests[payloadId, default: []].append(["method": req.method.rawValue, "headers": headers])
    }

    func list(_ payloadId: String) -> [[String: Any]] {
        lock.lock()
        defer { lock.unlock() }
        return requests[payloadId] ?? []
    }
}

let contextManager = ContextManager()
let payloadStore = PayloadStore()
let capturedRequests = CapturedRequests()

let payloadNotFoundBody: [String: Any] = ["error": "payload not found"]

// MARK: - Request helpers

func requestBody(_ req: Request) throws -> JSONValue {
    guard let buffer = req.body.data, buffer.readableBytes > 0 else { return .object([:]) }
    return try JSONDecoder().decode(JSONValue.self, from: Data(buffer.readableBytesView))
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
        return try reply(["result": value ?? NSNull(), "events": events])
    } catch {
        let events = Array(storage.eventCollector.events.suffix(from: before))
        return try reply(["error": error.localizedDescription, "events": events], status: .badRequest)
    }
}

func errorReply(_ message: String, status: HTTPResponseStatus = .badRequest) throws -> HTTPResponse {
    return try reply(["error": message, "events": [] as [Any]], status: status)
}

/// The scenario's units: string UIDs go to context creation, numeric ones are set right after it
/// with `setUnit(_:to:)` (Int when integral, else Double), which keeps them numbers.
struct ScenarioUnits {
    var strings: [UnitType: String] = [:]
    var numbers: [(UnitType, JSONValue)] = []

    init(_ units: JSONValue?) {
        for (unitType, uid) in units?.objectValue ?? [:] {
            switch uid {
            case .string(let s): strings[UnitType(unitType)] = s
            case .int, .double: numbers.append((UnitType(unitType), uid))
            default: strings[UnitType(unitType)] = uid.description
            }
        }
    }

    func apply(to context: Context) throws {
        for (unitType, uid) in numbers {
            try setNumericUnit(context, unitType, uid)
        }
    }
}

func setNumericUnit(_ context: Context, _ unitType: UnitType, _ uid: JSONValue) throws {
    switch uid {
    case .int(let i):
        if let int = Int(exactly: i) { try context.setUnit(unitType, to: int) } else { try context.setUnit(unitType, to: Double(i)) }
    case .double(let d):
        try context.setUnit(unitType, to: d)
    default:
        break
    }
}

func contextOptions(_ request: JSONValue, logger: EventCollector) -> Context.Options {
    Context.Options(
        publishDelay: request["options"]?["publishDelay"]?.doubleValue ?? -1,
        refreshInterval: request["options"]?["refreshPeriod"]?.doubleValue ?? 0,
        eventLogger: logger.logger)
}

func contextResult(_ contextId: String, _ context: Context, _ collector: EventCollector) throws -> HTTPResponse {
    let state = context.state
    var failed = false
    if case .failed = state { failed = true }
    return try reply([
        "result": [
            "contextId": contextId,
            "ready": state.isSettled,
            "failed": failed,
            "finalized": state == .closed,
        ],
        "events": collector.events,
    ])
}

func makeClient(
    endpoint: String, application: String, environment: String, transport: WrapperTransport, apiKey: String = "test-api-key"
) throws -> ABsmartlyClient {
    guard let url = URL(string: endpoint) else {
        throw ABsmartlyError.invalidConfiguration("Missing Endpoint configuration")
    }
    var configuration = ABsmartlyClient.Configuration(
        endpoint: url, apiKey: apiKey, application: application, environment: .init(rawValue: environment))
    configuration.transport = transport
    return try ABsmartlyClient(configuration: configuration)
}

// MARK: - Routes

func routes(_ app: VaporApplication) throws {
    app.get("health") { _ -> HTTPResponse in
        return try reply(["status": "healthy", "sdk": "swift", "version": "2.0.0"])
    }

    app.get("capabilities") { _ -> HTTPResponse in
        return try reply([
            "diagnostics": true,
            "diagnosticsUtf8": true,
            "attrsSeq": true,
            "publishFail": true,
            "variableKeysMap": true,
            "globalCustomFieldKeys": true,
            "getUnits": true,
            "getAttributes": true,
            "readyError": true,
            "numericUnitIds": true,
            "publishThrowsOnFailure": true,
            "trackRejectsNonObjectProperties": true,
            "refreshNoOpOnFailedContext": true,
            "throwsOnMisuse": false,
            "singleFinalizeEvent": true,
            "semverNumericOperands": true,
            "assignmentRules": true,
            "semverOperators": true,
            "xAgentHeader": true,
            "clientRejectsBlankApplication": true,
            "clientAcceptsBlankApplication": false,
        ])
    }

    app.post("diagnostic") { req -> HTTPResponse in
        let request = try requestBody(req)
        let value = request["value"] ?? .null
        let result: Any
        switch request["operation"]?.stringValue ?? "" {
        case "hashUnit":
            if let number = value.doubleValue {
                result = Diagnostics.hashUnit(number)
            } else {
                result = Diagnostics.hashUnit(value.stringValue ?? "")
            }
        case "utf8Bytes":
            result = Diagnostics.utf8Bytes(value.stringValue ?? "").map(Int.init)
        default:
            return try errorReply("Unsupported diagnostic operation: \(request["operation"]?.stringValue ?? "")")
        }
        return try reply(["result": result, "events": [] as [Any]])
    }

    app.put("context_payload", ":payloadId") { req -> HTTPResponse in
        guard let payloadId = req.parameters.get("payloadId") else { throw Abort(.badRequest) }
        let request = try requestBody(req)
        payloadStore[payloadId] = try JSONEncoder().encode(request["data"] ?? .null)
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
    app.get("context_payload", ":payloadId", "context") { req async throws -> HTTPResponse in
        if let payloadId = req.parameters.get("payloadId") {
            capturedRequests.record(req, payloadId: payloadId)
        }
        return try await servePayload(req)
    }

    // Mock collector - with publishToEndpoint the SDK publishes PUT {endpoint}/context here.
    app.on(.PUT, "context_payload", ":payloadId", "context", body: .collect(maxSize: "10mb")) { req -> HTTPResponse in
        guard let payloadId = req.parameters.get("payloadId") else { throw Abort(.badRequest) }
        capturedRequests.record(req, payloadId: payloadId)
        return try reply([:])
    }

    app.get("context_payload", ":payloadId", "requests") { req -> HTTPResponse in
        guard let payloadId = req.parameters.get("payloadId") else { throw Abort(.badRequest) }
        return try reply(["requests": capturedRequests.list(payloadId)])
    }

    app.post("context") { req async throws -> HTTPResponse in
        let request = try requestBody(req)
        let contextId = "ctx-\(Date().timeIntervalSince1970)-\(UUID().uuidString)"
        let eventCollector = EventCollector()
        let units = ScenarioUnits(request["units"])
        let options = contextOptions(request, logger: eventCollector)

        if request["mode"]?.stringValue == "e2e" {
            let env = ProcessInfo.processInfo.environment
            guard let e2eEndpoint = env["ABSMARTLY_E2E_ENDPOINT"],
                  let e2eApiKey = env["ABSMARTLY_E2E_API_KEY"],
                  let e2eApp = env["ABSMARTLY_E2E_APPLICATION"],
                  let e2eEnv = env["ABSMARTLY_E2E_ENVIRONMENT"] else {
                return try errorReply("e2e mode not configured", status: .notImplemented)
            }
            let transport = WrapperTransport(publishToEndpoint: true)
            let client = try makeClient(
                endpoint: e2eEndpoint, application: e2eApp, environment: e2eEnv, transport: transport, apiKey: e2eApiKey)
            var e2eOptions = options
            e2eOptions.publishDelay = -1
            e2eOptions.refreshInterval = 0
            let context = client.makeContext(units: units.strings, options: e2eOptions)
            try units.apply(to: context)
            try? await context.ready()
            for (key, value) in request["attributes"]?.objectValue ?? [:] {
                context.setAttribute(key, to: value)
            }
            contextManager.store(
                contextId: contextId, storage: ContextStorage(context: context, eventCollector: eventCollector, transport: transport))
            return try contextResult(contextId, context, eventCollector)
        }

        // Optional client config (the 2026-11 system-attributes round): a scenario can set the
        // application/environment names so audiences can target them. When the `client` field is
        // omitted, the fixed test-app/test-env defaults keep every pre-2026-11 scenario unchanged.
        // An explicitly supplied application is forwarded as-is so the SDK validates it. The SDK
        // rejects an empty environment, so the no-environment case (clientNoEnvironment) is a
        // reviewed exception and SKIPs.
        let client = request["client"]
        let clientApplication: String
        if let value = client?["application"] {
            guard let name = value.stringValue else {
                return try errorReply("Invalid 'application' in client argument")
            }
            clientApplication = name
        } else {
            clientApplication = "test-app"
        }
        let clientEnvironment: String
        if let value = client?["environment"]?.stringValue, !value.isEmpty {
            clientEnvironment = value
        } else if let client = client, !client.isNull {
            return try errorReply("client.environment is required by swift-sdk; no-environment contexts are not supported")
        } else {
            clientEnvironment = "test-env"
        }

        let publishToEndpoint = request["publishToEndpoint"]?.boolValue == true && request["endpoint"]?.stringValue != nil
        let payloadThrottle = request["options"]?["payloadThrottle"]?.intValue ?? 0
        let context: Context
        let transport: WrapperTransport

        if let data = request["data"], !data.isNull {
            if payloadThrottle > 0 {
                return try errorReply("payloadThrottle needs an endpoint, not inline data")
            }
            let contextData: ContextData
            do {
                contextData = try JSONDecoder().decode(ContextData.self, from: try JSONEncoder().encode(data))
            } catch {
                return try errorReply("Invalid context data: \(error.localizedDescription)")
            }
            // The requested client names apply to inline-data contexts too, so an audience on
            // application/environment matches here as in endpoint mode. Inline data never fetches.
            transport = WrapperTransport(publishToEndpoint: false, inlineData: true)
            let sdk: ABsmartlyClient
            do {
                sdk = try makeClient(
                    endpoint: "http://127.0.0.1:3000", application: clientApplication, environment: clientEnvironment,
                    transport: transport)
            } catch {
                return try errorReply(error.localizedDescription)
            }
            context = sdk.context(units: units.strings, options: options, data: contextData)
            try units.apply(to: context)
        } else if var endpoint = request["endpoint"]?.stringValue {
            if let range = endpoint.range(of: "localhost:\\d+", options: .regularExpression) {
                endpoint = endpoint.replacingCharacters(in: range, with: "127.0.0.1:3000")
            }
            // The SDK fetches `GET {endpoint}/context` itself; a throttle only delays that fetch.
            transport = WrapperTransport(publishToEndpoint: publishToEndpoint, throttleMs: payloadThrottle)
            let sdk: ABsmartlyClient
            do {
                sdk = try makeClient(
                    endpoint: endpoint, application: clientApplication, environment: clientEnvironment, transport: transport)
            } catch {
                return try errorReply(error.localizedDescription)
            }
            context = sdk.makeContext(units: units.strings, options: options)
            try units.apply(to: context)
            if payloadThrottle == 0 {
                try? await context.ready()
            }
        } else if request["failLoad"]?.boolValue == true {
            // A real fetch failure: the SDK's own transport against a port nothing listens on.
            transport = WrapperTransport(publishToEndpoint: false)
            let sdk: ABsmartlyClient
            do {
                sdk = try makeClient(
                    endpoint: "http://127.0.0.1:1", application: clientApplication, environment: clientEnvironment,
                    transport: transport)
            } catch {
                return try errorReply(error.localizedDescription)
            }
            context = sdk.makeContext(units: units.strings, options: options)
            try units.apply(to: context)
            try? await context.ready()
        } else {
            return try errorReply("Either data or endpoint must be provided")
        }

        contextManager.store(
            contextId: contextId, storage: ContextStorage(context: context, eventCollector: eventCollector, transport: transport))
        return try contextResult(contextId, context, eventCollector)
    }

    app.post("context", ":contextId", "setUnit") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) {
            let unitType = UnitType(request["unitType"]?.stringValue ?? "")
            let uid = request["uid"] ?? .null
            if uid.doubleValue != nil {
                try setNumericUnit(storage.context, unitType, uid)
            } else {
                try storage.context.setUnit(unitType, to: uid.stringValue ?? "")
            }
            return nil
        }
    }

    app.post("context", ":contextId", "getUnit") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) {
            storage.context.unitValues[UnitType(request["unitType"]?.stringValue ?? "")].map { jsonToAny($0) }
        }
    }

    app.post("context", ":contextId", "getUnits") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) {
            Dictionary(uniqueKeysWithValues: storage.context.unitValues.map { ($0.key.rawValue, jsonToAny($0.value)) })
        }
    }

    app.post("context", ":contextId", "attribute") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) {
            storage.context.setAttribute(request["name"]?.stringValue ?? "", to: request["value"] ?? .null)
            return nil
        }
    }

    app.post("context", ":contextId", "getAttribute") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) { storage.context.attribute(request["name"]?.stringValue ?? "").map { jsonToAny($0) } }
    }

    app.post("context", ":contextId", "getAttributes") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) { storage.context.attributes.mapValues { jsonToAny($0) } }
    }

    app.post("context", ":contextId", "treatment") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) { storage.context.treatment(for: request["experimentName"]?.stringValue ?? "") }
    }

    app.post("context", ":contextId", "peek") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) {
            storage.context.treatment(for: request["experimentName"]?.stringValue ?? "", exposing: false)
        }
    }

    app.post("context", ":contextId", "variableValue") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) {
            jsonToAny(storage.context.variableValue(request["key"]?.stringValue ?? "") ?? request["defaultValue"])
        }
    }

    app.post("context", ":contextId", "peekVariableValue") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) {
            jsonToAny(storage.context.variableValue(request["key"]?.stringValue ?? "", exposing: false) ?? request["defaultValue"])
        }
    }

    app.post("context", ":contextId", "track") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        let goalName = request["goalName"]?.stringValue ?? ""
        let props: [String: JSONValue]?
        switch request["properties"] {
        case .object(let object)?:
            props = object
        case .null?, nil:
            props = nil
        default:
            // track(_:properties: [String: JSONValue]?) cannot take a non-object; the value is
            // rejected at the type boundary with the SDK-wide message.
            return try errorReply("Goal '\(goalName)' properties must be of type object.")
        }
        return try await call(storage) {
            storage.context.track(goalName, properties: props)
            return nil
        }
    }

    let overrideHandler: @Sendable (Request) async throws -> HTTPResponse = { req in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) {
            storage.context.overrides[request["experimentName"]?.stringValue ?? ""] = request["variant"]?.intValue ?? 0
            return nil
        }
    }
    app.post("context", ":contextId", "override", use: overrideHandler)
    app.post("context", ":contextId", "setOverride", use: overrideHandler)

    let customAssignmentHandler: @Sendable (Request) async throws -> HTTPResponse = { req in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) {
            storage.context.customAssignments[request["experimentName"]?.stringValue ?? ""] = request["variant"]?.intValue ?? 0
            return nil
        }
    }
    app.post("context", ":contextId", "customAssignment", use: customAssignmentHandler)
    app.post("context", ":contextId", "setCustomAssignment", use: customAssignmentHandler)

    app.post("context", ":contextId", "customFieldValue") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) {
            storage.context.customFieldValue(
                request["fieldName"]?.stringValue ?? "", in: request["experimentName"]?.stringValue ?? ""
            ).map { jsonToAny($0) }
        }
    }

    app.post("context", ":contextId", "customFieldValueType") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        let request = try requestBody(req)
        return try await call(storage) {
            storage.context.customFieldType(request["fieldName"]?.stringValue ?? "", in: request["experimentName"]?.stringValue ?? "")
        }
    }

    app.post("context", ":contextId", "variableKeys") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) { Array(storage.context.variableKeys.keys) }
    }

    app.post("context", ":contextId", "variableKeysMap") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) { storage.context.variableKeys }
    }

    let customFieldKeysHandler: @Sendable (Request) async throws -> HTTPResponse = { req in
        let storage = try lookup(req)
        return try await call(storage) { Array(storage.context.customFieldKeys).sorted() }
    }
    app.post("context", ":contextId", "customFieldKeys", use: customFieldKeysHandler)
    app.post("context", ":contextId", "globalCustomFieldKeys", use: customFieldKeysHandler)

    app.get("context", ":contextId", "pending") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) { storage.context.pendingEventCount }
    }

    app.get("context", ":contextId", "isFinalized") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) { storage.context.state == .closed }
    }

    app.get("context", ":contextId", "isReady") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) { storage.context.state.isSettled }
    }

    app.get("context", ":contextId", "isFailed") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) {
            if case .failed = storage.context.state { return true }
            return false
        }
    }

    app.get("context", ":contextId", "experiments") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) { storage.context.experiments }
    }

    app.post("context", ":contextId", "readyError") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) {
            if case .failed(let error) = storage.context.state {
                return ["isError": true, "message": error.localizedDescription] as [String: Any]
            }
            return nil
        }
    }

    app.post("context", ":contextId", "publishFail") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) {
            storage.transport.setShouldFail(true)
            return nil
        }
    }

    app.post("context", ":contextId", "publish") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) {
            try await storage.context.publish()
            return nil
        }
    }

    app.post("context", ":contextId", "refresh") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) {
            try await storage.context.refresh()
            return nil
        }
    }

    app.post("context", ":contextId", "finalize") { req async throws -> HTTPResponse in
        let storage = try lookup(req)
        return try await call(storage) {
            try await storage.context.close()
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
        if storage.context.state != .closed {
            do { try await storage.context.close() } catch { closeError = error }
        }
        contextManager.remove(contextId: contextId)
        let events = Array(storage.eventCollector.events.suffix(from: before))
        if let closeError = closeError {
            return try reply(["error": closeError.localizedDescription, "events": events], status: .badRequest)
        }
        return try reply(["result": "deleted", "events": events])
    }
}
