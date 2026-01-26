FROM golang:1.20-alpine

WORKDIR /app

COPY go-sdk /go-sdk
COPY cross-sdk-tests/go-wrapper/go.mod ./
COPY cross-sdk-tests/go-wrapper/server.go ./

RUN go mod tidy && go mod download

RUN go build -o server server.go

EXPOSE 3000

CMD ["./server"]
