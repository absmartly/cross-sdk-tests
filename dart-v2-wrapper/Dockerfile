FROM dart:stable AS sdk-builder

COPY flutter-sdk-v2/packages/dart_sdk /flutter-sdk-v2/packages/dart_sdk

FROM sdk-builder AS tester
WORKDIR /flutter-sdk-v2/packages/dart_sdk
RUN dart pub get && dart run build_runner build --delete-conflicting-outputs
CMD ["dart", "test"]

FROM sdk-builder AS server
WORKDIR /app

COPY cross-sdk-tests/dart-v2-wrapper /app

RUN dart pub get

EXPOSE 3000

CMD ["dart", "run", "server.dart"]
