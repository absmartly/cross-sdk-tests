# Context fetch compression: what each SDK asks for and decodes

Survey of the SDK's own HTTP fetch of `GET {endpoint}/context`, the request the
SDKs make on context creation and refresh. Written 2026-11-12 against
`absmartly/cross-sdk-tests` 30d23e9 and the SDK branches pinned in
`sdk-branches.sh` (checked out at the commits listed per SDK).

Why it matters: Cloudflare in front of the Collector serves `/v1/context` as
zstd > br > gzip, picked from the request's `Accept-Encoding`. On a large tenant
(441 experiments) the body is 234 KB as zstd, 321 KB br, 420 KB gzip and
1,554,927 B when the request sends no `Accept-Encoding`.

How it was measured: the negotiating context server (`context-server/`) records
the `Accept-Encoding` and `User-Agent` of every `GET /context` per payload and
serves gzip when the request advertised it, identity otherwise (it never serves
br or zstd; whether an SDK lists them is only reported). The orchestrator probes
each SDK once at startup (create, then a refresh after the server's data
changed, both negotiated) and scenarios 230 and 232 check every fetch. A second,
REPORT-ONLY "forced gzip" probe makes the server send gzip whatever the request
said, without changing the SDK's `Accept-Encoding` (5 s timeout, gates nothing;
`forcedGzip` in `report.json`, with `requested` saying whether the header
advertised gzip). For an SDK whose recorded header did not advertise gzip (java,
kotlin, php, go, rust, cpp, and from code dotnet and elixir) that body is
**unrequested**, and the result is the evidence below for which SDKs fail on
it. For a gzip-advertising SDK it is "forced gzip (requested)": an ordinary
gzip response, so it shows nothing beyond the negotiated probe.
The recorded User-Agent is a heuristic, not proof of who fetched: a wrapper that
shares the SDK's HTTP stack (node's fetch, Python's `requests`) sends the same
User-Agent and is indistinguishable by it. As in `WRAPPER_API_SPEC.md`, the only
guarantee that the SDK's own client made the request is code review of the wrapper.
"Observed" below means seen on the wire from that SDK's lane in this sandbox.
"From code" means read from source with no lane run, because the image could not
be built here (no network access to nuget.org, hex.pm, repo.scala-sbt.org,
pub.dev or registry.yarnpkg.com; the swift image fails its own build-time test).
CI runs the full matrix.

The wrapper must use the SDK's default client for the observation to say
anything about the SDK default. Where a wrapper injects its own transport, this is
stated.

## java (java-sdk `feat/d1-reads-throw-before-ready` 77e1f4f)

- Client: Apache httpclient5 **async** (`HttpAsyncClientBuilder`),
  `core-api/src/main/java/com/absmartly/sdk/DefaultHTTPClient.java:51`. The SDK
  declares 5.1.3 (`core-api/build.gradle:9`). The lane runs **5.2.1**: the wrapper
  is a Spring Boot app and Boot's dependency management pins httpclient5, as the
  User-Agent `Apache-HttpAsyncClient/5.2.1 (Java/17.0.21)` shows. An application
  without Boot gets 5.1.3. The wrapper uses the SDK's `Client`/`DefaultHTTPClient`
  (`java-wrapper/.../WrapperController.java:297-320`), so the lane shows the SDK default.
- Accept-Encoding: **observed none**, on create and on refresh.
  `Client.getContextData` passes `null` headers (`core-api/.../Client.java:99`).
- Decodes: nothing. Async httpclient5 below 5.6 has no content decompression.
  Forced gzip (unrequested: no gzip advertised), **observed**: the context loads as failed (`ready=true, failed=true`).
- User-configurable today: only by supplying a custom `HTTPClient` implementation.
  `DefaultHTTPClientConfig` has no compression switch.
