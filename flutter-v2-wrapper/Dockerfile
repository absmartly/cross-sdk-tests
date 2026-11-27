FROM ghcr.io/cirruslabs/flutter:3.22.0 AS sdk-builder

COPY flutter-sdk-v2/packages /flutter-sdk-v2/packages

FROM sdk-builder AS tester
WORKDIR /flutter-sdk-v2/packages/flutter_sdk
RUN flutter pub get
CMD ["flutter", "test"]

FROM sdk-builder AS server
WORKDIR /app

COPY cross-sdk-tests/flutter-v2-wrapper /app

RUN flutter pub get && flutter test test/widget_driver_test.dart

EXPOSE 3000

CMD ["flutter", "test", "--plain-name=Widget Test Server", "test/server_test.dart"]
