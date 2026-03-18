FROM eclipse-temurin:17-jdk AS builder

WORKDIR /app

RUN apt-get update && \
    apt-get install -y curl gnupg && \
    curl -sL "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x2EE0EA64E40A89B84B2DF73499E82A75642AC823" | gpg --dearmor > /usr/share/keyrings/sbt-archive-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/sbt-archive-keyring.gpg] https://repo.scala-sbt.org/scalasbt/debian all main" | tee /etc/apt/sources.list.d/sbt.list && \
    echo "deb [signed-by=/usr/share/keyrings/sbt-archive-keyring.gpg] https://repo.scala-sbt.org/scalasbt/debian /" | tee /etc/apt/sources.list.d/sbt_old.list && \
    apt-get update && \
    apt-get install -y sbt && \
    rm -rf /var/lib/apt/lists/*

COPY scala-sdk /app/scala-sdk

COPY cross-sdk-tests/scala-wrapper /app/scala-wrapper

WORKDIR /app/scala-wrapper
RUN sbt -J-Xms128M -J-Xmx512M assembly

FROM builder AS tester
WORKDIR /app/scala-sdk
RUN sbt -J-Xms128M -J-Xmx512M "Test / compile"
CMD ["sbt", "test"]

FROM eclipse-temurin:17-jre

WORKDIR /app

RUN useradd --system --uid 10001 --create-home app

COPY --from=builder --chown=app:app /app/scala-wrapper/target/scala-2.13/scala-wrapper.jar /app/wrapper.jar

EXPOSE 3000

USER app

CMD ["java", "-jar", "/app/wrapper.jar"]
