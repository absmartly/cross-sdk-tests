FROM ghcr.io/cirruslabs/flutter:3.22.0 AS sdk-builder

COPY flutter-sdk/packages /flutter-sdk/packages

FROM sdk-builder AS tester
WORKDIR /flutter-sdk/packages/flutter_sdk
RUN flutter pub get
CMD ["flutter", "test"]

FROM sdk-builder AS server
WORKDIR /app

COPY cross-sdk-tests/flutter-wrapper /app

RUN flutter pub get

EXPOSE 3000

CMD ["flutter", "test", "--plain-name=Widget Test Server", "test/server_test.dart"]
