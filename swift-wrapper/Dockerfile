# Swift SDK 2.x: no third-party dependencies (swift-crypto on Linux only), Swift 6 toolchain.
FROM swift:6.4-jammy AS sdk-builder

COPY swift-sdk /swift-sdk

RUN apt-get update && apt-get install -y \
    libssl-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

FROM sdk-builder AS tester
WORKDIR /swift-sdk
RUN swift build --build-tests -Xswiftc -warnings-as-errors
COPY cross-sdk-tests/swift-wrapper/run-tests.sh /run-tests.sh
RUN chmod +x /run-tests.sh
CMD ["/run-tests.sh"]

FROM sdk-builder AS server
WORKDIR /app

COPY cross-sdk-tests/swift-wrapper /app

RUN cd /app && swift package resolve

RUN cd /app && swift test --filter EventCollectorTests && swift build -c release

EXPOSE 3000

CMD ["/app/.build/release/Run"]
