FROM rust:latest AS builder

RUN apt-get update && apt-get install -y --no-install-recommends musl-tools pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY rust-sdk /rust-sdk
COPY cross-sdk-tests/rust-wrapper/Cargo.toml ./
COPY cross-sdk-tests/rust-wrapper/src ./src

# Update the path to rust-sdk for docker build context
RUN sed -i 's|path = "../../rust-sdk"|path = "/rust-sdk"|g' Cargo.toml

RUN cargo build --release

FROM builder AS tester
WORKDIR /rust-sdk
CMD ["cargo", "test"]

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libssl3 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/target/release/rust-wrapper ./server

EXPOSE 3000

CMD ["./server"]
