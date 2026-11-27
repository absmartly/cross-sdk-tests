import ABsmartly
import Foundation

// Byte-level helpers for request bodies whose JSON must reach the SDK unchanged. Decoding a body into
// `JSONValue` (a Swift dictionary) loses object key order, which a switchback's `switchbackClusters`
// depends on, and `JSONDecoder` reads `3.0` as the integer 3, which a switchback cluster attribute
// must not become.
enum RawJSON {
    /// The bytes of the top-level member `key` of the JSON object `body`, as written; nil when the
    /// body is not an object or has no such member. A later duplicate member wins, as in the decoder.
    static func member(_ body: Data, _ key: String) -> Data? {
        var scanner = Scanner(Array(body))
        return scanner.member(key).map { Data($0) }
    }

    /// The value of `key` parsed with `JSONValue(parsing:)` (JSONSerialization), which keeps a number
    /// literal with a fraction or exponent a double.
    static func value(_ body: Data, _ key: String) -> JSONValue? {
        guard let raw = member(body, key) else { return nil }
        return try? JSONValue(parsing: String(decoding: raw, as: UTF8.self))
    }

    private struct Scanner {
        let bytes: [UInt8]
        var i = 0

        init(_ bytes: [UInt8]) {
            self.bytes = bytes
            if bytes.starts(with: [0xEF, 0xBB, 0xBF]) {
                i = 3
            }
        }

        mutating func member(_ key: String) -> ArraySlice<UInt8>? {
            skipWhitespace()
            guard consume(UInt8(ascii: "{")) else { return nil }
            var found: ArraySlice<UInt8>?
            skipWhitespace()
            if consume(UInt8(ascii: "}")) { return nil }
            while true {
                skipWhitespace()
                guard let name = readString() else { return nil }
                skipWhitespace()
                guard consume(UInt8(ascii: ":")) else { return nil }
                skipWhitespace()
                let start = i
                guard skipValue() else { return nil }
                if name == key {
                    found = bytes[start..<i]
                }
                skipWhitespace()
                if consume(UInt8(ascii: ",")) { continue }
                return consume(UInt8(ascii: "}")) ? found : nil
            }
        }

        private func peek() -> UInt8? { i < bytes.count ? bytes[i] : nil }

        private mutating func consume(_ c: UInt8) -> Bool {
            guard peek() == c else { return false }
            i += 1
            return true
        }

        private mutating func skipWhitespace() {
            while let c = peek(), c == 0x20 || c == 0x0A || c == 0x0D || c == 0x09 { i += 1 }
        }

        private mutating func readString() -> String? {
            let start = i
            guard skipString() else { return nil }
            let text = String(decoding: bytes[start..<i], as: UTF8.self)
            return (try? JSONValue(parsing: text))?.stringValue
        }

        private mutating func skipString() -> Bool {
            guard consume(UInt8(ascii: "\"")) else { return false }
            while let c = peek() {
                i += 1
                if c == UInt8(ascii: "\"") { return true }
                if c == UInt8(ascii: "\\") {
                    guard peek() != nil else { return false }
                    i += 1
                }
            }
            return false
        }

        private mutating func skipValue() -> Bool {
            guard let c = peek() else { return false }
            switch c {
            case UInt8(ascii: "\""):
                return skipString()
            case UInt8(ascii: "{"), UInt8(ascii: "["):
                let close = c == UInt8(ascii: "{") ? UInt8(ascii: "}") : UInt8(ascii: "]")
                i += 1
                skipWhitespace()
                if consume(close) { return true }
                while true {
                    skipWhitespace()
                    if c == UInt8(ascii: "{") {
                        guard skipString() else { return false }
                        skipWhitespace()
                        guard consume(UInt8(ascii: ":")) else { return false }
                        skipWhitespace()
                    }
                    guard skipValue() else { return false }
                    skipWhitespace()
                    if consume(UInt8(ascii: ",")) { continue }
                    return consume(close)
                }
            default:
                let start = i
                while let d = peek(), d != UInt8(ascii: ","), d != UInt8(ascii: "}"), d != UInt8(ascii: "]"),
                    d != 0x20, d != 0x0A, d != 0x0D, d != 0x09
                {
                    i += 1
                }
                return i > start
            }
        }
    }
}
