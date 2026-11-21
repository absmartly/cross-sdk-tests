# Context fetch compression: what each SDK asks for and decodes

Survey of the SDK's own HTTP fetch of `GET {endpoint}/context`, the request the
SDKs make on context creation and refresh. Written 2026-11-12 against
`absmartly/cross-sdk-tests` 30d23e9 and re-measured 2026-11-17 on main ada8952
with the SDK pins in `sdk-branches.sh` there (every SDK on `main` except react,
vue2 and vue3; the commits are listed per SDK). Nothing changed on the wire with
the new pins except swift, whose lane now builds and passes: it is observed and
`required`.

Why it matters: Cloudflare in front of the Collector serves `/v1/context` as
zstd > br > gzip, picked from the request's `Accept-Encoding`. On a large tenant
(441 experiments) the body is 234 KB as zstd, 321 KB br, 420 KB gzip and
1,554,927 B when the request sends no `Accept-Encoding`.

How it was measured: the negotiating context server (`context-server/`) records
the `Accept-Encoding` and `User-Agent` of every `GET /context` per payload and
serves gzip when the request advertised it, identity otherwise. That holds for
the gzip payloads (270-272 and the `contextFetchCompression` probe), which never
serve br. The br payloads (273-274 and the `contextFetchBrotli` probe; see the
Brotli section) allow br > gzip > identity. zstd is never served, only
reported. The orchestrator probes
each SDK once at startup (create, then a refresh after the server's data
changed, both negotiated) and scenarios 270 and 272 check every fetch. A second,
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
be built here (no network access to api.nuget.org, hex.pm, repo.scala-sbt.org,
pub.dev or registry.yarnpkg.com: dotnet, elixir, scala, dart, flutter, react).
CI runs the full matrix.

The wrapper must use the SDK's default client for the observation to say
anything about the SDK default. Where a wrapper injects its own transport, this is
stated.

## java (java-sdk `main` 07807b5)

