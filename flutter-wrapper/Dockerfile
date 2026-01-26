FROM ghcr.io/cirruslabs/flutter:3.22.0

WORKDIR /app

# Copy both dart_sdk and flutter_sdk packages (flutter_sdk depends on dart_sdk)
COPY flutter-sdk/packages /flutter-sdk/packages
COPY cross-sdk-tests/flutter-wrapper /app

RUN flutter pub get

EXPOSE 3000

CMD ["flutter", "test", "--plain-name=Widget Test Server", "test/server_test.dart"]
