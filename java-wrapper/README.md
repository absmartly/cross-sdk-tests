# java-wrapper

Spring Boot wrapper around the ABSmartly Java SDK for the cross-SDK conformance suite.

## SDK source

By default the Docker build copies the SDK from the sibling directory `java-sdk`
(relative to the build context, which is the parent of this repo — see
`docker-compose.yml`). The Dockerfile builds the SDK jar, installs it to a local
Maven cache at the pinned `CORE_API_VERSION` coordinate, and `build.gradle` resolves
it from `mavenLocal()`.

### Validating against an unreleased SDK branch

To run the suite against a local, unreleased SDK checkout (e.g. a feature-branch
worktree) without touching the default CI path:

1. Check out the SDK branch as a sibling of `cross-sdk-tests`, naming the directory
   `java-sdk-holdouts` — that name must match the `SDK_SOURCE_DIR` value committed in
   `docker-compose.local-sdk.yml`.
2. Build the java-sdk image with the compose override that sets `SDK_SOURCE_DIR`:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.local-sdk.yml build java-sdk
   docker compose -f docker-compose.yml -f docker-compose.local-sdk.yml up -d java-sdk
   ```

   `SDK_SOURCE_DIR` defaults to `java-sdk` (the Dockerfile's `ARG`), so the override
   only changes which sibling directory gets `COPY`'d into the build — the wrapper's
   own `build.gradle` dependency version (`core-api:1.6.3`) does not need to change,
   since `mavenLocal()` is checked first and the build installs the jar under that
   same coordinate.
3. Run the orchestrator as usual (see `README_HOW_TO_RUN.md`); only the `java-sdk`
   service is affected, all other wrappers are unchanged.

This mechanism is a local validation convenience. Once the SDK feature ships in a
released version, bump `core-api` in `build.gradle` and drop back to the default
`SDK_SOURCE_DIR=java-sdk` sibling-checkout flow for CI.
