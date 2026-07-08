# Wrapper Repository Setup

Most wrappers live directly in this repository as `<sdk>-wrapper/` subdirectories
and are edited here. A couple also have standalone GitHub repositories for
external collaboration and PR submission; this document notes those.

## Kotlin Wrapper

**Repository:** https://github.com/absmartly/kotlin-wrapper
**Description:** Kotlin A/B testing SDK wrapper for cross-SDK tests
**Local path:** `kotlin-wrapper/`
**GitHub URL:** git@github.com:absmartly/kotlin-wrapper.git

To clone the standalone repository:
```bash
git clone https://github.com/absmartly/kotlin-wrapper.git
```

## Elixir Wrapper / SDK

**Repository:** https://github.com/absmartly/elixir-sdk
**Description:** Elixir A/B testing SDK for cross-SDK tests
**Local path:** `elixir-wrapper/` (the wrapper). The Elixir SDK itself is the
sibling `../elixir-sdk` repository, symlinked here as `elixir-sdk`.
**GitHub URL:** git@github.com:absmartly/elixir-sdk.git

To clone the standalone SDK repository:
```bash
git clone https://github.com/absmartly/elixir-sdk.git
```

## Usage in Cross-SDK Tests

The wrapper directories (`kotlin-wrapper/`, `elixir-wrapper/`, and the rest) are
part of this cross-sdk-tests repository and can be modified directly here. The
separate GitHub repositories above exist for external collaboration and PR
submissions.
