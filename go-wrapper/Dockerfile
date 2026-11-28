FROM golang:latest AS sdk-builder

ARG GO_SDK_DIR=go-sdk
COPY ${GO_SDK_DIR} /go-sdk

FROM sdk-builder AS tester
WORKDIR /go-sdk
RUN go test ./... -count=0 2>&1 || true
CMD ["go", "test", "-v", "./..."]

FROM sdk-builder AS server
WORKDIR /app

COPY cross-sdk-tests/go-wrapper/go.mod ./
COPY cross-sdk-tests/go-wrapper/*.go ./

RUN go mod tidy && go vet ./... && go test ./... && go build -o server .

EXPOSE 3000

CMD ["./server"]
