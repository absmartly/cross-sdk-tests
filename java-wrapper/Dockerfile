FROM gradle:6.9-jdk8 AS builder

WORKDIR /build

# SDK_SOURCE_DIR lets local validation point at an unreleased SDK checkout (e.g. a
# feature-branch worktree) that sits alongside cross-sdk-tests in the build context.
# CI keeps the default "java-sdk" sibling checkout; see docker-compose.holdouts-local.yml
# for the override used to validate against ~/git/java-sdk-holdouts.
ARG SDK_SOURCE_DIR=java-sdk

COPY ${SDK_SOURCE_DIR} /build/java-sdk
RUN cd /build/java-sdk && ./gradlew publishToMavenLocal -x test -x signReleasePublication

COPY cross-sdk-tests/java-wrapper /build/java-wrapper
RUN cd /build/java-wrapper && gradle bootJar -x test

FROM amazoncorretto:8

WORKDIR /app

COPY --from=builder /build/java-wrapper/build/libs/*.jar /app/app.jar

EXPOSE 3000

CMD ["java", "-jar", "/app/app.jar"]
