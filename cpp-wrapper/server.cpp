#include <httplib.h>
#include <nlohmann/json.hpp>

#include <absmartly/context.h>
#include <absmartly/context_config.h>
#include <absmartly/context_event_logger.h>
#include <absmartly/models.h>
#include <absmartly/errors.h>
#include <absmartly/hashing.h>
#include <absmartly/sdk.h>
#include <absmartly/client_config.h>
#include <absmartly/client.h>
#include <absmartly/default_http_client.h>
#include <absmartly/context_publisher.h>
#include <absmartly/default_context_publisher.h>
#include <absmartly/default_context_data_provider.h>

#include <atomic>
#include <cctype>
#include <chrono>
#include <future>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <vector>
#include <iostream>

using json = nlohmann::json;

static int64_t now_millis() {
    auto now = std::chrono::system_clock::now();
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               now.time_since_epoch())
        .count();
}

struct Event {
    std::string type;
    json data;
    int64_t timestamp;
};

json event_to_json(const Event& e) {
    return json{{"type", e.type}, {"data", e.data}, {"timestamp", e.timestamp}};
}

json events_to_json(const std::vector<Event>& events) {
    json arr = json::array();
    for (const auto& e : events) {
        arr.push_back(event_to_json(e));
    }
    return arr;
}

class EventCollector : public absmartly::ContextEventLogger {
public:
    std::vector<Event> events;
    std::mutex mu;

    void handle_event(absmartly::Context& ctx, const std::string& event_type,
                      const json& data) override {
        std::lock_guard<std::mutex> lock(mu);
        events.push_back({event_type, data.is_null() ? json(nullptr) : json(data), now_millis()});
    }

    std::vector<Event> get_new_events(size_t since) {
        std::lock_guard<std::mutex> lock(mu);
        if (since >= events.size()) {
            return {};
        }
        return std::vector<Event>(events.begin() + static_cast<long>(since),
                                  events.end());
    }

    size_t size() {
        std::lock_guard<std::mutex> lock(mu);
        return events.size();
    }

    std::vector<Event> all_events() {
        std::lock_guard<std::mutex> lock(mu);
        return events;
    }
};

// ContextPublisher handed to the SDK Context. It is the transport boundary
// only: the SDK's own Context::publish() builds the event, manages pending
// state and invokes this publisher. `arm_failure()` makes the NEXT transport
// call fail (one-shot) with "Publish failed", so the publishFail capability
// exercises the SDK's real failure path instead of the wrapper short-circuiting
// publish. Whether pending events survive is decided entirely by the SDK.
// In e2e mode the delegate is the SDK's DefaultContextPublisher (real HTTP);
// in local mode there is no collector, so a successful transport is a no-op.
class FailablePublisher : public absmartly::ContextPublisher {
public:
    explicit FailablePublisher(std::shared_ptr<absmartly::ContextPublisher> delegate = nullptr)
        : delegate_(std::move(delegate)) {}

    void arm_failure() { fail_next_.store(true); }

    std::future<void> publish(const absmartly::PublishEvent& event) override {
        if (fail_next_.exchange(false)) {
            std::promise<void> p;
            p.set_exception(std::make_exception_ptr(std::runtime_error("Publish failed")));
            return p.get_future();
        }
        if (delegate_) {
            return delegate_->publish(event);
        }
        std::promise<void> p;
        p.set_value();
        return p.get_future();
    }

private:
    std::shared_ptr<absmartly::ContextPublisher> delegate_;
    std::atomic<bool> fail_next_{false};
};

struct ContextEntry {
    std::unique_ptr<absmartly::Context> context;
    std::shared_ptr<EventCollector> collector;
    std::shared_ptr<FailablePublisher> publisher;
    std::map<std::string, json> unit_original_values;
    // Data comes from an endpoint the SDK fetches, so /refresh re-fetches
    // through the SDK's provider.
    bool fetches_from_endpoint = false;
    // Serializes calls on this one context. Held for the whole request so a
    // publish/finalize that waits on HTTP blocks only its own context.
    std::mutex mu;
};

// contexts_mu guards only the map; each entry has its own lock.
static std::mutex contexts_mu;
static std::map<std::string, std::shared_ptr<ContextEntry>> contexts;
static std::atomic<int> context_counter{0};

static std::mutex payloads_mu;
static std::map<std::string, absmartly::ContextData> payload_store;

static int server_port = 3000;

static std::string make_context_id() {
    int n = context_counter.fetch_add(1);
    return "ctx-" + std::to_string(n);
}

// A context entry plus its held per-context lock. The shared_ptr keeps the
// entry alive if DELETE removes it from the map mid-request.
class LockedEntry {
public:
    explicit LockedEntry(std::shared_ptr<ContextEntry> entry) : entry_(std::move(entry)) {
        if (entry_) {
            lock_ = std::unique_lock<std::mutex>(entry_->mu);
        }
    }
    ContextEntry* operator->() const { return entry_.get(); }
    explicit operator bool() const { return entry_ != nullptr; }

private:
    std::shared_ptr<ContextEntry> entry_;
    std::unique_lock<std::mutex> lock_;
};

static LockedEntry lock_context(const std::string& id) {
    std::shared_ptr<ContextEntry> entry;
    {
        std::lock_guard<std::mutex> lock(contexts_mu);
        auto it = contexts.find(id);
        if (it != contexts.end()) {
            entry = it->second;
        }
    }
    return LockedEntry(std::move(entry));
}

static void store_context(const std::string& id, std::shared_ptr<ContextEntry> entry) {
    std::lock_guard<std::mutex> lock(contexts_mu);
    contexts[id] = std::move(entry);
}

