FROM ubuntu:22.04 AS build

RUN apt-get update && \
    apt-get install -y cmake g++ git curl ca-certificates libcurl4-openssl-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY cpp-sdk/ ./cpp-sdk/
COPY cross-sdk-tests/cpp-wrapper/ ./cross-sdk-tests/cpp-wrapper/

RUN mkdir -p build && cd build && \
    cmake ../cross-sdk-tests/cpp-wrapper -DCMAKE_BUILD_TYPE=Release -DABSMARTLY_BUILD_TESTS=OFF && \
    make -j$(nproc)

FROM build AS tester
RUN mkdir -p /app/build-tests && cd /app/build-tests && \
    cmake ../cpp-sdk -DCMAKE_BUILD_TYPE=Release -DABSMARTLY_BUILD_TESTS=ON && \
    make -j$(nproc)
WORKDIR /app/build-tests
CMD ["sh", "-c", "./tests/absmartly_tests --reporter JUnit 2>&1"]

# The wrapper build registers test_probes.cpp with ctest (BUILD_TESTING). The runtime stage copies the
# binary from here, so a failing probe regression fails the image build.
FROM build AS wrapper-tests
RUN cd /app/build && ctest --output-on-failure

FROM ubuntu:22.04

RUN apt-get update && \
    apt-get install -y libstdc++6 libcurl4 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=wrapper-tests /app/build/cpp-wrapper .

EXPOSE 3000

ENTRYPOINT ["./cpp-wrapper"]
