import XCTest
import ABsmartly
@testable import App

final class SwitchbackTests: XCTestCase {
    func testBehaviouralCapabilities() {
        XCTAssertTrue(SwitchbackSelfTest.run())
        XCTAssertTrue(SwitchbackSelfTest.runIntegralFloat())
    }

    func testRawDataKeepsClusterOrderAndNumberSyntax() throws {
        let raw = Data(#"{"data":{"experiments":[{"name":"e","switchbackClusters":{"z":"a","b":"b"}}]},"value":{"n":3.0,"i":3}}"#.utf8)
        let data = try XCTUnwrap(RawJSON.member(raw, "data"))
        let decoded = try ContextData(json: data)
        XCTAssertEqual(decoded.experiments[0].switchbackClusters?.map(\.unitType), ["z", "b"])
        let value = try XCTUnwrap(RawJSON.value(raw, "value"))
        guard case .double? = value["n"], case .int? = value["i"] else {
            return XCTFail("float and integer tokens must remain distinct")
        }
    }

    func testRawMemberSkipsNestedEscapedStringsAndUsesLastDuplicate() {
        let raw = Data(#"{"data":{"s":"a\"}b","x":[1,{"data":42}]},"data":{"last":true}}"#.utf8)
        XCTAssertEqual(RawJSON.member(raw, "data"), Data(#"{"last":true}"#.utf8))
        XCTAssertNil(RawJSON.member(raw, "missing"))
    }

    func testAssignmentRequestValidation() throws {
        XCTAssertEqual(try switchbackInt64(.string("-9223372036854775808"), "seed"), .min)
        XCTAssertEqual(try switchbackInt64(.string("9223372036854775807"), "seed"), .max)
        for invalid: JSONValue in [.string("9223372036854775808"), .string("1.0"), .string(""), .int(1), .null] {
            XCTAssertThrowsError(try switchbackInt64(invalid, "seed"))
        }
        XCTAssertEqual(try switchbackArmCount(.int(2)), 2)
        XCTAssertEqual(try switchbackArmCount(.int(1000)), 1000)
        for invalid: JSONValue in [.int(1), .int(1001), .double(2.5), .string("2"), .null] {
            XCTAssertThrowsError(try switchbackArmCount(invalid))
        }
    }
}
