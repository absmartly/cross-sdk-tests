FROM dart:stable

WORKDIR /app

COPY flutter-sdk/packages/dart_sdk /flutter-sdk/packages/dart_sdk
COPY cross-sdk-tests/dart-wrapper /app

RUN dart pub get

EXPOSE 3000

CMD ["dart", "run", "server.dart"]