// The orchestrator may address this wrapper as localhost:<published port>,
// which inside the container is not this server; point it at our own port.
static std::string translate_endpoint(const std::string& endpoint) {
    for (const std::string prefix : {"localhost:", "127.0.0.1:"}) {
        auto start = endpoint.find(prefix);
        if (start == std::string::npos) {
            continue;
        }
        auto port_start = start + prefix.size();
        auto port_end = endpoint.find('/', port_start);
        if (port_end == std::string::npos) {
            port_end = endpoint.size();
        }
        if (port_end == port_start) {
            continue;
        }
        bool digits = true;
        for (auto i = port_start; i < port_end; ++i) {
            if (!std::isdigit(static_cast<unsigned char>(endpoint[i]))) {
                digits = false;
                break;
            }
        }
        if (digits) {
            return endpoint.substr(0, start) + "127.0.0.1:" + std::to_string(server_port) +
                   endpoint.substr(port_end);
        }
    }
    return endpoint;
}

// Delays the SDK's own data provider by `throttle_ms`, then lets it fetch.
class DeferredContextDataProvider : public absmartly::ContextDataProvider {
public:
    DeferredContextDataProvider(std::shared_ptr<absmartly::ContextDataProvider> inner, int throttle_ms)
        : inner_(std::move(inner)), throttle_ms_(throttle_ms) {}

    std::future<absmartly::ContextData> get_context_data() override {
        return std::async(std::launch::async, [inner = inner_, ms = throttle_ms_]() {
            std::this_thread::sleep_for(std::chrono::milliseconds(ms));
            return inner->get_context_data().get();
        });
    }

private:
    std::shared_ptr<absmartly::ContextDataProvider> inner_;
    int throttle_ms_;
};

static json make_response(const json& result, const std::vector<Event>& events) {
    return json{{"result", result}, {"events", events_to_json(events)}};
}

static json make_error_response(const std::string& error,
                                [[maybe_unused]] const std::string& code) {
    // Match the other 20 wrappers: error bodies carry only `error`. The `code`
    // argument is retained at call sites for readability but is not emitted (the
    // orchestrator only ever reads the `error` field).
    return json{{"error", error}};
}

static std::string uid_to_string(const json& val) {
    if (val.is_string()) {
        return val.get<std::string>();
    }
    if (val.is_number_integer()) {
        return std::to_string(val.get<int64_t>());
    }
    if (val.is_number_float()) {
        double d = val.get<double>();
        if (d == static_cast<double>(static_cast<int64_t>(d))) {
            return std::to_string(static_cast<int64_t>(d));
        }
        return std::to_string(d);
    }
    return val.dump();
}

static absmartly::ContextData parse_context_data(const json& j) {
    absmartly::ContextData cd;
    if (j.contains("experiments") && j["experiments"].is_array()) {
        cd.experiments = j["experiments"].get<std::vector<absmartly::ExperimentData>>();
    }
    return cd;
}

static std::optional<absmartly::ContextData> find_payload(const std::string& id) {
    std::lock_guard<std::mutex> lock(payloads_mu);
    auto it = payload_store.find(id);
    if (it == payload_store.end()) {
        return std::nullopt;
    }
    return it->second;
}

