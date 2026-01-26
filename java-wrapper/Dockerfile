FROM gradle:6.9-jdk8 AS builder

WORKDIR /build

COPY java-sdk /build/java-sdk
RUN cd /build/java-sdk && ./gradlew publishToMavenLocal -x test -x signReleasePublication

COPY cross-sdk-tests/java-wrapper /build/java-wrapper
RUN cd /build/java-wrapper && gradle bootJar -x test

FROM amazoncorretto:8

WORKDIR /app

COPY --from=builder /build/java-wrapper/build/libs/*.jar /app/app.jar

EXPOSE 3000

CMD ["java", "-jar", "/app/app.jar"]
