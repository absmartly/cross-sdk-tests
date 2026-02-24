FROM gradle:8.5-jdk17 AS builder

WORKDIR /build

COPY java-sdk /build/java-sdk
RUN cd /build/java-sdk && ./gradlew :core-api:jar -x test && \
    mkdir -p /root/.m2/repository/com/absmartly/sdk/core-api/1.6.3 && \
    cp /build/java-sdk/core-api/build/libs/core-api.jar /root/.m2/repository/com/absmartly/sdk/core-api/1.6.3/core-api-1.6.3.jar && \
    cd /build/java-sdk && ./gradlew :core-api:generatePomFileForReleasePublication -x test && \
    cp /build/java-sdk/core-api/build/publications/release/pom-default.xml /root/.m2/repository/com/absmartly/sdk/core-api/1.6.3/core-api-1.6.3.pom

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