- Client: Apache httpclient5 **async** (`HttpAsyncClientBuilder`),
  `core-api/src/main/java/com/absmartly/sdk/DefaultHTTPClient.java:51`. The SDK
  declares 5.1.3 (`gradle/sdk-module.gradle:5`). The lane runs **5.2.1**: the wrapper
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
  5.6.5 also fixes an async Brotli decompression hang. Caveat, Java floor:
  httpclient5 5.1.x needs **Java 7** (its parent pom sets
  `maven.compiler.source`/`target` 1.7, the 5.1.3 jar's classes are class-file
  major version 51, and the 5.1 release notes say "5.1 is going to be the last
  release series compatible with Java 1.7"); 5.2 and later, 5.6 included, need
  **Java 8** (1.8, major 52). java-sdk ships two artifacts from the same sources
  and both depend on httpclient5 5.1.3 (`gradle/sdk-module.gradle:5`):
  `core-api` (Java 8+, `targetCompatibility = "1.8"` in `core-api/build.gradle:7-8`)
  and `core-api-java6` (described as Java 6/7, sources at 1.6 in `build.gradle:41-42`).
  So the 5.6 bump fits `core-api` with no floor change. For `core-api-java6` it
  raises the floor from Java 7 (5.1.x's) to Java 8, which defeats that artifact.
  There, stay on 5.1.x (Java 7+, not Java 6) and set `Accept-Encoding: gzip` in
  `Client.getContextData` and gunzip in `Client` when `Content-Encoding: gzip`,
  with `java.util.zip.GZIPInputStream`. That needs a `getContentEncoding` on the
  SDK's `HTTPClient.Response`.
- br/zstd: not without new native dependencies (brotli4j, zstd-jni). gzip only.

## kotlin (kotlin-sdk `main` bb7181f)

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

## scala (scala-sdk `main` 4d38c20) — lane not built here

- Client: sttp client3 3.9.2 with `HttpURLConnectionBackend`
  (`src/main/scala/com/absmartly/sdk/SDK.scala:34`, fetch at `:121-126` via `basicRequest`).
  The wrapper builds an `SDK` with the SDK's own config (`scala-wrapper/.../Server.scala:55-57`).
- Accept-Encoding, **from code**: `gzip, deflate`. sttp's `basicRequest` is
  `emptyRequest.acceptEncoding("gzip, deflate")` (`sttp/client3/SttpApi.scala:44`).
- Decodes, from code: gzip and deflate (`HttpURLConnectionBackend.scala:283-291`);
  any other coding throws `UnsupportedEncodingException`.
- Fix: none needed for gzip. Policy: `required` from this reading; the CI lane proves it.
- br/zstd: not without extra codecs. gzip is enough.

## dotnet (dotnet-sdk `main` fd564d2) — lane not built here

- Client: `HttpClient` from `IHttpClientFactory`. With DI
  (`DependencyInjection/ServiceCollectionExtensions.cs:46-73`) the primary handler is
  `SocketsHttpHandler` (net8/9) or `HttpClientHandler` (netstandard2.0), and neither
  sets `AutomaticDecompression`. The fetch is `ABSmartlyService.cs:126`.
- **The wrapper injects its own transport**: `DummyHttpClientFactory` and
  `DummyHttpClient` wrap a bare `new HttpClient()` (`dotnet-wrapper/Program.cs:1390-1405`).
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

## php (php-sdk `main` 80a5ec6)

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

## python (python3-sdk `main` 80daada)

- Client: `requests.Session` (`sdk/default_http_client.py:12`; `requests >= 2.28.1`;
  the lane ran requests 2.34.2). The wrapper uses the SDK's `DefaultHTTPClient`
  (`python-wrapper/server.py:246-248`).
- Accept-Encoding: **observed `gzip, deflate`** (create and refresh), served gzip.
- Decodes: gzip, deflate (urllib3). Forced gzip (requested), **observed**: decoded and loaded.
- Fix: none for gzip. The lane image has urllib3 2.8.0, whose `ACCEPT_ENCODING` is
  `gzip,deflate`. urllib3 adds `br` when `brotli`/`brotlicffi` is importable and
  `zstd` when its zstd support is available (optional packages, not installed here).

## go (go-sdk `main` 54b4e8a)

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

## ruby (ruby-sdk `main` c3a0e08) and liquid (liquid-sdk `main` e38f9c6, wraps ruby-sdk)

- Client: Faraday with `net_http_persistent` (`lib/default_http_client.rb:24-31`); the ruby lane runs Faraday 2.8.1, the liquid lane 2.15.1 (User-Agents `Faraday v2.8.1` and `Faraday v2.15.1`).
  Both wrappers use the SDK's `Client.create` (`ruby-wrapper/server.rb:164-170`,
  `liquid-wrapper/server.rb:294-300`).
- Accept-Encoding: **observed `gzip;q=1.0,deflate;q=0.6,identity;q=0.3`** on both lanes (Net::HTTP default).
- Decodes: gzip, deflate (Net::HTTP). Forced gzip (requested), **observed**: decoded and loaded.
- Fix: none. br/zstd: not in Net::HTTP.

## rust (rust-sdk `main` 7c26910)

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

## cpp (cpp-sdk `main` 9ea8669)

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

## elixir (elixir-sdk `main` ea4fecb) — lane not built here

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

## swift (swift-sdk `main` b67a2a9)

- Client: `URLSession` with `URLSessionConfiguration.ephemeral`
  (`Sources/ABSmartly/DefaultHTTPClient.swift:29-33`).
- Accept-Encoding: **observed `deflate, gzip, br, zstd`** (create and refresh), served gzip.
  User-Agent `Run (unknown version) curl/7.81.0`: on Linux swift-corelibs-foundation sets
  libcurl `CURLOPT_ACCEPT_ENCODING ""` (`EasyHandle.swift`), so it advertises and decodes
  every coding the system libcurl supports. On Apple platforms `URLSession` sends
  `gzip, deflate, br` and decodes them.
- Decodes: forced gzip (requested), **observed**: decoded and loaded.
- Fix: none. Policy: `required`. (On the earlier pins the swift image failed its own
  build-time test and the lane failed on main CI, so it was a reviewed exception; with
  main's pins the lane builds and passes here.)

## dart and flutter (flutter-sdk `main` 86b7911) — lanes not built here

- Client: `package:http` `IOClient(HttpClient())` (`packages/dart_sdk/lib/src/default_http_client.dart:24-31`).
  Both wrappers use the SDK's `DefaultHTTPClient` for endpoint contexts
  (`dart-wrapper/server.dart:475-478`; flutter uses `Client.create` with no client, `flutter-wrapper/server.dart:458-463`).
- Accept-Encoding, **from code**: `gzip`. dart:io adds it on every request
  (`sdk/lib/_http/http_impl.dart:2367`), and `autoUncompress` defaults to true (`:2862`).
- Decodes, from code: gzip only (`http_impl.dart:615-621`).
- Fix: none for gzip. br/zstd: not in dart:io. Policy: `required` (dart and flutter) from this reading; CI proves it.

## javascript (javascript-sdk `main` 311cfca) — the reference; react, vue2, vue3, angular wrap it

- Client: global `fetch` (Node's undici in the node:24 image), else `node-fetch`
  (`src/fetch.ts:22-27`). Fetch at `src/client.ts:98-107`/`:194`.
- Accept-Encoding: **observed `gzip, deflate`** over the context server's http on javascript, vue2, vue3 and angular
  (User-Agent `node`). react: lane not built here (yarn registry unreachable); same SDK and runtime,
  so react is `required` too and its CI lane proves it.
- Decodes: gzip, br and zstd. **Observed** in the lane image (Node v24.22.3,
  undici 7.30.2) with a local server forcing each coding: global `fetch`
  decoded all three. Forced gzip (requested), **observed** in the probe: decoded
  and loaded. What undici advertises depends on the URL scheme
  (`lib/web/fetch/index.js:1523-1528` in 7.30.2): **observed** in the lane image,
  `gzip, deflate` over http (the harness's context server) and
  `br, gzip, deflate` over https (no zstd in 7.30.2).
- Browsers: the browser sets `Accept-Encoding` itself (zstd/br/gzip in current browsers).
- Fix: none.

## Summary of the gzip gate (`contextFetchCompression`)

Required, observed or from code; CI proves it. Observed on a lane here:
javascript, python, ruby, liquid, swift, vue2, vue3, angular. From code: scala,
dart, flutter, react. Every other SDK is a reviewed exception with its reason in
`orchestrator/expected-capabilities.json`: java, kotlin, php, go, rust, cpp
(observed, fix pending), dotnet and elixir (code sends no Accept-Encoding, fix
pending). The capability is measured by the orchestrator, not reported by the
wrappers. An exception that starts measuring true fails the run until the policy
moves it to `required`; a required SDK that measures false fails with the
observed `Accept-Encoding` header(s) and the probe check that failed (no gzip
advertised, refresh not fetched, not decoded, or refresh data not used). A
probe that cannot measure (context server unreachable, no GET /context
recorded, a create or refresh that timed out, or the context server serving an
encoding the negotiation would not choose) fails the run and skips only the
scenarios that require the capability. Inside scenarios 270-272, a failing context-server control call or a served
encoding the negotiation would not choose is a harness error: the run fails,
but the scenario is not counted as an SDK failure (an SDK failure already
found earlier in that scenario is still counted, and shown next to it).

## Brotli (`contextFetchBrotli`, scenarios 273-274)

Measured 2026-11-17 on this branch (stacked on #20, ae86279) with the same SDK
pins as above. Cloudflare's order is zstd > br > gzip; on the 441-experiment
tenant br is 321 KB against gzip's 420 KB (-24%), and zstd 234 KB (-44%).

**How it is measured.** The context server encodes br with the pinned
`brotli==1.2.0` wheel (`context-server/requirements.txt`). The br payloads allow
`br`, `gzip`, `identity` in that order, honouring q-values (`br;q=0` refuses br;
`*` does not revive it). A second startup probe, like the gzip one but on a br
payload, sets `contextFetchBrotli` true only when create and refresh both
advertise br, are served br, and their bodies are used. Probe and scenarios run
over the context server's plain **http**, recorded as `transport` in the
evidence and `report.json`, so a client that picks its `Accept-Encoding` by
scheme is measured on http only (see javascript below). Failed measurements and
wrong served codings are handled as for gzip (above); a MEASURE FAIL gates only
274. 273 is ungated: whatever an SDK negotiates, it must match the twin 271 and
use the refreshed data. 274 requires `contextFetchBrotli`.

**Observed on the 14 lanes built here** (`report.json`, `context_fetch_brotli_probe`):

| lane | Accept-Encoding on the br probe (create, refresh) | served | `contextFetchBrotli` | 273 | 274 |
|---|---|---|---|---|---|
| swift | `deflate, gzip, br, zstd` (both) | br, br | **true** | pass (br) | **pass (br)** |
| javascript, angular, vue2, vue3 | `gzip, deflate` (http; `br, gzip, deflate` over https, see below) | gzip | false (http artefact) | pass (gzip) | skip (exception) |
| python | `gzip, deflate` | gzip | false | pass (gzip) | skip |
| ruby, liquid | `gzip;q=1.0,deflate;q=0.6,identity;q=0.3` | gzip | false | pass (gzip) | skip |
| java, kotlin, go, rust, cpp, php | none | identity | false | pass (identity) | skip |

Not built here (registries unreachable: nuget.org, hex.pm, repo.scala-sbt.org,
pub.dev, registry.yarnpkg.com): dotnet, elixir, scala, dart, flutter, react.
Their rows below are **from code**; CI proves them.

Policy: `required` for swift (observed); every other SDK is a reviewed
exception in `orchestrator/expected-capabilities.json` naming what is missing.
An exception that starts measuring true fails the run until it is moved.

### Per SDK: native br today, the minimal change, and its cost

"Native today" is the SDK's own fetch at the pinned commit, unmodified.

- **swift** (swift-sdk b67a2a9). Native: **observed** advertises
  `deflate, gzip, br, zstd` and decodes br (274 passes). `URLSession`
  (`Sources/ABSmartly/DefaultHTTPClient.swift:29-33`); on Linux
  swift-corelibs-foundation sets libcurl `CURLOPT_ACCEPT_ENCODING ""`, and the
  lane's libcurl links libbrotlidec and libzstd (**observed**, `ldd`). Apple
  platforms: `URLSession` sends `gzip, deflate, br` and decodes them (platform
  handles it). Change: none.
- **cpp** (cpp-sdk 9ea8669). Native: **observed** no Accept-Encoding, nothing
  decoded. libcurl easy handle with no encoding option
  (`src/default_http_client.cpp:21-34`). The lane image's libcurl links
  libbrotlidec, libzstd and zlib (**observed**, `ldd`). Change (flag):
  `curl_easy_setopt(curl, CURLOPT_ACCEPT_ENCODING, "")` in `get`; curl then
  advertises and decodes every coding it was built with (gzip, and br/zstd when
  present). Cost: none in the SDK; br depends on the system libcurl build (true
  on Debian/Ubuntu, Homebrew, vcpkg with the `brotli` feature). Safe: yes, and the
  same line fixes gzip.
- **php** (php-sdk 80a5ec6). Native: **observed** none with the wrapper's
  injected `ReactHttpClient` (`src/Http/ReactHttpClient.php:38`); the default
  curl client sets no `CURLOPT_ENCODING` (`src/Http/HTTPClient.php:212-219`).
  Change (flag), curl client: `CURLOPT_ENCODING => ''`. **Observed** in the
  php lane image (libcurl 8.14.1, `CURL_VERSION_BROTLI` set): that option sends
  `deflate, gzip, br, zstd`, is served br by the context server and decodes it.
  Cost: none (ext-curl is already required). ReactHttpClient: ReactPHP's
  `Browser` does not decode; br there needs the PECL `brotli` extension (not in
  the image) plus a manual header and decode: not worth it, gzip via
  `gzdecode` is the sensible fix there.
- **dotnet** (dotnet-sdk fd564d2), from code. Native: none; neither primary
  handler sets `AutomaticDecompression`
  (`src/ABSmartly.Sdk/DependencyInjection/ServiceCollectionExtensions.cs:50-60`
  `SocketsHttpHandler`, `:68-73` `HttpClientHandler`). Change (flag):
  `AutomaticDecompression = DecompressionMethods.All` (gzip, deflate, br) on the
  net8/net9 `SocketsHttpHandler`; `DecompressionMethods.Brotli` is built into
  .NET Core 3.0+ (System.IO.Compression), no package. The netstandard2.0 target
  (`ABSmartly.Sdk.csproj:19`) has no Brotli member: gzip/deflate only there.
  Cost: none. Safe: yes. Note the wrapper injects a bare `HttpClient`
  (`dotnet-wrapper/Program.cs:1390-1405`), so the lane would also need the
  wrapper to use the SDK's handler to show it.
- **rust** (rust-sdk 7c26910). Native: **observed** none; reqwest 0.12 with
  only `json` (`Cargo.toml:26`). Change (flag): `features = ["json", "gzip",
  "brotli"]`. reqwest then sends `Accept-Encoding: gzip, br` and decodes both
  (reqwest 0.12 `Cargo.toml`: `brotli = ["dep:async-compression",
  "async-compression?/brotli", ...]`, backed by the pure-Rust `brotli` crate,
  BSD-3-Clause/MIT). Cost: pure Rust, no system library, a few hundred KB of
  code in the binary (the static dictionary is ~120 KB). Safe: yes. (`zstd`
  feature links C libzstd via zstd-sys, built from source.)
- **javascript** (javascript-sdk 311cfca) and **react, vue2, vue3, angular**
  (wrap it; react from code). Client on Node: global `fetch` (undici); the
  `node-fetch` fallback runs only when `globalThis.fetch` is missing
  (`src/fetch.ts:19-33`, `lib/fetch.js:61-69` in the lane image's
  `node_modules/@absmartly/javascript-sdk`). **Observed** in the javascript lane
  image: the SDK's exported fetch is the bound global `fetch`, User-Agent `node`.
  The SDK sets no Accept-Encoding (`src/client.ts:184-191`), so undici's default
  applies, and it is scheme-dependent (`lib/web/fetch/index.js:1523-1528`).
  **Observed** in the lane image (Node v24.22.3, undici 7.30.2), with the SDK's
  own fetch against local servers: over http (the context server) it sends
  `gzip, deflate`; over https it sends `br, gzip, deflate` and decodes a br body.
  So `contextFetchBrotli` = false for this family is an artefact of the
  harness's http transport. The `node-fetch` 2.6.8 fallback sends a
  fixed `gzip,deflate` (`lib/index.js:1360`) whatever the scheme. Change: none
  needed on Node >= 24.22.3 over https (observed); undici's source has the same
  https-only br branch from 5.0.0, Node 18's line, on (read in the 5.0.0,
  5.10.0, 5.20.0, 5.28.4, 6.0.0 and 6.21.0 tarballs, not run). Verify that the
  SDK keeps using global fetch rather than `node-fetch`.
  Browsers set `Accept-Encoding` themselves (current ones send
  `gzip, deflate, br, zstd`): browser handles it.
- **python** (python3-sdk 80daada). Native: **observed** `gzip, deflate`
  (requests 2.34.2, urllib3 2.8.0 in the lane). urllib3 appends `br` only when
  `brotli` or `brotlicffi` is importable (`urllib3/util/request.py:22-31` in
  2.8.0); the SDK depends only on `requests >= 2.28.1` (`pyproject.toml:21`).
  Change (dependency): `brotli` (or `brotlicffi`). **Observed** in the python
  lane image: after `pip install brotli==1.2.0`, `requests` sends
  `gzip, deflate, br`, is served br and decodes it, with no SDK code change.
  Cost: MIT; binary wheels (checked here: cp311 manylinux x86_64 1.43 MB,
  cp311/cp314 manylinux aarch64 ~1.5 MB, cp311 macOS x86_64 0.45 MB); where no
  wheel exists it builds a C extension from a 7.4 MB sdist (needs a compiler).
  `brotlicffi` (MIT) is the PyPy-friendly alternative. Safe: as an optional
  extra (`absmartly[brotli]`) yes; as a hard dependency acceptable but it adds a
  native wheel to every install.
- **go** (go-sdk 54b4e8a). Native: **observed** none; `DisableCompression: true`
  (`sdk/DefaultHttpClient.go:50`). br is not in the Go standard library, and
  Go's transport only adds and decodes gzip by itself. Change (dependency):
  `github.com/andybalholm/brotli` (v1.3.0, pure Go, MIT, no cgo), plus a manual
  `Accept-Encoding: br, gzip` and a decode on `Content-Encoding: br` (setting
  the header by hand turns off Go's transparent gzip, so gzip must be decoded
  too; resty already gunzips). Cost: pure Go (no cgo), so every Go platform;
  small binary growth. Safe: yes, but it is code in the SDK, not a flag.
  Dropping `DisableCompression` (the gzip fix) is the first step.
- **ruby** (ruby-sdk c3a0e08) and **liquid** (liquid-sdk e38f9c6). Native:
  **observed** `gzip;q=1.0,deflate;q=0.6,identity;q=0.3` (Net::HTTP); no br
  decoder. Faraday stack: `lib/default_http_client.rb:24-31`. Change
  (dependency): the `faraday-gzip` middleware (3.0.4, MIT) plus the `brotli`
  gem; the middleware advertises `gzip,deflate,br` and decodes br only when the
  `brotli` gem loads (`lib/faraday/gzip/middleware.rb:24-31`, `:114`). Cost:
  `brotli` is a C extension compiled at install: needs a compiler and slows
  installs, and C extensions do not load on JRuby. Safe as an
  optional gem the SDK uses when present; not as a hard dependency.
- **java** (java-sdk 07807b5). Native: **observed** none (httpclient5 async,
  `core-api/src/main/java/com/absmartly/sdk/DefaultHTTPClient.java:53`; 5.1.3,
  `gradle/sdk-module.gradle:10`). Change (dependency + bump): httpclient5 5.6,
  whose async transport decompresses transparently, plus the optional codec its
  pom lists: `com.aayushatharva.brotli4j:brotli4j` (optional in the
  httpclient5 5.6 pom; `zstd-jni` likewise). Cost: brotli4j is Apache-2.0 but
  native (per-platform `native-*` jars; 1.18.0's linux-x86_64 one is 427 KB), so
  platform coverage is whatever brotli4j ships; 5.6 needs Java 8, so not
  `core-api-java6`. Alternative: `org.brotli:dec` 0.1.2 (Google, MIT, pure
  Java, 98 KB, last released 2017) with a manual header and
  `BrotliInputStream`. Safe: optional dependency on `core-api` only.
- **kotlin** (kotlin-sdk bb7181f). Native: **observed** none
  (`HttpURLConnection`, `src/main/kotlin/com/absmartly/sdk/DefaultHTTPClient.kt:64`);
  no br in the JDK. Change (dependency): `org.brotli:dec` (pure Java, MIT, 98 KB)
  with `Accept-Encoding: br, gzip` and a `BrotliInputStream`/`GZIPInputStream`
  wrap by `contentEncoding`. Cost: small, pure JVM, but an unmaintained artifact
  (2017). Safe: yes, though gzip (JDK only) gets most of the win.
- **scala** (scala-sdk 4d38c20), from code. Native: sttp `basicRequest` sends
  `gzip, deflate` and `HttpURLConnectionBackend` throws
  `UnsupportedEncodingException` on any other coding (sttp 3.9.2,
  `HttpURLConnectionBackend.scala:283-291`). Change: a custom backend or
  response handler with `org.brotli:dec`, or a different sttp backend
  (`HttpClientFutureBackend` has no br either). Not worth it.
- **dart, flutter** (flutter-sdk 86b7911), from code. Native: dart:io sends
  `gzip` and decodes gzip only (`sdk/lib/_http/http_impl.dart:2367`, `:615-621`).
  Change: a manual header plus a pure-Dart Brotli decoder package (not
  verified here, pub.dev unreachable) and turning off `autoUncompress`. Flutter
  web: the browser handles it. Not worth it.
- **elixir** (elixir-sdk ea4fecb), from code. Native: none
  (HTTPoison/hackney, `lib/absmartly/http/client.ex:48`, options `:101`); OTP
  has no Brotli. Change: a NIF dependency (Rust or C) plus manual header and
  decode; not verified here (hex.pm unreachable). Native build per platform:
  not worth it.

### Recommendation (ranked)

1. **Already done**: swift (observed, `required`).
2. **(Almost) free, a flag or one header, no new dependency**: cpp
   (`CURLOPT_ACCEPT_ENCODING ""`, also fixes gzip), php curl default
   (`CURLOPT_ENCODING ''`, observed to work in the lane image), dotnet
   (`DecompressionMethods.All` on net8/9), rust (reqwest `brotli` feature, pure
   Rust). The javascript family needs none on Node >= 24.22.3 over https
   (observed, see above); only its http measurement here is false.
3. **Needs a dependency**: python (`brotli` wheel as an extra; zero code),
   go (`andybalholm/brotli`, pure Go, plus header/decode code), ruby/liquid
   (`faraday-gzip` + C-extension `brotli` gem), java `core-api` (httpclient5 5.6
   + brotli4j, native), kotlin (`org.brotli:dec`, pure but unmaintained).
4. **Not worth it**: scala, dart/flutter (non-web), elixir, java
   `core-api-java6`, php ReactHttpClient. gzip already captures most of the
   saving (1.55 MB -> 420 KB); br adds a further ~100 KB on the largest tenant.

**zstd readiness.** zstd is Cloudflare's first preference (234 KB on the large
tenant). Python 3.14 ships `compression.zstd`, and urllib3 2.x appends `zstd`
to its default `Accept-Encoding` on 3.14 with no extra package
(`urllib3/util/request.py:33-41`; observed in a 3.14 venv). Swift on Linux
already advertises zstd (observed), as does any libcurl client with
`CURLOPT_ACCEPT_ENCODING ""` on images whose libcurl links libzstd (cpp, php,
observed via `ldd`); Node's undici 7.30.2 decodes zstd (observed, see
javascript above) but advertises it on neither scheme; newer undici
adds zstd over https (`br, gzip, deflate, zstd` observed with Node 26 / undici
8.11.2 in review). .NET, Go, the JDK, dart:io and OTP have no zstd decoder; reqwest's `zstd` feature and Java's zstd-jni are native builds.
The context server does not serve zstd: covering it needs a zstd encoder on the
server (stdlib on Python 3.14, or the `zstandard` wheel on 3.11) and a third
preference list, a separate follow-up.

**Follow-up: https transport.** Every `contextFetchBrotli` (and
`contextFetchCompression`) measurement here is an http measurement, so
scheme-dependent clients (Node's undici today) measure false although they
advertise br over https. Serving the context server over https too (a
self-signed certificate each lane's SDK trusts) would measure the production
transport; not done in this change.
