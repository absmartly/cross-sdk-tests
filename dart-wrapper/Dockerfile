FROM dart:stable AS sdk-builder

COPY flutter-sdk/packages/dart_sdk /flutter-sdk/packages/dart_sdk

FROM sdk-builder AS tester
WORKDIR /flutter-sdk/packages/dart_sdk
RUN dart pub get
CMD ["dart", "test"]

FROM sdk-builder AS server
WORKDIR /app

COPY cross-sdk-tests/dart-wrapper /app

RUN dart pub get

EXPOSE 3000

CMD ["dart", "run", "server.dart"]
