# java-wrapper

Spring Boot wrapper around the ABSmartly Java SDK for the cross-SDK conformance suite.

## SDK source

By default the Docker build pulls the SDK from the sibling directory `java-sdk`
(relative to the build context, which is the parent of this repo — see
`docker-compose.yml`). This is what CI uses once a released SDK version is
published; `build.gradle` here pins `com.absmartly.sdk:core-api:1.6.3` from
`mavenLocal()`/`mavenCentral()`.

### Validating against an unreleased SDK branch

To run the suite against a local, unreleased SDK checkout (e.g. a feature-branch
worktree such as `~/git/java-sdk-holdouts` for the holdouts feature) without
touching the default CI path:

1. Check out the SDK branch as a sibling of `cross-sdk-tests`, e.g.
   `~/git/java-sdk-holdouts` (or `~/git/java-sdk` on a branch).
2. Build the java-sdk image with the `SDK_SOURCE_DIR` build arg pointed at that
   sibling directory's name, using the provided compose override:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.holdouts-local.yml build java-sdk
   docker compose -f docker-compose.yml -f docker-compose.holdouts-local.yml up -d java-sdk
   ```

   `SDK_SOURCE_DIR` defaults to `java-sdk` (the Dockerfile's `ARG`), so the
   override in `docker-compose.holdouts-local.yml` only changes which sibling
   directory gets `COPY`'d into the build and `publishToMavenLocal`'d — the
   wrapper's own `build.gradle` dependency version (`core-api:1.6.3`) does not
   need to change, since `mavenLocal()` is checked first and the worktree
   publishes under that same coordinate.
3. Run the orchestrator as usual (see `README_HOW_TO_RUN.md`); only the
   `java-sdk` service is affected, all other wrappers are unchanged.

This mechanism is a local validation convenience. Once the SDK feature ships in
a released version, bump `core-api` in `build.gradle` and drop back to the
default `SDK_SOURCE_DIR=java-sdk` sibling-checkout flow for CI.
