FROM gradle:8.5-jdk17 AS sdk-builder

WORKDIR /build

# SDK_SOURCE_DIR lets local validation point at an unreleased SDK checkout (e.g. a
# feature-branch worktree) that sits alongside cross-sdk-tests in the build context.
# CI keeps the default "java-sdk" sibling checkout; see docker-compose.local-sdk.yml
# for the override, which takes the directory from JAVA_SDK_DIR.
ARG SDK_SOURCE_DIR=java-sdk

COPY ${SDK_SOURCE_DIR} /build/java-sdk
# Install the published (shaded) jar into the local Maven repo under the version the SDK's own pom
# declares (java-sdk VERSION), and record it for the wrapper build. A mismatch
# between the path version and the pom version makes Gradle ignore mavenLocal
# and silently resolve the released core-api from Maven Central instead.
# core-api 2.x publishes the shaded jar (Jackson relocated) as core-api.jar; core-api-plain.jar is the unshaded one.
RUN cd /build/java-sdk && ./gradlew :core-api:shadowJar :core-api:generatePomFileForReleasePublication -x test && \
    POM=/build/java-sdk/core-api/build/publications/release/pom-default.xml && \
    CORE_API_VERSION=$(sed -n 's:^  <version>\(.*\)</version>$:\1:p' "$POM" | head -1) && \
    test -n "$CORE_API_VERSION" && \
    DEST=/root/.m2/repository/com/absmartly/sdk/core-api/${CORE_API_VERSION} && \
    mkdir -p "$DEST" && \
    cp /build/java-sdk/core-api/build/libs/core-api.jar "$DEST/core-api-${CORE_API_VERSION}.jar" && \
    cp "$POM" "$DEST/core-api-${CORE_API_VERSION}.pom" && \
    echo "$CORE_API_VERSION" > /root/.m2/core-api.version

FROM gradle:8.5-jdk17 AS builder

WORKDIR /build

COPY --from=sdk-builder /root/.m2 /root/.m2
COPY cross-sdk-tests/java-wrapper /build/java-wrapper
RUN cd /build/java-wrapper && gradle test bootJar --no-daemon

# core-api's own tests (the JDK 25 lane is skipped: the image has no JDK 25 and the SDK's CI covers it).
FROM sdk-builder AS tester
WORKDIR /build/java-sdk
RUN ./gradlew :core-api:compileTestJava -x test
CMD ["./gradlew", ":core-api:test", "-PskipJdk25", "--rerun-tasks"]

FROM amazoncorretto:17

WORKDIR /app

COPY --from=builder /build/java-wrapper/build/libs/*.jar /app/app.jar

EXPOSE 3000

CMD ["java", "-jar", "/app/app.jar"]