- Minimal fix: httpclient5 **5.6** adds transparent async decompression.
  `HttpAsyncClientBuilder` installs `ContentCompressionAsyncExec` unless
  `disableContentCompression()` is called. It sends `Accept-Encoding: gzip, x-gzip, deflate`
  and decodes them (`ContentCompressionAsyncExec.java:92-119` in 5.6.5). The 5.6
  release notes list "transparent content decompression and content compression with
  deflate, gzip, zstd (optional), and brotli (optional) codecs in the async transport".
  5.6.5 also fixes an async Brotli decompression hang. Caveat: httpclient5 5.2+
  needs Java 8, and java-sdk compiles with `sourceCompatibility = "1.6"`
  (`build.gradle:40`), so the bump also needs a Java 8 floor. On 5.1.x (the
  current Java 6/7 compatible line) the alternative is to set
  `Accept-Encoding: gzip` in `Client.getContextData` and gunzip in `Client`
  when `Content-Encoding: gzip`, with `java.util.zip.GZIPInputStream`. That needs
  a `getContentEncoding` on the SDK's `HTTPClient.Response`.
- br/zstd: not without new native dependencies (brotli4j, zstd-jni). gzip only.

## kotlin (kotlin-sdk `feat/public-hashing-object-take2` 8f68ec5)

- Client: `java.net.HttpURLConnection`, `src/main/kotlin/com/absmartly/sdk/DefaultHTTPClient.kt:64`.
  The wrapper uses the SDK's `Client` (`kotlin-wrapper/.../Application.kt:234-239`).
- Accept-Encoding: **observed none** (create and refresh). User-Agent `Java/17.0.21`.
- Decodes: nothing. `readResponse` copies the raw stream (`DefaultHTTPClient.kt:95-105`).
  Forced gzip (unrequested), **observed**: failed context, `IllegalStateException: Failed to deserialize context data`.
- Configurable today: only through a custom `HTTPClient`.
- Minimal fix: in `execute` set `Accept-Encoding: gzip` and wrap the stream in
  `GZIPInputStream` when `connection.contentEncoding == "gzip"` (JDK only).
  Or move to `java.net.http.HttpClient` (also no transparent decompression).
- br/zstd: not in the JDK. gzip only.

## scala (scala-sdk `chore/review-follow-ups` 63fe0af) — lane not built here

- Client: sttp client3 3.9.2 with `HttpURLConnectionBackend`
  (`src/main/scala/com/absmartly/sdk/SDK.scala:34`, fetch at `:121-126` via `basicRequest`).
  The wrapper builds an `SDK` with the SDK's own config (`scala-wrapper/.../Server.scala:55-57`).
- Accept-Encoding, **from code**: `gzip, deflate`. sttp's `basicRequest` is
  `emptyRequest.acceptEncoding("gzip, deflate")` (`sttp/client3/SttpApi.scala:44`).
- Decodes, from code: gzip and deflate (`HttpURLConnectionBackend.scala:283-291`);
  any other coding throws `UnsupportedEncodingException`.
- Fix: none needed for gzip. Policy: `required` from this reading; the CI lane proves it.
- br/zstd: not without extra codecs. gzip is enough.

## dotnet (dotnet-sdk `fix/audit-operator-fixes` ea172a6) — lane not built here

- Client: `HttpClient` from `IHttpClientFactory`. With DI
  (`DependencyInjection/ServiceCollectionExtensions.cs:46-73`) the primary handler is
  `SocketsHttpHandler` (net8/9) or `HttpClientHandler` (netstandard2.0), and neither
  sets `AutomaticDecompression`. The fetch is `ABSmartlyService.cs:126`.
- **The wrapper injects its own transport**: `DummyHttpClientFactory` and
  `DummyHttpClient` wrap a bare `new HttpClient()` (`dotnet-wrapper/Program.cs:1299-1340`).
  The lane therefore shows .NET's `HttpClient` default, not the SDK's DI handler.
  Both send nothing, so the result would be the same here.
- Accept-Encoding, **from code**: none. `HttpClient` adds it only when the handler's
  `AutomaticDecompression` is set.
- Decodes, from code: nothing. A gzip body reaches the JSON deserializer and fails.
- Configurable today: yes for DI users. `AddABsmartly` returns nothing, but a user
  can call `services.AddHttpClient(ABsmartly.HttpClientName).ConfigurePrimaryHttpMessageHandler(...)`
  after it. Users of the explicit-factory constructors supply their own client.
