import Vapor
import ABSmartly
import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import PromiseKit

typealias HTTPResponse = Vapor.Response
typealias VaporApplication = Vapor.Application
typealias ABSmartlyApplication = ABSmartly.Application

class EventCollector: ContextEventLogger {
    var events: [[String: Any]] = []
    private let maxEvents = 100 // Prevent unbounded growth

    func handleEvent(context: Context, event: ContextEventLoggerEvent) {
        // Prevent memory exhaustion from unbounded event accumulation
        if events.count >= maxEvents {
            events.removeFirst(maxEvents / 2) // Remove oldest half
        }

        let timestamp = Int64(Date().timeIntervalSince1970 * 1000)

        switch event {
        case .ready(let data):
            events.append([
                "type": "ready",
                "data": convertContextDataToDict(data),
                "timestamp": timestamp
            ])
        case .refresh(let data):
            events.append([
                "type": "refresh",
                "data": convertContextDataToDict(data),
                "timestamp": timestamp
            ])
        case .publish(let publishEvent):
            events.append([
                "type": "publish",
                "data": convertPublishEventToDict(publishEvent),
                "timestamp": timestamp
            ])
        case .exposure(let exposure):
            events.append([
                "type": "exposure",
                "data": convertExposureToDict(exposure),
                "timestamp": timestamp
            ])
        case .goal(let goal):
            events.append([
                "type": "goal",
                "data": convertGoalToDict(goal),
                "timestamp": timestamp
            ])
        case .close:
            events.append([
                "type": "finalize",
                "data": NSNull(),
                "timestamp": timestamp
            ])
        case .error(let error):
            events.append([
                "type": "error",
                "data": ["message": error.localizedDescription],
                "timestamp": timestamp
            ])
        }
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
            ["name": app.name ?? ""]
        }
        dict["variants"] = exp.variants.map { variant in
            ["name": variant.name ?? "", "config": variant.config ?? ""]
        }
        dict["audience"] = exp.audience
        dict["audienceStrict"] = exp.audienceStrict
        if let customFieldValues = exp.customFieldValues {
            dict["customFieldValues"] = customFieldValues.map { cfv in
                ["name": cfv.name ?? "", "type": cfv.type ?? "", "value": cfv.value ?? ""]
            }
        }
        return dict
    }

    private func convertPublishEventToDict(_ event: PublishEvent) -> [String: Any] {
        var dict: [String: Any] = [:]
        dict["hashed"] = event.hashed
        dict["publishedAt"] = event.publishedAt
        dict["units"] = event.units.map { unit in
            ["type": unit.type ?? "", "uid": unit.uid ?? ""]
        }
        dict["exposures"] = event.exposures.map { exp in
            convertExposureToDict(exp)
        }
        dict["goals"] = event.goals.map { goal in
            convertGoalToDict(goal)
        }
        dict["attributes"] = event.attributes.map { attr in
            ["name": attr.name ?? "", "value": jsonToAny(attr.value), "setAt": attr.setAt]
        }
        return dict
    }

    private func convertExposureToDict(_ exposure: Exposure) -> [String: Any] {
        var dict: [String: Any] = [:]
        dict["id"] = exposure.id
        dict["name"] = exposure.name
        dict["unit"] = exposure.unit
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
        } else if let number = json.number {
            return number
        } else if let bool = json.bool {
            return bool
        } else if json.null != nil {
            return NSNull()
        }

        return NSNull()
    }
}

class CustomPublisher: ContextEventHandler {
    func publish(event: PublishEvent) -> Promise<Void> {
        return Promise.value(())
    }
}

class DummyClient: ABSmartly.Client {
    func getContextData() -> Promise<ContextData> {
        return Promise.value(ContextData(experiments: []))
    }

    func publish(event: PublishEvent) -> Promise<Void> {
        return Promise.value(())
    }

    func close() -> Promise<Void> {
        return Promise.value(())
    }
}

