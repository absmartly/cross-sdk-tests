FROM gradle:8.5-jdk17 AS build

WORKDIR /build

COPY kotlin-sdk /build/kotlin-sdk
COPY cross-sdk-tests/kotlin-wrapper /build/kotlin-wrapper

WORKDIR /build/kotlin-wrapper
RUN gradle shadowJar --no-daemon

FROM build AS tester
WORKDIR /build/kotlin-sdk
CMD ["./gradlew", "test"]

FROM eclipse-temurin:17-jre

WORKDIR /app

COPY --from=build /build/kotlin-wrapper/build/libs/*-all.jar /app/wrapper.jar

EXPOSE 3000

ENTRYPOINT []
CMD ["java", "-Xmx256m", "-Xms128m", "-jar", "/app/wrapper.jar"]