- Minimal fix: `AutomaticDecompression = DecompressionMethods.All` on the
  `SocketsHttpHandler` (`.All` includes Brotli on .NET Core 3.0+), and
  `DecompressionMethods.GZip | DecompressionMethods.Deflate` on the netstandard2.0 `HttpClientHandler`.
- br/zstd: br built into .NET Core 3.0+ (no dependency). zstd is not built into .NET.

## php (php-sdk `fix/audit-operator-fixes` a59b1e4)

- Default client: curl, `src/Http/HTTPClient.php` (options at `:212-219`, per-request at `:92-95`).
  **The wrapper injects `ReactHttpClient`** (ReactPHP `Browser`, `src/Http/ReactHttpClient.php:38`)
  for every endpoint-backed context (`php-wrapper/server.php:367-370`), so the lane
  shows the ReactPHP client, not the curl default.
- Accept-Encoding: **observed none** with ReactHttpClient (User-Agent `ReactPHP/1`).
  From code, the curl default sets no `CURLOPT_ENCODING`, so curl sends none either.
- Decodes: ReactHttpClient, **observed**: nothing. Forced gzip (unrequested) gives a failed
  context, `Control character error, possibly incorrectly encoded` (json_decode on
  gzip bytes). The curl default, from code: nothing, since curl decodes only when
  `CURLOPT_ENCODING` is set.
- Configurable today: no (no option, and `curlInit` is private).
- Minimal fix: curl client, `CURLOPT_ENCODING => ''` in `curlInit`. curl then
  advertises and decodes every coding it was built with (the php:8.4 image's
  libcurl 8.14.1 has gzip, br and zstd). ReactHttpClient: send
  `Accept-Encoding: gzip` and `gzdecode` the body when `Content-Encoding: gzip`
  (ReactPHP `Browser` does not decode by itself).
- br/zstd: through curl when built with them. gzip via ext-zlib otherwise.

## python (python3-sdk — pinned `fix/python-sdk-all-tests-passing` no longer exists; ran `main` 2fe0206)

- Client: `requests.Session` (`sdk/default_http_client.py:12`; `requests >= 2.28.1`;
  the lane ran requests 2.34.2). The wrapper uses the SDK's `DefaultHTTPClient`
  (`python-wrapper/server.py:246-248`).
- Accept-Encoding: **observed `gzip, deflate`** (create and refresh), served gzip.
- Decodes: gzip, deflate (urllib3). Forced gzip (requested), **observed**: decoded and loaded.
- Fix: none for gzip. The lane image has urllib3 2.8.0, whose `ACCEPT_ENCODING` is
  `gzip,deflate`. urllib3 adds `br` when `brotli`/`brotlicffi` is importable and
  `zstd` when its zstd support is available (optional packages, not installed here).
- Note: the pinned branch is gone, so CI's `git clone --branch` fails for this lane. Unrelated to this change.

## go (go-sdk `fix/all-tests-passing` d6541a2)

- Client: resty v2.16.5 over a custom `http.Transport` (`sdk/DefaultHttpClient.go:43-53`).
  The wrapper uses `sdk.CreateDefaultClient` (`go-wrapper/server.go:413`).
- Accept-Encoding: **observed none**. The SDK sets `DisableCompression: true`
  (`sdk/DefaultHttpClient.go:50`), which turns off the transport's automatic `gzip`.
- Decodes: resty gunzips any `Content-Encoding: gzip` body (resty `client.go:1274-1276`).
  Forced gzip (unrequested), **observed**: decoded and loaded fine.
- Configurable today: no; the transport is built inside `DefaultHttpClientConfig`.
- Minimal fix: drop `DisableCompression: true`. The Go transport then sends
  `Accept-Encoding: gzip` and decodes transparently. Do not also set the header by
  hand: that turns off Go's transparent decoding (resty would still gunzip).
