# java-v1-wrapper

Spring Boot wrapper around the **1.x** ABSmartly Java SDK (`ABSmartly`, `ContextConfig`, `ContextEventLogger`) for the
cross-SDK conformance suite. It runs as the `java-v1` lane so the 1.x maintenance line keeps passing the contract
while 2.x is current; `java-wrapper` covers 2.x. CI clones `absmartly/java-sdk` at the `java-sdk-v1` pin in
`sdk-branches.sh` (`release/1.x` once it exists, until then the last 1.x tag) into the `java-sdk-v1` sibling directory.

## SDK source

By default the Docker build copies the SDK from the sibling directory `java-sdk-v1`
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
2. Build the java-sdk image with the compose override, setting `JAVA_SDK_V1_DIR` to that
   directory name (it feeds `SDK_SOURCE_DIR`; unset, it stays `java-sdk-v1`):

   ```bash
   JAVA_SDK_V1_DIR=java-sdk-holdouts docker compose -f docker-compose.yml -f docker-compose.local-sdk.yml build java-v1-sdk
   JAVA_SDK_V1_DIR=java-sdk-holdouts docker compose -f docker-compose.yml -f docker-compose.local-sdk.yml up -d java-v1-sdk

   # any other sibling checkout, e.g. the switchback branch
   JAVA_SDK_V1_DIR=java-sdk-switchback \
     docker compose -f docker-compose.yml -f docker-compose.local-sdk.yml build java-v1-sdk
   ```

   `SDK_SOURCE_DIR` defaults to `java-sdk-v1` (the Dockerfile's `ARG`), so the override
   only changes which sibling directory gets `COPY`'d into the build; the wrapper
   picks up whatever version that checkout declares.
3. Run the orchestrator as usual (see `README_HOW_TO_RUN.md`); only the `java-v1-sdk`
   service is affected, all other wrappers are unchanged.

This mechanism is a local validation convenience; CI uses the default
`SDK_SOURCE_DIR=java-sdk-v1` sibling checkout.
