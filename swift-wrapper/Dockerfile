FROM swift:5.9-jammy

WORKDIR /app

COPY swift-sdk /swift-sdk
COPY cross-sdk-tests/swift-wrapper /app

RUN apt-get update && apt-get install -y \
    libssl-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Patch SwiftyJSON NSNumber operator issue for Swift 5.9
RUN cd /app && swift package resolve && \
    sed -i '/^func < (lhs: NSNumber, rhs: NSNumber)/,/^}$/d' /app/.build/checkouts/SwiftyJSON/Source/SwiftyJSON/SwiftyJSON.swift && \
    sed -i 's/lhs\.rawNumber < rhs\.rawNumber/lhs.rawNumber.compare(rhs.rawNumber) == .orderedAscending/g' /app/.build/checkouts/SwiftyJSON/Source/SwiftyJSON/SwiftyJSON.swift

RUN cd /app && swift build -c release

EXPOSE 3000

CMD ["/app/.build/release/Run"]