- br/zstd: not in the Go standard library. gzip only.

## ruby (ruby-sdk `feat/ruby-3.3-compatibility` 7be6b04) and liquid (wraps ruby-sdk)

- Client: Faraday 2.8.1 with `net_http_persistent` (`lib/default_http_client.rb:24-31`).
  Both wrappers use the SDK's `Client.create` (`ruby-wrapper/server.rb:164-170`,
  `liquid-wrapper/server.rb:294-300`).
- Accept-Encoding: **observed `gzip;q=1.0,deflate;q=0.6,identity;q=0.3`** on both lanes (Net::HTTP default).
- Decodes: gzip, deflate (Net::HTTP). Forced gzip (requested), **observed**: decoded and loaded.
- Fix: none. br/zstd: not in Net::HTTP.

## rust (rust-sdk `fix/all-tests-passing` be740a2)

- Client: reqwest 0.12 with only the `json` feature (`Cargo.toml:23`); fetch at
  `src/sdk.rs:309-317`. The wrapper uses the SDK's own fetch (`rust-wrapper/src/main.rs:57`).
- Accept-Encoding: **observed none**.
- Decodes: nothing. Forced gzip (unrequested), **observed**: failed context, `HTTP request failed: error decoding response body`.
- Configurable today: no (the client is built inside the SDK; cargo features are the SDK's).
- Minimal fix: `reqwest = { version = "0.12", features = ["json", "gzip"] }`. reqwest
  then adds `Accept-Encoding: gzip` and decodes it. `brotli` and `zstd` features exist
  (pure-Rust `brotli` crate; `zstd` links C libzstd through zstd-sys, built from
  source by cargo with no system package).
- br/zstd: br is light (pure Rust). zstd is a vendored C build.

## cpp (cpp-sdk `feat/initial-sdk` af5cd17)

- Client: libcurl easy handle, `src/default_http_client.cpp:21-45`. The wrapper
  passes `std::make_shared<absmartly::DefaultHTTPClient>()` (`cpp-wrapper/server.cpp:458,554`).
- Accept-Encoding: **observed none**.
- Decodes: nothing. Forced gzip (unrequested), **observed**: failed context, nlohmann JSON
  `parse error ... last read: '<U+001F>'` (the gzip magic byte).
- Configurable today: only through a custom `HTTPClient`.
- Minimal fix: `curl_easy_setopt(curl, CURLOPT_ACCEPT_ENCODING, "")` in `get`.
  libcurl then advertises and decodes everything it was built with. The lane image's
  libcurl links zlib, brotli and zstd. Also in `async_http_client.cpp` if it fetches context.
- br/zstd: whatever the system libcurl was built with. No SDK dependency added.

## elixir (elixir-sdk `feat/initial-implementation` 33dd569) — lane not built here

- Client: HTTPoison over hackney (`lib/absmartly/http/client.ex:38`, options `:90-92`).
  The SDK's mix.lock has httpoison 3.0.0/hackney 4.9.1. The wrapper's mix.lock pins
  httpoison 2.3.0/hackney 1.25.0, which is what the lane would run. The wrapper calls the
  SDK's `ABSmartly.HTTP.Client.fetch_context` (`elixir-wrapper/.../router.ex:441-480`).
- Accept-Encoding, **from code**: none. hackney 1.x has no decompression at all.
  hackney 4.x sends `Accept-Encoding: gzip, deflate` and decodes only with
  `auto_decompress: true` (`hackney.erl:1775-1781`, `hackney_conn.erl:2769-2800`), and the SDK does not set it.
- Decodes, from code: nothing. A gzip body fails `Jason.decode` ("Invalid JSON response").
- Configurable today: no.
- Minimal fix: send `{"Accept-Encoding", "gzip"}` in `build_headers` and
  `:zlib.gunzip/1` the body when the response has `Content-Encoding: gzip`. This works on
  every hackney version. On hackney >= 4, `hackney: [auto_decompress: true]` alone also works.
- br/zstd: not in OTP's long-standing stdlib (recent OTP releases add a `zstd` module; whether the image's OTP has it was not checked). gzip via `:zlib`, built in.