int main() {
    if (const char* port_env = std::getenv("PORT")) {
        server_port = std::atoi(port_env);
    }

    httplib::Server svr;
    // Endpoint-mode contexts fetch from this same server and finalize/publish
    // may block on HTTP, so leave headroom beyond the default thread count.
    svr.new_task_queue = [] { return new httplib::ThreadPool(32); };

    svr.Get("/health", [](const httplib::Request&, httplib::Response& res) {
        json resp = {{"status", "healthy"}, {"sdk", "cpp"}, {"version", "1.0.0"}};
        res.set_content(resp.dump(), "application/json");
    });

    svr.Get("/capabilities",
            [](const httplib::Request&, httplib::Response& res) {
                json resp = {{"diagnostics", true}, {"attrsSeq", false},
                    {"publishFail", true}, {"variableKeysMap", true},
                    {"globalCustomFieldKeys", true}, {"getUnits", true},
                    {"getAttributes", true}, {"readyError", true}};
                res.set_content(resp.dump(), "application/json");
            });

    svr.Post("/diagnostic",
             [](const httplib::Request& req, httplib::Response& res) {
                 json body;
                 try {
                     body = json::parse(req.body);
                 } catch (...) {
                     res.status = 400;
                     res.set_content(make_error_response("Invalid JSON", "PARSE_ERROR").dump(), "application/json");
                     return;
                 }

                 const std::string op = body.value("operation", "");
                 const json value = body.contains("value") ? body["value"] : json();
                 const std::string text = value.is_string() ? value.get<std::string>() : value.dump();

                 json result;
                 if (op == "hashUnit") {
                     result = absmartly::hash_unit(text);
                 } else if (op == "base64UrlNoPadding") {
                     result = absmartly::base64url_no_padding(
                         reinterpret_cast<const uint8_t*>(text.data()), text.size());
                 } else if (op == "utf8Bytes") {
                     result = json::array();
                     for (unsigned char c : text) {
                         result.push_back(static_cast<int>(c));
                     }
                 } else if (op == "isObject") {
                     result = value.is_object();
                 } else if (op == "isNumeric") {
                     result = value.is_number();
                 } else if (op == "isPromise") {
                     result = false;
                 } else {
                     res.status = 400;
                     res.set_content(
                         make_error_response("Unsupported diagnostic operation: " + op, "BAD_REQUEST").dump(),
                         "application/json");
                     return;
                 }

                 json resp = {{"result", result}, {"events", json::array()}};
                 res.set_content(resp.dump(), "application/json");
             });

    svr.Put(R"(/context_payload/(.+))",
            [](const httplib::Request& req, httplib::Response& res) {
                std::string payload_id = req.matches[1];
                json body;
                try {
                    body = json::parse(req.body);
                } catch (...) {
                    res.status = 400;
                    res.set_content(
                        make_error_response("Invalid JSON", "PARSE_ERROR").dump(),
                        "application/json");
                    return;
                }

                absmartly::ContextData cd;
                if (body.contains("data")) {
                    cd = parse_context_data(body["data"]);
                }

                {
                    std::lock_guard<std::mutex> lock(payloads_mu);
                    payload_store[payload_id] = cd;
                }

                json resp = {{"success", true}};
                res.set_content(resp.dump(), "application/json");
            });

    // Read a stored payload; `?throttle=<ms>` delays the response.
    svr.Get(R"(/context_payload/([^/]+)$)",
            [](const httplib::Request& req, httplib::Response& res) {
                std::string payload_id = req.matches[1];
                if (req.has_param("throttle")) {
                    int throttle = std::atoi(req.get_param_value("throttle").c_str());
                    if (throttle > 0) {
                        std::this_thread::sleep_for(std::chrono::milliseconds(throttle));
                    }
                }
                std::optional<absmartly::ContextData> cd = find_payload(payload_id);
                if (!cd) {
                    res.status = 404;
                    res.set_content(make_error_response("payload not found", "NOT_FOUND").dump(), "application/json");
                    return;
                }
                json resp = *cd;
                res.set_content(resp.dump(), "application/json");
            });

    // Mock collector `GET {endpoint}/context`, fetched by the SDK's Client. An
    // unknown payload is a 404, so the SDK fails the context rather than it
    // loading as ready with empty data.
    svr.Get(R"(/context_payload/([^/]+)/context)",
            [](const httplib::Request& req, httplib::Response& res) {
                std::string payload_id = req.matches[1];
                std::optional<absmartly::ContextData> cd = find_payload(payload_id);
                if (!cd) {
                    res.status = 404;
                    res.set_content(make_error_response("payload not found", "NOT_FOUND").dump(), "application/json");
                    return;
                }
                json resp = *cd;
                res.set_content(resp.dump(), "application/json");
            });

    svr.Post("/context",
             [](const httplib::Request& req, httplib::Response& res) {
                 json body;
                 try {
                     body = json::parse(req.body);
                 } catch (...) {
                     res.status = 400;
                     res.set_content(
                         make_error_response("Invalid JSON", "PARSE_ERROR").dump(),
                         "application/json");
                     return;
                 }

                 if (body.contains("mode") && body["mode"].is_string() && body["mode"].get<std::string>() == "e2e") {
                     const char* e2e_endpoint = std::getenv("ABSMARTLY_E2E_ENDPOINT");
                     const char* e2e_api_key = std::getenv("ABSMARTLY_E2E_API_KEY");
                     const char* e2e_app = std::getenv("ABSMARTLY_E2E_APPLICATION");
                     const char* e2e_env = std::getenv("ABSMARTLY_E2E_ENVIRONMENT");

                     if (!e2e_endpoint || !e2e_api_key || !e2e_app || !e2e_env) {
                         res.status = 501;
                         res.set_content(json{{"error", "e2e mode not configured"}}.dump(), "application/json");
                         return;
                     }

                     absmartly::ClientConfig client_config;
                     client_config.endpoint = e2e_endpoint;
                     client_config.api_key = e2e_api_key;
                     client_config.application = e2e_app;
                     client_config.environment = e2e_env;

                     auto e2e_collector = std::make_shared<EventCollector>();

                     absmartly::SDKConfig e2e_sdk_config;
                     e2e_sdk_config.client = std::make_shared<absmartly::Client>(client_config,
                         std::make_shared<absmartly::DefaultHTTPClient>());
                     e2e_sdk_config.context_event_handler = e2e_collector;
                     // Real SDK HTTP publisher, wrapped only so publishFail can
                     // arm a one-shot transport failure.
                     auto e2e_publisher = std::make_shared<FailablePublisher>(
                         std::make_shared<absmartly::DefaultContextPublisher>(e2e_sdk_config.client));
                     e2e_sdk_config.context_event_publisher = e2e_publisher;

                     auto e2e_sdk = absmartly::SDK::create(std::move(e2e_sdk_config));

                     absmartly::ContextConfig e2e_ctx_config;
                     // Never auto-publish or auto-refresh in e2e mode; the test
                     // drives publishing explicitly.
                     e2e_ctx_config.publish_delay = -1;
                     e2e_ctx_config.refresh_period = 0;
                     std::map<std::string, json> e2e_original_units;
                     if (body.contains("units") && body["units"].is_object()) {
                         for (auto& [key, val] : body["units"].items()) {
                             e2e_ctx_config.units[key] = uid_to_string(val);
                             e2e_original_units[key] = val;
                         }
                     }

                     auto e2e_ctx = e2e_sdk->create_context(e2e_ctx_config);
                     e2e_ctx->wait_until_ready();

                     if (body.contains("attributes") && body["attributes"].is_object()) {
                         for (auto& [key, val] : body["attributes"].items()) {
                             e2e_ctx->set_attribute(key, val);
                         }
                     }

                     std::string e2e_context_id = make_context_id();
                     bool ready = e2e_ctx->is_ready();
                     bool failed = e2e_ctx->is_failed();
                     bool finalized = e2e_ctx->is_finalized();
                     auto all = e2e_collector->all_events();

                     auto entry = std::make_shared<ContextEntry>();
                     entry->context = std::move(e2e_ctx);
                     entry->collector = e2e_collector;
                     entry->publisher = e2e_publisher;
                     entry->unit_original_values = std::move(e2e_original_units);
                     entry->fetches_from_endpoint = true;
                     store_context(e2e_context_id, std::move(entry));

                     json result = {{"contextId", e2e_context_id},
                                    {"ready", ready},
                                    {"failed", failed},
                                    {"finalized", finalized}};
                     res.set_content(make_response(result, all).dump(), "application/json");
                     return;
                 }

                 absmartly::ContextConfig config;
                 std::map<std::string, json> original_units;

                 if (body.contains("units") && body["units"].is_object()) {
                     for (auto& [key, val] : body["units"].items()) {
                         config.units[key] = uid_to_string(val);
                         original_units[key] = val;
                     }
                 }

                 if (body.contains("options") && body["options"].is_object()) {
                     auto& opts = body["options"];
                     if (opts.contains("publishDelay") && opts["publishDelay"].is_number()) {
                         config.publish_delay = opts["publishDelay"].get<int>();
                     }
                     if (opts.contains("refreshPeriod") && opts["refreshPeriod"].is_number()) {
                         config.refresh_period = opts["refreshPeriod"].get<int>();
                     }
                 }
                 int payload_throttle = 0;
                 if (body.contains("options") && body["options"].is_object()) {
                     auto& opts = body["options"];
                     if (opts.contains("payloadThrottle") && opts["payloadThrottle"].is_number()) {
                         payload_throttle = opts["payloadThrottle"].get<int>();
                     }
                 }

                 auto collector = std::make_shared<EventCollector>();
                 auto publisher = std::make_shared<FailablePublisher>();
                 std::string context_id = make_context_id();
                 bool fetches_from_endpoint = false;

                 try {
                     std::unique_ptr<absmartly::Context> ctx;

                     if (!body.contains("data") && body.contains("endpoint") && body["endpoint"].is_string()) {
                         // Live fetch: the SDK's Client GETs `{endpoint}/context`
                         // itself. The mock collector route answers 404 for an
                         // unknown payload, so a missing payload fails the
                         // context instead of loading as ready-but-empty.
                         fetches_from_endpoint = true;
                         absmartly::ClientConfig client_config;
                         client_config.endpoint = translate_endpoint(body["endpoint"].get<std::string>());
                         client_config.api_key = "test-api-key";
                         client_config.application = "test-app";
                         client_config.environment = "test-env";
                         auto client = std::make_shared<absmartly::Client>(
                             client_config, std::make_shared<absmartly::DefaultHTTPClient>());
                         auto provider = std::make_shared<absmartly::DefaultContextDataProvider>(client);

                         if (payload_throttle > 0) {
                             // Return before the data arrives: the context stays
                             // not-ready until the delayed SDK fetch resolves
                             // (is_ready() picks it up). Refresh uses the
                             // undelayed SDK provider.
                             DeferredContextDataProvider deferred(provider, payload_throttle);
                             ctx = std::make_unique<absmartly::Context>(
                                 config, deferred.get_context_data(), collector, publisher, provider);
                         } else {
                             absmartly::SDKConfig sdk_config;
                             sdk_config.client = client;
                             sdk_config.context_data_provider = provider;
                             sdk_config.context_event_handler = collector;
                             sdk_config.context_event_publisher = publisher;
                             auto sdk = absmartly::SDK::create(std::move(sdk_config));
                             ctx = sdk->create_context(config);
                             ctx->wait_until_ready();
                         }
                     } else if (body.contains("failLoad") && body["failLoad"].is_boolean() && body["failLoad"].get<bool>()) {
                         auto promise = std::make_shared<std::promise<absmartly::ContextData>>();
                         ctx = std::make_unique<absmartly::Context>(config, promise->get_future(), collector, publisher);
                         promise->set_exception(std::make_exception_ptr(std::runtime_error("Context load failed")));
                         // Resolve the load now, so the failed state, ready_error
                         // and the `error` event all exist before we respond.
                         ctx->wait_until_ready();
                     } else {
                         absmartly::ContextData context_data;
                         if (body.contains("data")) {
                             context_data = parse_context_data(body["data"]);
                         }
                         ctx = std::make_unique<absmartly::Context>(config, context_data, collector, publisher);
                     }

                     bool ready = ctx->is_ready();
                     bool failed = ctx->is_failed();
                     bool finalized = ctx->is_finalized();
                     auto all = collector->all_events();

                     auto entry = std::make_shared<ContextEntry>();
                     entry->context = std::move(ctx);
                     entry->collector = collector;
                     entry->publisher = publisher;
                     entry->unit_original_values = std::move(original_units);
                     entry->fetches_from_endpoint = fetches_from_endpoint;
                     store_context(context_id, std::move(entry));

                     json result = {{"contextId", context_id},
                                    {"ready", ready},
                                    {"failed", failed},
                                    {"finalized", finalized}};

                     json resp = make_response(result, all);
                     res.set_content(resp.dump(), "application/json");
                 } catch (const std::exception& e) {
                     res.status = 400;
                     res.set_content(
                         make_error_response(e.what(), "CREATE_ERROR").dump(),
                         "application/json");
                 }
             });

    svr.Post(R"(/context/([^/]+)/setUnit)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 json body;
                 try {
                     body = json::parse(req.body);
                 } catch (...) {
                     res.status = 400;
                     res.set_content(
                         make_error_response("Invalid JSON", "PARSE_ERROR").dump(),
                         "application/json");
                     return;
                 }

                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 std::string unit_type = body.value("unitType", "");
                 json uid_json = body["uid"];
                 std::string uid = uid_to_string(uid_json);

                 size_t events_before = entry->collector->size();

                 try {
                     entry->context->set_unit(unit_type, uid);
                     entry->unit_original_values[unit_type] = uid_json;
                 } catch (const std::exception& e) {
                     res.status = 400;
                     res.set_content(
                         make_error_response(e.what(), "SET_UNIT_ERROR").dump(),
                         "application/json");
                     return;
                 }

                 auto new_events = entry->collector->get_new_events(events_before);
                 json resp = make_response(nullptr, new_events);
                 res.set_content(resp.dump(), "application/json");
             });

    svr.Post(R"(/context/([^/]+)/getUnit)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 json body;
                 try {
                     body = json::parse(req.body);
                 } catch (...) {
                     res.status = 400;
                     res.set_content(
                         make_error_response("Invalid JSON", "PARSE_ERROR").dump(),
                         "application/json");
                     return;
                 }

                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 std::string unit_type = body.value("unitType", "");
                 size_t events_before = entry->collector->size();

                 auto unit = entry->context->get_unit(unit_type);

                 auto new_events = entry->collector->get_new_events(events_before);

                 if (unit.has_value()) {
                     json result_val;
                     auto orig_it = entry->unit_original_values.find(unit_type);
                     if (orig_it != entry->unit_original_values.end()) {
                         result_val = orig_it->second;
                     } else {
                         const std::string& uid_str = unit.value();
                         bool is_integer = !uid_str.empty();
                         bool has_dot = false;
                         for (size_t i = 0; i < uid_str.size(); ++i) {
                             char c = uid_str[i];
                             if (c == '.') {
                                 has_dot = true;
                             } else if (c == '-' && i == 0) {
                                 continue;
                             } else if (c < '0' || c > '9') {
                                 is_integer = false;
                                 break;
                             }
                         }
                         if (is_integer && !has_dot) {
                             try {
                                 result_val = std::stoll(uid_str);
                             } catch (...) {
                                 result_val = uid_str;
                             }
                         } else if (is_integer && has_dot) {
                             try {
                                 result_val = std::stod(uid_str);
                             } catch (...) {
                                 result_val = uid_str;
                             }
                         } else {
                             result_val = uid_str;
                         }
                     }
                     json resp = make_response(result_val, new_events);
                     res.set_content(resp.dump(), "application/json");
                 } else {
                     json resp = make_response(nullptr, new_events);
                     res.set_content(resp.dump(), "application/json");
                 }
             });

    svr.Post(R"(/context/([^/]+)/attribute)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 json body;
                 try {
                     body = json::parse(req.body);
                 } catch (...) {
                     res.status = 400;
                     res.set_content(
                         make_error_response("Invalid JSON", "PARSE_ERROR").dump(),
                         "application/json");
                     return;
                 }

                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 std::string name = body.value("name", "");
                 json value = body.contains("value") ? body["value"] : json(nullptr);

                 size_t events_before = entry->collector->size();

                 try {
                     entry->context->set_attribute(name, value);
                 } catch (const std::exception& e) {
                     res.status = 400;
                     res.set_content(
                         make_error_response(e.what(), "SET_ATTRIBUTE_ERROR").dump(),
                         "application/json");
                     return;
                 }

                 auto new_events = entry->collector->get_new_events(events_before);
                 json resp = make_response(nullptr, new_events);
                 res.set_content(resp.dump(), "application/json");
             });

    svr.Post(R"(/context/([^/]+)/getAttribute)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 json body;
                 try {
                     body = json::parse(req.body);
                 } catch (...) {
                     res.status = 400;
                     res.set_content(
                         make_error_response("Invalid JSON", "PARSE_ERROR").dump(),
                         "application/json");
                     return;
                 }

                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 std::string name = body.value("name", "");
                 size_t events_before = entry->collector->size();

                 json result = entry->context->get_attribute(name);

                 auto new_events = entry->collector->get_new_events(events_before);
                 json resp = make_response(result, new_events);
                 res.set_content(resp.dump(), "application/json");
             });

    svr.Post(R"(/context/([^/]+)/treatment)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 json body;
                 try {
                     body = json::parse(req.body);
                 } catch (...) {
                     res.status = 400;
                     res.set_content(
                         make_error_response("Invalid JSON", "PARSE_ERROR").dump(),
                         "application/json");
                     return;
                 }

                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 std::string experiment_name = body.value("experimentName", "");
                 size_t events_before = entry->collector->size();

                 // The cpp SDK's treatment() has no finalize guard
                 // (context.cpp:244) and would return the default variant, so
                 // enforce the spec's "finalized => error" here (scenario 189).
                 if (entry->context->is_finalized()) {
                     res.status = 400;
                     res.set_content(
                         make_error_response("Context finalized", "TREATMENT_ERROR").dump(),
                         "application/json");
                     return;
                 }

                 try {
                     int variant = entry->context->treatment(experiment_name);
                     auto new_events = entry->collector->get_new_events(events_before);
                     json resp = make_response(variant, new_events);
                     res.set_content(resp.dump(), "application/json");
                 } catch (const std::exception& e) {
                     res.status = 400;
                     res.set_content(
                         make_error_response(e.what(), "TREATMENT_ERROR").dump(),
                         "application/json");
                 }
             });

    svr.Post(R"(/context/([^/]+)/peek)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 json body;
                 try {
                     body = json::parse(req.body);
                 } catch (...) {
                     res.status = 400;
                     res.set_content(
                         make_error_response("Invalid JSON", "PARSE_ERROR").dump(),
                         "application/json");
                     return;
                 }

                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 std::string experiment_name = body.value("experimentName", "");
                 size_t events_before = entry->collector->size();

                 try {
                     int variant = entry->context->peek(experiment_name);
                     auto new_events = entry->collector->get_new_events(events_before);
                     json resp = make_response(variant, new_events);
                     res.set_content(resp.dump(), "application/json");
                 } catch (const std::exception& e) {
                     res.status = 400;
                     res.set_content(
                         make_error_response(e.what(), "PEEK_ERROR").dump(),
                         "application/json");
                 }
             });

    svr.Post(R"(/context/([^/]+)/variableValue)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 json body;
                 try {
                     body = json::parse(req.body);
                 } catch (...) {
                     res.status = 400;
                     res.set_content(
                         make_error_response("Invalid JSON", "PARSE_ERROR").dump(),
                         "application/json");
                     return;
                 }

                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 std::string key = body.value("key", "");
                 json default_value =
                     body.contains("defaultValue") ? body["defaultValue"] : json(nullptr);

                 size_t events_before = entry->collector->size();

                 try {
                     json value = entry->context->variable_value(key, default_value);
                     auto new_events = entry->collector->get_new_events(events_before);
                     json resp = make_response(value, new_events);
                     res.set_content(resp.dump(), "application/json");
                 } catch (const std::exception& e) {
                     res.status = 400;
                     res.set_content(
                         make_error_response(e.what(), "VARIABLE_VALUE_ERROR").dump(),
                         "application/json");
                 }
             });

    svr.Post(R"(/context/([^/]+)/peekVariableValue)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 json body;
                 try {
                     body = json::parse(req.body);
                 } catch (...) {
                     res.status = 400;
                     res.set_content(
                         make_error_response("Invalid JSON", "PARSE_ERROR").dump(),
                         "application/json");
                     return;
                 }

                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 std::string key = body.value("key", "");
                 json default_value =
                     body.contains("defaultValue") ? body["defaultValue"] : json(nullptr);

                 size_t events_before = entry->collector->size();

                 try {
                     json value = entry->context->peek_variable_value(key, default_value);
                     auto new_events = entry->collector->get_new_events(events_before);
                     json resp = make_response(value, new_events);
                     res.set_content(resp.dump(), "application/json");
                 } catch (const std::exception& e) {
                     res.status = 400;
                     res.set_content(
                         make_error_response(e.what(), "PEEK_VARIABLE_VALUE_ERROR")
                             .dump(),
                         "application/json");
                 }
             });

    svr.Post(R"(/context/([^/]+)/track)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 json body;
                 try {
                     body = json::parse(req.body);
                 } catch (...) {
                     res.status = 400;
                     res.set_content(
                         make_error_response("Invalid JSON", "PARSE_ERROR").dump(),
                         "application/json");
                     return;
                 }

                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 std::string goal_name = body.value("goalName", "");

                 json properties = json();
                 if (body.contains("properties")) {
                     properties = body["properties"];
                 }

                 // The SDK coerces non-object properties to null and tracks
                 // anyway, so enforce the spec's type check here: a present
                 // properties value that is not an object (number, string,
                 // array) must fail (scenarios 37/38/39).
                 if (body.contains("properties") && !properties.is_null() &&
                     !properties.is_object()) {
                     res.status = 400;
                     res.set_content(
                         make_error_response(
                             "Goal '" + goal_name + "' properties must be of type object.",
                             "TRACK_ERROR")
                             .dump(),
                         "application/json");
                     return;
                 }

                 size_t events_before = entry->collector->size();

                 try {
                     entry->context->track(goal_name, properties);
                     auto new_events = entry->collector->get_new_events(events_before);
                     json resp = make_response(nullptr, new_events);
                     res.set_content(resp.dump(), "application/json");
                 } catch (const std::exception& e) {
                     res.status = 400;
                     res.set_content(
                         make_error_response(e.what(), "TRACK_ERROR").dump(),
                         "application/json");
                 }
             });

    // `override` and its spec alias `setOverride`.
    svr.Post(R"(/context/([^/]+)/(?:override|setOverride))",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 json body;
                 try {
                     body = json::parse(req.body);
                 } catch (...) {
                     res.status = 400;
                     res.set_content(
                         make_error_response("Invalid JSON", "PARSE_ERROR").dump(),
                         "application/json");
                     return;
                 }

                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 std::string experiment_name = body.value("experimentName", "");
                 int variant = body.value("variant", 0);

                 size_t events_before = entry->collector->size();

                 try {
                     entry->context->set_override(experiment_name, variant);
                     auto new_events = entry->collector->get_new_events(events_before);
                     json resp = make_response(nullptr, new_events);
                     res.set_content(resp.dump(), "application/json");
                 } catch (const std::exception& e) {
                     res.status = 400;
                     res.set_content(
                         make_error_response(e.what(), "OVERRIDE_ERROR").dump(),
                         "application/json");
                 }
             });

    // `customAssignment` and its spec alias `setCustomAssignment`.
    svr.Post(R"(/context/([^/]+)/(?:customAssignment|setCustomAssignment))",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 json body;
                 try {
                     body = json::parse(req.body);
                 } catch (...) {
                     res.status = 400;
                     res.set_content(
                         make_error_response("Invalid JSON", "PARSE_ERROR").dump(),
                         "application/json");
                     return;
                 }

                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 std::string experiment_name = body.value("experimentName", "");
                 int variant = body.value("variant", 0);

                 size_t events_before = entry->collector->size();

                 try {
                     entry->context->set_custom_assignment(experiment_name, variant);
                     auto new_events = entry->collector->get_new_events(events_before);
                     json resp = make_response(nullptr, new_events);
                     res.set_content(resp.dump(), "application/json");
                 } catch (const std::exception& e) {
                     res.status = 400;
                     res.set_content(
                         make_error_response(e.what(), "CUSTOM_ASSIGNMENT_ERROR").dump(),
                         "application/json");
                 }
             });

    svr.Post(R"(/context/([^/]+)/customFieldValue)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 json body;
                 try {
                     body = json::parse(req.body);
                 } catch (...) {
                     res.status = 400;
                     res.set_content(
                         make_error_response("Invalid JSON", "PARSE_ERROR").dump(),
                         "application/json");
                     return;
                 }

                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 std::string experiment_name = body.value("experimentName", "");
                 std::string field_name = body.value("fieldName", "");

                 size_t events_before = entry->collector->size();

                 json value = entry->context->custom_field_value(experiment_name, field_name);

                 auto new_events = entry->collector->get_new_events(events_before);
                 json resp = make_response(value, new_events);
                 res.set_content(resp.dump(), "application/json");
             });

    svr.Get(R"(/context/([^/]+)/pending)",
            [](const httplib::Request& req, httplib::Response& res) {
                std::string id = req.matches[1];

                auto entry = lock_context(id);
                if (!entry) {
                    res.status = 404;
                    res.set_content(
                        make_error_response("Context not found", "NOT_FOUND").dump(),
                        "application/json");
                    return;
                }

                int count = entry->context->pending();
                std::vector<Event> empty;
                json resp = make_response(count, empty);
                res.set_content(resp.dump(), "application/json");
            });

    svr.Get(R"(/context/([^/]+)/isFinalized)",
            [](const httplib::Request& req, httplib::Response& res) {
                std::string id = req.matches[1];

                auto entry = lock_context(id);
                if (!entry) {
                    res.status = 404;
                    res.set_content(
                        make_error_response("Context not found", "NOT_FOUND").dump(),
                        "application/json");
                    return;
                }

                bool finalized = entry->context->is_finalized();
                std::vector<Event> empty;
                json resp = make_response(finalized, empty);
                res.set_content(resp.dump(), "application/json");
            });

    svr.Get(R"(/context/([^/]+)/isReady)",
            [](const httplib::Request& req, httplib::Response& res) {
                std::string id = req.matches[1];

                auto entry = lock_context(id);
                if (!entry) {
                    res.status = 404;
                    res.set_content(
                        make_error_response("Context not found", "NOT_FOUND").dump(),
                        "application/json");
                    return;
                }

                bool ready = entry->context->is_ready();
                std::vector<Event> empty;
                json resp = make_response(ready, empty);
                res.set_content(resp.dump(), "application/json");
            });

    svr.Get(R"(/context/([^/]+)/isFailed)",
            [](const httplib::Request& req, httplib::Response& res) {
                std::string id = req.matches[1];

                auto entry = lock_context(id);
                if (!entry) {
                    res.status = 404;
                    res.set_content(
                        make_error_response("Context not found", "NOT_FOUND").dump(),
                        "application/json");
                    return;
                }

                // The SDK's is_failed()/ready_error() only read state; the
                // non-blocking is_ready() is what resolves a load future that
                // has completed (e.g. a throttled fetch that failed).
                entry->context->is_ready();
                bool failed = entry->context->is_failed();
                std::vector<Event> empty;
                json resp = make_response(failed, empty);
                res.set_content(resp.dump(), "application/json");
            });

    svr.Get(R"(/context/([^/]+)/experiments)",
            [](const httplib::Request& req, httplib::Response& res) {
                std::string id = req.matches[1];

                auto entry = lock_context(id);
                if (!entry) {
                    res.status = 404;
                    res.set_content(
                        make_error_response("Context not found", "NOT_FOUND").dump(),
                        "application/json");
                    return;
                }

                auto exp_names = entry->context->experiments();
                json result = json::array();
                for (const auto& name : exp_names) {
                    result.push_back(name);
                }

                std::vector<Event> empty;
                json resp = make_response(result, empty);
                res.set_content(resp.dump(), "application/json");
            });

    svr.Post(R"(/context/([^/]+)/variableKeys)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];

                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 auto var_keys = entry->context->variable_keys();
                 json result = json::array();
                 for (const auto& [key, _] : var_keys) {
                     result.push_back(key);
                 }

                 std::vector<Event> empty;
                 json resp = make_response(result, empty);
                 res.set_content(resp.dump(), "application/json");
             });

    svr.Post(R"(/context/([^/]+)/customFieldKeys)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 json body;
                 try {
                     body = json::parse(req.body);
                 } catch (...) {
                     res.status = 400;
                     res.set_content(
                         make_error_response("Invalid JSON", "PARSE_ERROR").dump(),
                         "application/json");
                     return;
                 }

                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 json result = json::array();
                 for (const auto& key : entry->context->custom_field_keys()) {
                     result.push_back(key);
                 }

                 std::vector<Event> empty;
                 json resp = make_response(result, empty);
                 res.set_content(resp.dump(), "application/json");
             });

    svr.Post(R"(/context/([^/]+)/customFieldValueType)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 json body;
                 try {
                     body = json::parse(req.body);
                 } catch (...) {
                     res.status = 400;
                     res.set_content(
                         make_error_response("Invalid JSON", "PARSE_ERROR").dump(),
                         "application/json");
                     return;
                 }

                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 std::string experiment_name = body.value("experimentName", "");
                 std::string field_name = body.value("fieldName", "");

                 auto type = entry->context->custom_field_value_type(experiment_name, field_name);
                 json result = type ? json(*type) : json(nullptr);

                 std::vector<Event> empty;
                 json resp = make_response(result, empty);
                 res.set_content(resp.dump(), "application/json");
             });

    svr.Post(R"(/context/([^/]+)/getUnits)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(make_error_response("Context not found", "NOT_FOUND").dump(), "application/json");
                     return;
                 }
                 size_t eb = entry->collector->size();
                 auto units = entry->context->get_units();
                 json result = json::object();
                 for (auto& [k, v] : units) {
                     try { result[k] = std::stoi(v); }
                     catch (...) {
                         try { result[k] = std::stod(v); }
                         catch (...) { result[k] = v; }
                     }
                 }
                 auto ne = entry->collector->get_new_events(eb);
                 res.set_content(make_response(result, ne).dump(), "application/json");
             });

    svr.Post(R"(/context/([^/]+)/getAttributes)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(make_error_response("Context not found", "NOT_FOUND").dump(), "application/json");
                     return;
                 }
                 size_t eb = entry->collector->size();
                 auto attrs = entry->context->get_attributes();
                 auto ne = entry->collector->get_new_events(eb);
                 res.set_content(make_response(attrs, ne).dump(), "application/json");
             });

    svr.Post(R"(/context/([^/]+)/readyError)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(make_error_response("Context not found", "NOT_FOUND").dump(), "application/json");
                     return;
                 }
                 entry->context->is_ready();  // resolve a completed load future (see isFailed)
                 auto error = entry->context->ready_error();
                 json result = error.empty() ? json(nullptr) : json{{"isError", true}, {"message", error}};
                 res.set_content(make_response(result, {}).dump(), "application/json");
             });

    svr.Post(R"(/context/([^/]+)/variableKeysMap)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(make_error_response("Context not found", "NOT_FOUND").dump(), "application/json");
                     return;
                 }
                 size_t eb = entry->collector->size();
                 auto keys = entry->context->variable_keys();
                 auto ne = entry->collector->get_new_events(eb);
                 res.set_content(make_response(keys, ne).dump(), "application/json");
             });

    svr.Post(R"(/context/([^/]+)/globalCustomFieldKeys)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(make_error_response("Context not found", "NOT_FOUND").dump(), "application/json");
                     return;
                 }
                 size_t eb = entry->collector->size();
                 auto keys = entry->context->custom_field_keys();
                 auto ne = entry->collector->get_new_events(eb);
                 res.set_content(make_response(keys, ne).dump(), "application/json");
             });

    svr.Post(R"(/context/([^/]+)/publishFail)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(make_error_response("Context not found", "NOT_FOUND").dump(), "application/json");
                     return;
                 }
                 if (!entry->publisher) {
                     res.status = 501;
                     res.set_content(make_error_response("publishFail not supported for this context", "UNSUPPORTED").dump(), "application/json");
                     return;
                 }
                 entry->publisher->arm_failure();
                 res.set_content(make_response(nullptr, {}).dump(), "application/json");
             });

    svr.Post(R"(/context/([^/]+)/publish)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];

                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 size_t events_before = entry->collector->size();

                 try {
                     absmartly::PublishEvent pub_event = entry->context->publish();
                     auto new_events = entry->collector->get_new_events(events_before);
                     json resp = make_response(nullptr, new_events);
                     res.set_content(resp.dump(), "application/json");
                 } catch (const std::exception& e) {
                     res.status = 500;
                     res.set_content(
                         make_error_response(e.what(), "PUBLISH_ERROR").dump(),
                         "application/json");
                 }
             });

    svr.Post(R"(/context/([^/]+)/refresh)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 // The body is optional (the orchestrator sends `{}` unless it
                 // has new data).
                 json body = json::object();
                 if (req.body.find_first_not_of(" \t\r\n") != std::string::npos) {
                     try {
                         body = json::parse(req.body);
                     } catch (...) {
                         res.status = 400;
                         res.set_content(
                             make_error_response("Invalid JSON", "PARSE_ERROR").dump(),
                             "application/json");
                         return;
                     }
                 }

                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 size_t events_before = entry->collector->size();

                 try {
                     if (body.contains("newData") && !body["newData"].is_null()) {
                         // Supplied data is applied as given, so direct callers
                         // get the data they sent even on endpoint-backed contexts.
                         absmartly::ContextData new_data = parse_context_data(body["newData"]);
                         entry->context->refresh(new_data);
                     } else if (entry->fetches_from_endpoint) {
                         // No newData: the SDK re-fetches through its own data
                         // provider, as a real refresh does.
                         entry->context->refresh();
                     } else {
                         entry->context->refresh(entry->context->data());
                     }
                     auto new_events = entry->collector->get_new_events(events_before);
                     json resp = make_response(nullptr, new_events);
                     res.set_content(resp.dump(), "application/json");
                 } catch (const std::exception& e) {
                     res.status = 400;
                     res.set_content(
                         make_error_response(e.what(), "REFRESH_ERROR").dump(),
                         "application/json");
                 }
             });

    svr.Post(R"(/context/([^/]+)/finalize)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];

                 auto entry = lock_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 size_t events_before = entry->collector->size();

                 try {
                     absmartly::PublishEvent pub_event = entry->context->finalize();
                     auto new_events = entry->collector->get_new_events(events_before);
                     json resp = make_response(nullptr, new_events);
                     res.set_content(resp.dump(), "application/json");
                 } catch (const std::exception& e) {
                     res.status = 400;
                     res.set_content(
                         make_error_response(e.what(), "FINALIZE_ERROR").dump(),
                         "application/json");
                 }
             });

    svr.Delete(R"(/context/([^/]+))",
               [](const httplib::Request& req, httplib::Response& res) {
                   std::string id = req.matches[1];

                   // Take the entry out under the map lock but destroy it after
                   // releasing it: destroying a context whose throttled fetch
                   // is still pending waits for that fetch.
                   std::shared_ptr<ContextEntry> removed;
                   {
                       std::lock_guard<std::mutex> lock(contexts_mu);
                       auto it = contexts.find(id);
                       if (it != contexts.end()) {
                           removed = std::move(it->second);
                           contexts.erase(it);
                       }
                   }
                   removed.reset();

                   json resp = {{"result", "deleted"}};
                   res.set_content(resp.dump(), "application/json");
               });

    std::cout << "C++ SDK wrapper listening on port " << server_port << std::endl;
    svr.listen("0.0.0.0", server_port);

    return 0;
}
