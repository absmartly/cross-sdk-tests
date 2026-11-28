import XCTest
import Vapor
import ABsmartly
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
@testable import App

final class ContextFinalizationTests: XCTestCase {
    func testFailedLoadPreservesSDKReadyErrorAfterFinalize() async throws {
        try await assertFailedFinalization(queryBeforeFinalize: true)
    }

    func testFailedLoadPreservesErrorWithoutAnEarlierGetter() async throws {
        try await assertFailedFinalization(queryBeforeFinalize: false)
    }

    private func assertFailedFinalization(queryBeforeFinalize: Bool) async throws {
        let server = try WrapperServer()
        defer { server.stop() }
        let created = try await server.request(.POST, "context", body: ["failLoad": true])
        let result = try XCTUnwrap(created["result"] as? [String: Any])
        XCTAssertEqual(result["failed"] as? Bool, true)
        let id = try XCTUnwrap(result["contextId"] as? String)
        defer { contextManager.remove(contextId: id) }
        let context = try XCTUnwrap(contextManager.get(contextId: id)?.context)
        let originalError: ABsmartlyError
        do {
            try await context.ready()
            XCTFail("The SDK's real load must fail")
            return
        } catch {
            originalError = error
        }
        if queryBeforeFinalize {
            try await assertFailure(server, id: id, error: originalError)
        }

        let finalized = try await server.request(.POST, "context/\(id)/finalize")
        XCTAssertTrue(finalized["result"] is NSNull)
        XCTAssertEqual(context.state, .closed)
        let isFinalized = try await server.request(.GET, "context/\(id)/isFinalized")
        XCTAssertEqual(isFinalized["result"] as? Bool, true)
        try await assertFailure(server, id: id, error: originalError)

        do {
            try await context.ready()
            XCTFail("Finalization must not recover the SDK's load failure")
        } catch {
            XCTAssertEqual(error, originalError)
        }
    }

    private func assertFailure(_ server: WrapperServer, id: String, error: ABsmartlyError) async throws {
        let failed = try await server.request(.GET, "context/\(id)/isFailed")
        XCTAssertEqual(failed["result"] as? Bool, true)
        let response = try await server.request(.POST, "context/\(id)/readyError")
        let readyError = try XCTUnwrap(response["result"] as? [String: Any])
        XCTAssertEqual(readyError["isError"] as? Bool, true)
        XCTAssertEqual(readyError["message"] as? String, error.localizedDescription)
    }

    func testSuccessfulFinalizationStillReportsNoLoadFailure() async throws {
        let server = try WrapperServer()
        defer { server.stop() }
        let created = try await server.request(.POST, "context", body: ["data": ["experiments": []]])
        let result = try XCTUnwrap(created["result"] as? [String: Any])
        let id = try XCTUnwrap(result["contextId"] as? String)
        defer { contextManager.remove(contextId: id) }
        let context = try XCTUnwrap(contextManager.get(contextId: id)?.context)
        try await context.ready()
        try await assertNoFailure(server, id: id)

        let finalized = try await server.request(.POST, "context/\(id)/finalize")
        XCTAssertTrue(finalized["result"] is NSNull)
        XCTAssertEqual(context.state, .closed)
        try await context.ready()
        let isFinalized = try await server.request(.GET, "context/\(id)/isFinalized")
        XCTAssertEqual(isFinalized["result"] as? Bool, true)
        try await assertNoFailure(server, id: id)
    }

    func testLoadingGettersAndFinalizationDoNotWaitForReady() async throws {
        let server = try WrapperServer()
        defer { server.stop() }
        let payloadId = UUID().uuidString
        _ = try await server.request(.PUT, "context_payload/\(payloadId)", body: ["data": ["experiments": []]])
        defer { payloadStore[payloadId] = nil }
        let created = try await server.request(.POST, "context", body: [
            "endpoint": "\(server.endpoint)/context_payload/\(payloadId)",
            "options": ["payloadThrottle": 10_000],
        ])
        let result = try XCTUnwrap(created["result"] as? [String: Any])
        let id = try XCTUnwrap(result["contextId"] as? String)
        defer { contextManager.remove(contextId: id) }
        let context = try XCTUnwrap(contextManager.get(contextId: id)?.context)
        XCTAssertEqual(context.state, .loading)
        // These requests time out in two seconds if a getter or finalize awaits loading.
        try await assertNoFailure(server, id: id)
        let finalized = try await server.request(.POST, "context/\(id)/finalize")
        XCTAssertTrue(finalized["result"] is NSNull)
        XCTAssertEqual(context.state, .closed)
        try await assertNoFailure(server, id: id)
    }

    private func assertNoFailure(_ server: WrapperServer, id: String) async throws {
        let failed = try await server.request(.GET, "context/\(id)/isFailed")
        XCTAssertEqual(failed["result"] as? Bool, false)
        let readyError = try await server.request(.POST, "context/\(id)/readyError")
        XCTAssertTrue(readyError["result"] is NSNull)
    }
}

private final class WrapperServer {
    private let app: Vapor.Application
    private let session: URLSession
    let endpoint: String

    init() throws {
        app = Vapor.Application(.testing)
        try configure(app)
        app.http.server.configuration.hostname = "127.0.0.1"
        app.http.server.configuration.port = 0
        try app.server.start()
        let port = try XCTUnwrap(app.http.server.shared.localAddress?.port)
        endpoint = "http://127.0.0.1:\(port)"
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 2
        configuration.timeoutIntervalForResource = 5
        session = URLSession(configuration: configuration)
    }

    func request(_ method: HTTPMethod, _ path: String, body: [String: Any] = [:]) async throws -> [String: Any] {
        var request = URLRequest(url: try XCTUnwrap(URL(string: "\(endpoint)/\(path)")))
        request.httpMethod = method.rawValue
        if method != .GET {
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await session.data(for: request)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200, String(decoding: data, as: UTF8.self))
        return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    func stop() {
        session.invalidateAndCancel()
        app.server.shutdown()
        app.shutdown()
    }
}
