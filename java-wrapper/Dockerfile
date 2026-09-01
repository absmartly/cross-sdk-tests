FROM gradle:8.5-jdk11 AS sdk-builder

WORKDIR /build

# core-api version published to the local maven repo. Must match the
# `com.absmartly.sdk:core-api:<version>` dependency in java-wrapper/build.gradle
# and VERSION in java-sdk/build.gradle. Override at build time if the SDK bumps.
ARG CORE_API_VERSION=1.6.3

# The SDK targets Java 1.6 for the Android SDK, and javac dropped -source/-target 6 in
# JDK 12, so core-api must be built on JDK 11 or older. Its test suite needs Mockito 5,
# which needs JDK 11+, so 11 is the only version that satisfies both. Spring Boot 3 needs
# JDK 17 for the wrapper, so the two builds get their own stages and meet at the local
# maven repository copied forward below.
COPY java-sdk /build/java-sdk
RUN cd /build/java-sdk && ./gradlew :core-api:jar -x test && \
    mkdir -p /root/.m2/repository/com/absmartly/sdk/core-api/${CORE_API_VERSION} && \
    cp /build/java-sdk/core-api/build/libs/core-api.jar /root/.m2/repository/com/absmartly/sdk/core-api/${CORE_API_VERSION}/core-api-${CORE_API_VERSION}.jar && \
    cd /build/java-sdk && ./gradlew :core-api:generatePomFileForReleasePublication -x test && \
    cp /build/java-sdk/core-api/build/publications/release/pom-default.xml /root/.m2/repository/com/absmartly/sdk/core-api/${CORE_API_VERSION}/core-api-${CORE_API_VERSION}.pom

FROM gradle:8.5-jdk17 AS builder

WORKDIR /build

COPY --from=sdk-builder /root/.m2 /root/.m2
COPY cross-sdk-tests/java-wrapper /build/java-wrapper
RUN cd /build/java-wrapper && gradle bootJar -x test

# core-api's own tests run on the JDK that can build it, not the wrapper's.
FROM sdk-builder AS tester
WORKDIR /build/java-sdk
RUN ./gradlew :core-api:compileTestJava -x test
CMD ["./gradlew", ":core-api:test", "--rerun-tasks"]

FROM amazoncorretto:17

WORKDIR /app

COPY --from=builder /build/java-wrapper/build/libs/*.jar /app/app.jar

EXPOSE 3000

CMD ["java", "-jar", "/app/app.jar"]
