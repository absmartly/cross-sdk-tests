# Wrapper Repository Setup

This document describes the GitHub repositories created for the SDK wrappers used in cross-SDK testing.

## Kotlin Wrapper

**Repository:** https://github.com/absmartly/kotlin-wrapper
**Description:** Kotlin A/B testing SDK wrapper for cross-SDK tests
**Status:** 138/138 cross-SDK tests passing
**Local Path:** /cross-sdk-tests/kotlin-wrapper
**GitHub URL:** git@github.com:absmartly/kotlin-wrapper.git

To clone the standalone repository:
```bash
git clone https://github.com/absmartly/kotlin-wrapper.git
```

## Elixir SDK

**Repository:** https://github.com/absmartly/elixir-sdk
**Description:** Elixir A/B testing SDK for cross-SDK tests
**Status:** Cross-SDK suite is expected to run with no async capability skips.
**Local Path:** /cross-sdk-tests/elixir-wrapper
**GitHub URL:** git@github.com:absmartly/elixir-sdk.git

To clone the standalone repository:
```bash
git clone https://github.com/absmartly/elixir-sdk.git
```

## Usage in Cross-SDK Tests

Both wrappers are included as subdirectories in the cross-sdk-tests repository:
- kotlin-wrapper/
- elixir-wrapper/

These directories are part of the main cross-sdk-tests repository and can be modified directly here.
The separate GitHub repositories (kotlin-wrapper and elixir-sdk) are for external collaboration and PR submissions.
