FROM swift:5.10-jammy AS sdk-builder

COPY swift-sdk /swift-sdk

RUN apt-get update && apt-get install -y \
    libssl-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

FROM sdk-builder AS tester
WORKDIR /swift-sdk
# Patch SwiftyJSON NSNumber operator ambiguity on Linux
RUN swift package resolve && \
    sed -i '/^func < (lhs: NSNumber, rhs: NSNumber)/,/^}$/d' /swift-sdk/.build/checkouts/SwiftyJSON/Source/SwiftyJSON/SwiftyJSON.swift && \
    sed -i 's/lhs\.rawNumber < rhs\.rawNumber/lhs.rawNumber.compare(rhs.rawNumber) == .orderedAscending/g' /swift-sdk/.build/checkouts/SwiftyJSON/Source/SwiftyJSON/SwiftyJSON.swift
CMD ["swift", "test"]

FROM sdk-builder AS server
WORKDIR /app

COPY cross-sdk-tests/swift-wrapper /app

# Patch SwiftyJSON NSNumber operator ambiguity on Linux
RUN cd /app && swift package resolve && \
    sed -i '/^func < (lhs: NSNumber, rhs: NSNumber)/,/^}$/d' /app/.build/checkouts/SwiftyJSON/Source/SwiftyJSON/SwiftyJSON.swift && \
    sed -i 's/lhs\.rawNumber < rhs\.rawNumber/lhs.rawNumber.compare(rhs.rawNumber) == .orderedAscending/g' /app/.build/checkouts/SwiftyJSON/Source/SwiftyJSON/SwiftyJSON.swift

RUN cd /app && swift build -c release

EXPOSE 3000

CMD ["/app/.build/release/Run"]
