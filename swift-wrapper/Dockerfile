FROM swift:5.10-jammy AS sdk-builder

COPY swift-sdk /swift-sdk

RUN apt-get update && apt-get install -y \
    libssl-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Released SwiftyJSON (5.0.x) does not compile on Linux: its NSNumber `<` is
# ambiguous with Foundation's (SwiftyJSON issues #1038/#1041). Build against the
# fix proposed upstream in SwiftyJSON PR #1185, pinned to its exact commit.
ARG SWIFTYJSON_FIX_REPO=https://github.com/scade-platform/SwiftyJSON.git
ARG SWIFTYJSON_FIX_COMMIT=3594d05dfc1e55fe9319e5c08ee9a5b23b81d117
RUN git clone -q "$SWIFTYJSON_FIX_REPO" /deps/SwiftyJSON && \
    git -C /deps/SwiftyJSON checkout -q "$SWIFTYJSON_FIX_COMMIT"

FROM sdk-builder AS tester
WORKDIR /swift-sdk
RUN swift package resolve && swift package edit SwiftyJSON --path /deps/SwiftyJSON
RUN swift build --build-tests
COPY cross-sdk-tests/swift-wrapper/run-tests.sh /run-tests.sh
RUN chmod +x /run-tests.sh
CMD ["/run-tests.sh"]

FROM sdk-builder AS server
WORKDIR /app

COPY cross-sdk-tests/swift-wrapper /app

RUN cd /app && swift package resolve && swift package edit SwiftyJSON --path /deps/SwiftyJSON

RUN cd /app && swift build -c release

EXPOSE 3000

CMD ["/app/.build/release/Run"]