class DummyContextDataProvider: ContextDataProvider {
    func getContextData() -> Promise<ContextData> {
        return Promise.value(ContextData(experiments: []))
    }
}

struct ContextStorage {
    let context: Context
    let eventCollector: EventCollector
    let sdk: ABSmartlySDK
}

class ContextManager {
    private var contexts: [String: ContextStorage] = [:]
    private var contextOrder: [String] = []
    private let lock = NSLock()
    private let maxContexts = 50

    func store(contextId: String, context: Context, collector: EventCollector, sdk: ABSmartlySDK) {
        var toEvict: [ContextStorage] = []

        lock.lock()
        while contexts.count >= maxContexts && !contextOrder.isEmpty {
            let oldestId = contextOrder.removeFirst()
            if let oldStorage = contexts.removeValue(forKey: oldestId) {
                toEvict.append(oldStorage)
            }
        }
        contexts[contextId] = ContextStorage(context: context, eventCollector: collector, sdk: sdk)
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

let contextManager = ContextManager()

func routes(_ app: VaporApplication) throws {
    app.get("health") { req -> HTTPResponse in
        let health: [String: String] = [
            "status": "healthy",
            "sdk": "swift",
            "version": "1.0.0"
        ]
        return try HTTPResponse(status: .ok, body: .init(data: JSONEncoder().encode(health)))
    }

    app.post("context") { req -> HTTPResponse in
        struct CreateContextRequest: Content {
            let data: ContextDataDTO
            let units: [String: AnyCodable]
            let options: ContextOptionsDTO?
        }

        let request = try req.content.decode(CreateContextRequest.self)
        let contextId = "ctx-\(Date().timeIntervalSince1970)-\(UUID().uuidString)"

        let eventCollector = EventCollector()
        let customPublisher = CustomPublisher()
        let dummyProvider = DummyContextDataProvider()

        let config = ABSmartlyConfig(
            contextDataProvider: dummyProvider,
            contextEventHandler: customPublisher,
            contextEventLogger: eventCollector,
            variableParser: nil,
            scheduler: nil,
            client: nil
        )

        let sdk: ABSmartlySDK
        do {
            sdk = try ABSmartlySDK(config: config)
        } catch {
            print("Error creating SDK: \(error)")
            throw error
        }

        let contextData = convertDTOToContextData(request.data)

        var configBuilder = ContextConfig()
        configBuilder.publishDelay = request.options?.publishDelay ?? -1
        configBuilder.refreshInterval = request.options?.refreshPeriod ?? 0

        var unitsDict: [String: String] = [:]
        for (key, value) in request.units {
            unitsDict[key] = "\(value.value)"
        }
        configBuilder.setUnits(units: unitsDict)

        configBuilder.eventLogger = eventCollector

        let context = sdk.createContextWithData(config: configBuilder, contextData: contextData)

        contextManager.store(contextId: contextId, context: context, collector: eventCollector, sdk: sdk)

        let result: [String: Any] = [
            "result": [
                "contextId": contextId,
                "ready": context.isReady(),
                "failed": context.isFailed(),
                "finalized": context.isClosed()
            ],
            "events": sanitizeForJSON(eventCollector.events)
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.post("context", ":contextId", "setUnit") { req -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        struct SetUnitRequest: Content {
            let unitType: String
            let uid: AnyCodable
        }

        let request = try req.content.decode(SetUnitRequest.self)
        let eventsBefore = storage.eventCollector.events.count

        storage.context.setUnit(unitType: request.unitType, uid: "\(request.uid.value)")

        let newEvents = Array(storage.eventCollector.events.suffix(from: eventsBefore))
        let result: [String: Any] = [
            "result": NSNull(),
            "events": sanitizeForJSON(newEvents)
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.post("context", ":contextId", "getUnit") { req -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        struct GetUnitRequest: Content {
            let unitType: String
        }

        let request = try req.content.decode(GetUnitRequest.self)
        let eventsBefore = storage.eventCollector.events.count

        let unit = storage.context.getUnit(unitType: request.unitType)

        let newEvents = Array(storage.eventCollector.events.suffix(from: eventsBefore))

        var resultValue: Any = NSNull()
        if let unitString = unit {
            if let intValue = Int(unitString) {
                resultValue = intValue
            } else if let doubleValue = Double(unitString) {
                resultValue = doubleValue
            } else {
                resultValue = unitString
            }
        }

        let result: [String: Any] = [
            "result": sanitizeForJSON(resultValue),
            "events": sanitizeForJSON(newEvents)
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.post("context", ":contextId", "attribute") { req -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        struct AttributeRequest: Content {
            let name: String
            let value: AnyCodable
        }

        let request = try req.content.decode(AttributeRequest.self)
        let eventsBefore = storage.eventCollector.events.count

        storage.context.setAttribute(name: request.name, value: JSON(request.value.value))

        let newEvents = Array(storage.eventCollector.events.suffix(from: eventsBefore))
        let result: [String: Any] = [
            "result": NSNull(),
            "events": sanitizeForJSON(newEvents)
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.post("context", ":contextId", "getAttribute") { req -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        struct GetAttributeRequest: Content {
            let name: String
        }

        let request = try req.content.decode(GetAttributeRequest.self)
        let eventsBefore = storage.eventCollector.events.count

        let attribute = storage.context.getAttribute(name: request.name)

        let newEvents = Array(storage.eventCollector.events.suffix(from: eventsBefore))

        var resultValue: Any = NSNull()
        if let attr = attribute {
            resultValue = jsonToAnyHelper(attr)
        }

        let result: [String: Any] = [
            "result": sanitizeForJSON(resultValue),
            "events": sanitizeForJSON(newEvents)
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.post("context", ":contextId", "treatment") { req -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        struct TreatmentRequest: Content {
            let experimentName: String
        }

        let request = try req.content.decode(TreatmentRequest.self)
        let eventsBefore = storage.eventCollector.events.count

        let variant = storage.context.getTreatment(request.experimentName)

        let newEvents = Array(storage.eventCollector.events.suffix(from: eventsBefore))
        let result: [String: Any] = [
            "result": sanitizeForJSON(variant),
            "events": sanitizeForJSON(newEvents)
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.post("context", ":contextId", "peek") { req -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        struct PeekRequest: Content {
            let experimentName: String
        }

        let request = try req.content.decode(PeekRequest.self)
        let eventsBefore = storage.eventCollector.events.count

        let variant = storage.context.peekTreatment(request.experimentName)

        let newEvents = Array(storage.eventCollector.events.suffix(from: eventsBefore))
        let result: [String: Any] = [
            "result": sanitizeForJSON(variant),
            "events": sanitizeForJSON(newEvents)
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.post("context", ":contextId", "variableValue") { req -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        struct VariableValueRequest: Content {
            let key: String
            let defaultValue: AnyCodable?
        }

        let request = try req.content.decode(VariableValueRequest.self)
        let eventsBefore = storage.eventCollector.events.count

        let defaultJSON = request.defaultValue.map { JSON($0.value) }
        let value = storage.context.getVariableValue(request.key, defaultValue: defaultJSON)

        let newEvents = Array(storage.eventCollector.events.suffix(from: eventsBefore))

        var resultValue: Any = NSNull()
        if let val = value {
            resultValue = jsonToAnyHelper(val)
        }

        let result: [String: Any] = [
            "result": sanitizeForJSON(resultValue),
            "events": sanitizeForJSON(newEvents)
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.post("context", ":contextId", "peekVariableValue") { req -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        struct PeekVariableValueRequest: Content {
            let key: String
            let defaultValue: AnyCodable?
        }

        let request = try req.content.decode(PeekVariableValueRequest.self)
        let eventsBefore = storage.eventCollector.events.count

        let defaultJSON = request.defaultValue.map { JSON($0.value) }
        let value = storage.context.peekVariableValue(request.key, defaultValue: defaultJSON)

        let newEvents = Array(storage.eventCollector.events.suffix(from: eventsBefore))

        var resultValue: Any = NSNull()
        if let val = value {
            resultValue = jsonToAnyHelper(val)
        }

        let result: [String: Any] = [
            "result": sanitizeForJSON(resultValue),
            "events": sanitizeForJSON(newEvents)
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.post("context", ":contextId", "track") { req -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        struct TrackRequest: Content {
            let goalName: String
            let properties: AnyCodable?
        }

        let request = try req.content.decode(TrackRequest.self)
        let eventsBefore = storage.eventCollector.events.count

        var props: [String: JSON]? = nil
        if let properties = request.properties {
            if let dict = properties.value as? [String: Any] {
                var propsDict: [String: JSON] = [:]
                for (key, value) in dict {
                    propsDict[key] = JSON(value)
                }
                props = propsDict
            } else if !(properties.value is NSNull) {
                let result: [String: Any] = [
                    "error": "Goal '\(request.goalName)' properties must be of type object."
                ]
                return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
            }
        }

        storage.context.track(request.goalName, properties: props)

        let newEvents = Array(storage.eventCollector.events.suffix(from: eventsBefore))
        let result: [String: Any] = [
            "result": NSNull(),
            "events": sanitizeForJSON(newEvents)
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.post("context", ":contextId", "override") { req -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        struct OverrideRequest: Content {
            let experimentName: String
            let variant: Int
        }

        let request = try req.content.decode(OverrideRequest.self)

        storage.context.setOverride(experimentName: request.experimentName, variant: request.variant)

        let result: [String: Any] = [
            "result": NSNull(),
            "events": []
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.post("context", ":contextId", "customAssignment") { req -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        struct CustomAssignmentRequest: Content {
            let experimentName: String
            let variant: Int
        }

        let request = try req.content.decode(CustomAssignmentRequest.self)

        storage.context.setCustomAssignment(experimentName: request.experimentName, variant: request.variant)

        let result: [String: Any] = [
            "result": NSNull(),
            "events": []
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.post("context", ":contextId", "customFieldValue") { req -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        struct CustomFieldValueRequest: Content {
            let experimentName: String
            let fieldName: String
        }

        let request = try req.content.decode(CustomFieldValueRequest.self)
        let eventsBefore = storage.eventCollector.events.count

        let value = storage.context.getCustomFieldValue(experimentName: request.experimentName, key: request.fieldName)

        let newEvents = Array(storage.eventCollector.events.suffix(from: eventsBefore))
        let result: [String: Any] = [
            "result": sanitizeForJSON(value),
            "events": sanitizeForJSON(newEvents)
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.post("context", ":contextId", "variableKeys") { req -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        let eventsBefore = storage.eventCollector.events.count

        let keys = storage.context.getVariableKeys()
        let keyList = Array(keys.keys)

        let newEvents = Array(storage.eventCollector.events.suffix(from: eventsBefore))
        let result: [String: Any] = [
            "result": keyList,
            "events": sanitizeForJSON(newEvents)
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.post("context", ":contextId", "customFieldKeys") { req -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        struct CustomFieldKeysRequest: Content {
            let experimentName: String
        }

        let request = try req.content.decode(CustomFieldKeysRequest.self)
        let eventsBefore = storage.eventCollector.events.count

        let keys = storage.context.getCustomFieldKeys(experimentName: request.experimentName)

        let newEvents = Array(storage.eventCollector.events.suffix(from: eventsBefore))
        let result: [String: Any] = [
            "result": keys,
            "events": sanitizeForJSON(newEvents)
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.post("context", ":contextId", "customFieldValueType") { req -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        struct CustomFieldValueTypeRequest: Content {
            let experimentName: String
            let fieldName: String
        }

        let request = try req.content.decode(CustomFieldValueTypeRequest.self)
        let eventsBefore = storage.eventCollector.events.count

        let valueType = storage.context.getCustomFieldValueType(experimentName: request.experimentName, key: request.fieldName)

        let newEvents = Array(storage.eventCollector.events.suffix(from: eventsBefore))
        let result: [String: Any] = [
            "result": sanitizeForJSON(valueType),
            "events": sanitizeForJSON(newEvents)
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.get("context", ":contextId", "pending") { req -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        let pending = storage.context.getPendingCount()

        let result: [String: Any] = [
            "result": pending,
            "events": []
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.get("context", ":contextId", "isFinalized") { req -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        let finalized = storage.context.isClosed()

        let result: [String: Any] = [
            "result": finalized,
            "events": []
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.post("context", ":contextId", "publish") { req async throws -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        let eventsBefore = storage.eventCollector.events.count

        // Get pending count before publish to know if we should expect a publish event
        let pendingBefore = storage.context.getPendingCount()
        req.logger.info("[DEBUG PUBLISH] pendingBefore=\(pendingBefore), eventsBefore=\(eventsBefore)")

        try await storage.context.publish().value

        req.logger.info("[DEBUG PUBLISH] After await, total events=\(storage.eventCollector.events.count)")

        // Poll for the publish event to appear (PromiseKit callback timing issue)
        var pollCount = 0
        let maxPolls = 50 // 50 * 10ms = 500ms max wait
        while pendingBefore > 0 && pollCount < maxPolls {
            let currentEvents = Array(storage.eventCollector.events.suffix(from: eventsBefore))
            if currentEvents.contains(where: { ($0["type"] as? String) == "publish" }) {
                req.logger.info("[DEBUG PUBLISH] Found publish event after \(pollCount) polls")
                break
            }
            try await Task.sleep(nanoseconds: 10_000_000) // 10ms
            pollCount += 1
        }

        if pollCount == maxPolls {
            req.logger.warning("[DEBUG PUBLISH] Timeout waiting for publish event! Final events=\(storage.eventCollector.events.count)")
            for (i, evt) in storage.eventCollector.events.enumerated() {
                req.logger.info("[DEBUG PUBLISH]   Event \(i): \(evt["type"] ?? "unknown")")
            }
        }

        let newEvents = Array(storage.eventCollector.events.suffix(from: eventsBefore))
        req.logger.info("[DEBUG PUBLISH] Returning \(newEvents.count) new events")
        let result: [String: Any] = [
            "result": NSNull(),
            "events": sanitizeForJSON(newEvents)
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.post("context", ":contextId", "refresh") { req async throws -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        struct RefreshRequest: Content {
            let newData: ContextDataDTO
        }

        let request = try req.content.decode(RefreshRequest.self)
        let eventsBefore = storage.eventCollector.events.count

        let newContextData = convertDTOToContextData(request.newData)

        storage.context.setData(newContextData)

        storage.eventCollector.handleEvent(context: storage.context, event: .refresh(data: newContextData))

        let newEvents = Array(storage.eventCollector.events.suffix(from: eventsBefore))
        let result: [String: Any] = [
            "result": NSNull(),
            "events": sanitizeForJSON(newEvents)
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.post("context", ":contextId", "finalize") { req async throws -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        let eventsBefore = storage.eventCollector.events.count

        // Get pending count before close to know if we should expect a publish event
        let pendingBefore = storage.context.getPendingCount()

        // Close the context (but keep in manager so isFinalized can still be queried)
        try await storage.context.close().value

        // Poll for the finalize event to appear (PromiseKit callback timing issue)
        var pollCount = 0
        let maxPolls = 50 // 50 * 10ms = 500ms max wait
        while pollCount < maxPolls {
            let currentEvents = Array(storage.eventCollector.events.suffix(from: eventsBefore))
            let hasFinalize = currentEvents.contains(where: { ($0["type"] as? String) == "finalize" })
            let hasPublish = pendingBefore == 0 || currentEvents.contains(where: { ($0["type"] as? String) == "publish" })
            if hasFinalize && hasPublish {
                break
            }
            try await Task.sleep(nanoseconds: 10_000_000) // 10ms
            pollCount += 1
        }

        let newEvents = Array(storage.eventCollector.events.suffix(from: eventsBefore))
        let result: [String: Any] = [
            "result": NSNull(),
            "events": sanitizeForJSON(newEvents)
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
    }

    app.delete("context", ":contextId") { req async throws -> HTTPResponse in
        guard let contextId = req.parameters.get("contextId"),
              let storage = contextManager.get(contextId: contextId) else {
            throw Abort(.notFound, reason: "Context not found")
        }

        if !storage.context.isClosed() {
            do {
                try await storage.context.close().value
            } catch {
                print("Error closing context \(contextId): \(error)")
            }
        }

        _ = await storage.sdk.close().value

        contextManager.remove(contextId: contextId)

        let result: [String: Any] = [
            "result": "deleted"
        ]

        return try HTTPResponse(status: .ok, body: .init(data: JSONSerialization.data(withJSONObject: result, options: [])))
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
    } else if let number = json.number {
        return number
    } else if let bool = json.bool {
        return bool
    } else if json.null != nil {
        return NSNull()
    }

    return NSNull()
}

func convertDTOToContextData(_ dto: ContextDataDTO) -> ContextData {
    do {
        let jsonData = try JSONEncoder().encode(dto)
        let contextData = try JSONDecoder().decode(ContextData.self, from: jsonData)
        return contextData
    } catch {
        print("Error converting DTO to ContextData: \(error)")
        return ContextData(experiments: [])
    }
}

struct AnyCodable: Codable {
    let value: Any

    init(_ value: Any) {
        self.value = value
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()

        if container.decodeNil() {
            value = NSNull()
        } else if let bool = try? container.decode(Bool.self) {
            value = bool
        } else if let int = try? container.decode(Int.self) {
            value = int
        } else if let double = try? container.decode(Double.self) {
            value = double
        } else if let string = try? container.decode(String.self) {
            value = string
        } else if let array = try? container.decode([AnyCodable].self) {
            value = array.map { $0.value }
        } else if let dict = try? container.decode([String: AnyCodable].self) {
            value = dict.mapValues { $0.value }
        } else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Cannot decode value")
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()

        if value is NSNull {
            try container.encodeNil()
        } else if let bool = value as? Bool {
            try container.encode(bool)
        } else if let int = value as? Int {
            try container.encode(int)
        } else if let double = value as? Double {
            try container.encode(double)
        } else if let string = value as? String {
            try container.encode(string)
        } else if let array = value as? [Any] {
            try container.encode(array.map { AnyCodable($0) })
        } else if let dict = value as? [String: Any] {
            try container.encode(dict.mapValues { AnyCodable($0) })
        } else {
            try container.encodeNil()
        }
    }
}

struct ContextDataDTO: Codable {
    let experiments: [ExperimentDTO]
}

struct ExperimentDTO: Codable {
    let id: Int
    let name: String
    let unitType: String
    let iteration: Int
    let seedHi: Int
    let seedLo: Int
    let split: [Double]
    let trafficSeedHi: Int
    let trafficSeedLo: Int
    let trafficSplit: [Double]
    let fullOnVariant: Int
    let applications: [ApplicationDTO]?
    let variants: [VariantDTO]?
    let audience: String?
    let audienceStrict: Bool?
    let customFieldValues: [CustomFieldValueDTO]?
}

struct ApplicationDTO: Codable {
    let name: String
}

struct VariantDTO: Codable {
    let name: String
    let config: String?
}

struct CustomFieldValueDTO: Codable {
    let name: String
    let type: String
    let value: String
}

struct ContextOptionsDTO: Codable {
    let publishDelay: TimeInterval?
    let refreshPeriod: TimeInterval?
}
