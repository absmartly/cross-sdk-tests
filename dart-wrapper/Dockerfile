FROM dart:stable AS sdk-builder

COPY flutter-sdk/packages/dart_sdk /flutter-sdk/packages/dart_sdk

FROM sdk-builder AS tester
WORKDIR /flutter-sdk/packages/dart_sdk
RUN dart pub get && dart run build_runner build --delete-conflicting-outputs
CMD ["dart", "test"]

FROM sdk-builder AS server
WORKDIR /app

COPY cross-sdk-tests/dart-wrapper /app

RUN dart pub get

# test_switchback.dart ships with the wrapper directory; running it here fails the image build
# (and so the dart lane) when the switchback probe regresses.
RUN dart run test_switchback.dart

EXPOSE 3000

CMD ["dart", "run", "server.dart"]