## swift (swift-sdk `feat/cross-sdk-tests-and-fixes` c728ed9) — lane not built here

- Client: `URLSession` with `URLSessionConfiguration.ephemeral`
  (`Sources/ABSmartly/DefaultHTTPClient.swift:29-33`).
- Accept-Encoding, **from code**: on Apple platforms `URLSession` sends
  `gzip, deflate, br` and decodes them. On Linux, swift-corelibs-foundation 5.10 sets
  libcurl `CURLOPT_ACCEPT_ENCODING ""` (`EasyHandle.swift:254-259`, enabled at
  `HTTPURLProtocol.swift:439`), so it advertises and decodes every coding the system
  libcurl supports.
- Fix: none expected. The image build fails here at `swift test --filter EventCollectorTests` (`fatalError`),
  and the swift lane already fails on main CI (cross-sdk run 43008024985 on
  `chore/drop-js-helper-scenarios`), so no lane can provide evidence. Policy: a
  reviewed exception saying exactly that, until the lane passes.

## dart and flutter (flutter-sdk `fix/audit-operator-fixes` 7aec273) — lanes not built here

- Client: `package:http` `IOClient(HttpClient())` (`packages/dart_sdk/lib/src/default_http_client.dart:24-31`).
  Both wrappers use the SDK's `DefaultHTTPClient` for endpoint contexts
  (`dart-wrapper/server.dart:475-478`; flutter uses `Client.create` with no client, `flutter-wrapper/server.dart:458-463`).
- Accept-Encoding, **from code**: `gzip`. dart:io adds it on every request
  (`sdk/lib/_http/http_impl.dart:2367`), and `autoUncompress` defaults to true (`:2862`).
- Decodes, from code: gzip only (`http_impl.dart:615-621`).
- Fix: none for gzip. br/zstd: not in dart:io. Policy: `required` (dart and flutter) from this reading; CI proves it.

## javascript (javascript-sdk `main` 96aa665) — the reference; react, vue2, vue3, angular wrap it

- Client: global `fetch` (Node's undici in the node:24 image), else `node-fetch`
  (`src/fetch.ts:22-27`). Fetch at `src/client.ts:98-107`/`:194`.
- Accept-Encoding: **observed `gzip, deflate`** on javascript, vue2, vue3 and angular
  (User-Agent `node`). react: lane not built here (yarn registry unreachable); same SDK and runtime,
  so react is `required` too and its CI lane proves it.
- Decodes: gzip, br and zstd. **Observed** in the lane image (Node v24.22.3,
  undici 7.30.2) with a local server forcing each coding: global `fetch`
  advertises only `gzip, deflate` but decoded all three. Forced gzip (requested),
  **observed** in the probe: decoded and loaded. Advertising br/zstd needs an
  explicit `Accept-Encoding` header, since undici's default does not list them.
- Browsers: the browser sets `Accept-Encoding` itself (zstd/br/gzip in current browsers).
- Fix: none.

## Summary of the gate (`contextFetchCompression`)

Required, observed or from code; CI proves it. Observed on a lane here:
javascript, python, ruby, liquid, vue2, vue3, angular. From code: scala, dart,
flutter, react. Every other SDK is a reviewed exception with its reason in
`orchestrator/expected-capabilities.json`: java, kotlin, php, go, rust, cpp
(observed, fix pending), dotnet and elixir (code sends no Accept-Encoding, fix
pending), and swift (its lane already fails on main CI, so it cannot provide
evidence). The capability is measured by the orchestrator, not reported by the
wrappers. An exception that starts measuring true fails the run until the policy
moves it to `required`; a required SDK that measures false fails with the
observed `Accept-Encoding` header(s) and the probe check that failed (no gzip
advertised, refresh not fetched, not decoded, or refresh data not used). A
probe that cannot measure (context server unreachable, no GET /context
recorded, a create or refresh that timed out, or the context server serving an
encoding the negotiation would not choose) fails the run and skips only the
scenarios that require the capability.
