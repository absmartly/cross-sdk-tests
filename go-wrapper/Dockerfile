FROM golang:1.23-alpine AS sdk-builder

COPY go-sdk /go-sdk

FROM sdk-builder AS tester
WORKDIR /go-sdk
RUN go test ./... -count=0 2>&1 || true
CMD ["go", "test", "-v", "./..."]

FROM sdk-builder AS server
WORKDIR /app

COPY cross-sdk-tests/go-wrapper/go.mod ./
COPY cross-sdk-tests/go-wrapper/go.sum ./
COPY cross-sdk-tests/go-wrapper/server.go ./

RUN go mod download

RUN go build -o server server.go

EXPOSE 3000

CMD ["./server"]
