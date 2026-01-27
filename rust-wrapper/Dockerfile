FROM rust:1.82-alpine as builder

RUN apk add --no-cache musl-dev

WORKDIR /app

COPY rust-sdk /rust-sdk
COPY cross-sdk-tests/rust-wrapper/Cargo.toml ./
COPY cross-sdk-tests/rust-wrapper/src ./src

# Update the path to rust-sdk for docker build context
RUN sed -i 's|path = "../../rust-sdk"|path = "/rust-sdk"|g' Cargo.toml

RUN cargo build --release

FROM alpine:3.19

RUN apk add --no-cache ca-certificates

WORKDIR /app

COPY --from=builder /app/target/release/rust-wrapper ./server

EXPOSE 3000

CMD ["./server"]
