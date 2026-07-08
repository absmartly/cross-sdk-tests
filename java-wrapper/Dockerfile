FROM gradle:8.5-jdk17 AS builder

WORKDIR /build

# core-api version published to the local maven repo. Must match the
# `com.absmartly.sdk:core-api:<version>` dependency in java-wrapper/build.gradle
# and VERSION in java-sdk/build.gradle. Override at build time if the SDK bumps.
ARG CORE_API_VERSION=1.6.3

COPY java-sdk /build/java-sdk
RUN cd /build/java-sdk && ./gradlew :core-api:jar -x test && \
    mkdir -p /root/.m2/repository/com/absmartly/sdk/core-api/${CORE_API_VERSION} && \
    cp /build/java-sdk/core-api/build/libs/core-api.jar /root/.m2/repository/com/absmartly/sdk/core-api/${CORE_API_VERSION}/core-api-${CORE_API_VERSION}.jar && \
    cd /build/java-sdk && ./gradlew :core-api:generatePomFileForReleasePublication -x test && \
    cp /build/java-sdk/core-api/build/publications/release/pom-default.xml /root/.m2/repository/com/absmartly/sdk/core-api/${CORE_API_VERSION}/core-api-${CORE_API_VERSION}.pom

COPY cross-sdk-tests/java-wrapper /build/java-wrapper
RUN cd /build/java-wrapper && gradle bootJar -x test

FROM builder AS tester
WORKDIR /build/java-sdk
RUN ./gradlew :core-api:compileTestJava -x test
CMD ["./gradlew", ":core-api:test", "--rerun-tasks"]

FROM amazoncorretto:17

WORKDIR /app

COPY --from=builder /build/java-wrapper/build/libs/*.jar /app/app.jar

EXPOSE 3000

CMD ["java", "-jar", "/app/app.jar"]
