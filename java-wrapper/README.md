# java-wrapper

Spring Boot wrapper around the **2.x** ABsmartly Java SDK (`ABsmartlyClient`, `ContextOptions`, typed events) for the
cross-SDK conformance suite. The 1.x line runs as the `java-v1` lane from `java-v1-wrapper`.

Every context gets its own `ABsmartlyClient` over a `WrapperTransport`: in memory it answers the SDK's own
`GET /context` (with the next `refresh` `newData` payload, or no experiments) and `PUT /context`, so the SDK's real
client, decoding and publish path run without a server. An `endpoint` enables HTTP fetching through the SDK's
default `JdkHttpTransport`; HTTP publishing also requires `publishToEndpoint: true`. Without that flag, publishes
still complete in memory. `/publishFail` arms a one-shot `500 Publish failed`. Events come from an `EventListener`
attached through `ContextOptions.eventListener`, reported under the 1.x names (`CLOSE` is `finalize`).

The "no environment configured" case (`clientNoEnvironment`) cannot be built through `ABsmartlyClient.builder()`,
which requires an environment; `com.absmartly.sdk.WrapperNoEnvironment` builds that context with the SDK's own
package-private constructors (no reflection), as the 1.x wrapper's `NoEnvironmentClient` did.

## SDK source

By default the Docker build copies the SDK from the sibling directory `java-sdk`
(relative to the build context, which is the parent of this repo — see
`docker-compose.yml`). The Dockerfile builds the SDK jar, installs it to a local
Maven cache under the version its own pom declares, and records that version in
`~/.m2/core-api.version`, which `build.gradle` reads to resolve the jar from
`mavenLocal()`. Installing it under any other version makes Gradle ignore the local
copy and fetch the released `core-api` from Maven Central instead.

### Validating against an unreleased SDK branch

To run the suite against a local, unreleased SDK checkout (e.g. a feature-branch
worktree) without touching the default CI path:

1. Check out the SDK branch as a sibling of `cross-sdk-tests` (e.g. `java-sdk-holdouts`).
2. Build the java-sdk image with the compose override, setting `JAVA_SDK_DIR` to that
   directory name (it feeds `SDK_SOURCE_DIR`; unset, it stays `java-sdk`):

   ```bash
   JAVA_SDK_DIR=java-sdk-holdouts docker compose -f docker-compose.yml -f docker-compose.local-sdk.yml build java-sdk
   JAVA_SDK_DIR=java-sdk-holdouts docker compose -f docker-compose.yml -f docker-compose.local-sdk.yml up -d java-sdk

   # any other sibling checkout, e.g. the switchback branch
   JAVA_SDK_DIR=java-sdk-switchback \
     docker compose -f docker-compose.yml -f docker-compose.local-sdk.yml build java-sdk
   ```

   `SDK_SOURCE_DIR` defaults to `java-sdk` (the Dockerfile's `ARG`), so the override
   only changes which sibling directory gets `COPY`'d into the build; the wrapper
   picks up whatever version that checkout declares.
3. Run the orchestrator as usual (see `README_HOW_TO_RUN.md`); only the `java-sdk`
   service is affected, all other wrappers are unchanged.

This mechanism is a local validation convenience; CI uses the default
`SDK_SOURCE_DIR=java-sdk` sibling checkout.
