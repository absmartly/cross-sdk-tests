#include <httplib.h>
#include <nlohmann/json.hpp>

#include <absmartly/context.h>
#include <absmartly/context_config.h>
#include <absmartly/context_event_handler.h>
#include <absmartly/models.h>
#include <absmartly/errors.h>
#include <absmartly/hashing.h>

#include <atomic>
#include <chrono>
#include <future>
#include <map>
#include <memory>
#include <mutex>
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

class EventCollector : public absmartly::ContextEventHandler {
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

struct ContextEntry {
    std::unique_ptr<absmartly::Context> context;
    std::shared_ptr<EventCollector> collector;
    std::map<std::string, json> unit_original_values;
    bool publishFail = false;
};

static std::mutex contexts_mu;
static std::map<std::string, ContextEntry> contexts;
static std::atomic<int> context_counter{0};

static std::mutex payloads_mu;
static std::map<std::string, absmartly::ContextData> payload_store;

static std::string make_context_id() {
    int n = context_counter.fetch_add(1);
    return "ctx-" + std::to_string(n);
}

static ContextEntry* get_context(const std::string& id) {
    auto it = contexts.find(id);
    if (it == contexts.end()) {
        return nullptr;
    }
    return &it->second;
}

static json make_response(const json& result, const std::vector<Event>& events) {
    return json{{"result", result}, {"events", events_to_json(events)}};
}

static json make_error_response(const std::string& error,
                                const std::string& code) {
    return json{{"error", error}, {"code", code}};
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

int main() {
    httplib::Server svr;

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

    svr.Get(R"(/context_payload/([^/]+)$)",
            [](const httplib::Request& req, httplib::Response& res) {
                std::string payload_id = req.matches[1];
                absmartly::ContextData cd;

                {
                    std::lock_guard<std::mutex> lock(payloads_mu);
                    auto it = payload_store.find(payload_id);
                    if (it != payload_store.end()) {
                        cd = it->second;
                    }
                }

                json resp = cd;
                res.set_content(resp.dump(), "application/json");
            });

    svr.Get(R"(/context_payload/([^/]+)/context)",
            [](const httplib::Request& req, httplib::Response& res) {
                std::string payload_id = req.matches[1];
                absmartly::ContextData cd;

                {
                    std::lock_guard<std::mutex> lock(payloads_mu);
                    auto it = payload_store.find(payload_id);
                    if (it != payload_store.end()) {
                        cd = it->second;
                    }
                }

                json resp = cd;
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
                 std::string context_id = make_context_id();

                 try {
                     std::unique_ptr<absmartly::Context> ctx;

                     if (payload_throttle > 0 && body.contains("endpoint") && body["endpoint"].is_string()) {
                         // Deferred context: create with a pending future, resolve after throttle delay
                         std::string endpoint = body["endpoint"].get<std::string>();
                         auto promise = std::make_shared<std::promise<absmartly::ContextData>>();
                         ctx = std::make_unique<absmartly::Context>(config, promise->get_future(), collector);

                         std::thread([promise, endpoint, payload_throttle]() {
                             std::this_thread::sleep_for(std::chrono::milliseconds(payload_throttle));
                             absmartly::ContextData deferred_data;
                             auto payload_pos = endpoint.find("/context_payload/");
                             if (payload_pos != std::string::npos) {
                                 std::string payload_id = endpoint.substr(payload_pos + 17);
                                 std::lock_guard<std::mutex> plock(payloads_mu);
                                 auto it = payload_store.find(payload_id);
                                 if (it != payload_store.end()) {
                                     deferred_data = it->second;
                                 }
                             }
                             promise->set_value(std::move(deferred_data));
                         }).detach();
                     } else if (body.contains("failLoad") && body["failLoad"].is_boolean() && body["failLoad"].get<bool>()) {
                         auto promise = std::make_shared<std::promise<absmartly::ContextData>>();
                         ctx = std::make_unique<absmartly::Context>(config, promise->get_future(), collector);
                         promise->set_exception(std::make_exception_ptr(std::runtime_error("Context load failed")));
                         std::this_thread::sleep_for(std::chrono::milliseconds(50));
                     } else {
                         absmartly::ContextData context_data;
                         if (body.contains("data")) {
                             context_data = parse_context_data(body["data"]);
                         } else if (body.contains("endpoint") && body["endpoint"].is_string()) {
                             std::string endpoint = body["endpoint"].get<std::string>();
                             auto payload_pos = endpoint.find("/context_payload/");
                             if (payload_pos != std::string::npos) {
                                 std::string payload_id = endpoint.substr(payload_pos + 17);
                                 std::lock_guard<std::mutex> plock(payloads_mu);
                                 auto it = payload_store.find(payload_id);
                                 if (it != payload_store.end()) {
                                     context_data = it->second;
                                 }
                             }
                         }
                         ctx = std::make_unique<absmartly::Context>(config, context_data, collector);
                     }

                     bool ready = ctx->is_ready();
                     bool failed = ctx->is_failed();
                     bool finalized = ctx->is_finalized();
                     auto all = collector->all_events();

                     ContextEntry entry;
                     entry.context = std::move(ctx);
                     entry.collector = collector;
                     entry.unit_original_values = std::move(original_units);

                     {
                         std::lock_guard<std::mutex> lock(contexts_mu);
                         contexts[context_id] = std::move(entry);
                     }

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

                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
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

                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
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

                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
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

                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
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

                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
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

                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
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

                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
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

                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
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

                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
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

    svr.Post(R"(/context/([^/]+)/override)",
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

                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
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

    svr.Post(R"(/context/([^/]+)/customAssignment)",
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

                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
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

                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
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

                std::lock_guard<std::mutex> lock(contexts_mu);
                auto* entry = get_context(id);
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

                std::lock_guard<std::mutex> lock(contexts_mu);
                auto* entry = get_context(id);
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

                std::lock_guard<std::mutex> lock(contexts_mu);
                auto* entry = get_context(id);
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

                std::lock_guard<std::mutex> lock(contexts_mu);
                auto* entry = get_context(id);
                if (!entry) {
                    res.status = 404;
                    res.set_content(
                        make_error_response("Context not found", "NOT_FOUND").dump(),
                        "application/json");
                    return;
                }

                bool failed = entry->context->is_failed();
                std::vector<Event> empty;
                json resp = make_response(failed, empty);
                res.set_content(resp.dump(), "application/json");
            });

    svr.Get(R"(/context/([^/]+)/experiments)",
            [](const httplib::Request& req, httplib::Response& res) {
                std::string id = req.matches[1];

                std::lock_guard<std::mutex> lock(contexts_mu);
                auto* entry = get_context(id);
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

                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
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

                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
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

                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 std::string experiment_name = body.value("experimentName", "");
                 std::string field_name = body.value("fieldName", "");

                 json result = nullptr;
                 const auto& ctx_data = entry->context->data();
                 for (const auto& exp : ctx_data.experiments) {
                     if (exp.name == experiment_name) {
                         for (const auto& field : exp.customFieldValues) {
                             if (field.name == field_name) {
                                 result = field.type;
                                 break;
                             }
                         }
                         break;
                     }
                 }

                 std::vector<Event> empty;
                 json resp = make_response(result, empty);
                 res.set_content(resp.dump(), "application/json");
             });

    svr.Post(R"(/context/([^/]+)/getUnits)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
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
                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
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
                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(make_error_response("Context not found", "NOT_FOUND").dump(), "application/json");
                     return;
                 }
                 auto error = entry->context->ready_error();
                 json result = error.empty() ? json(nullptr) : json(error);
                 res.set_content(make_response(result, {}).dump(), "application/json");
             });

    svr.Post(R"(/context/([^/]+)/variableKeysMap)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];
                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
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
                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
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
                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(make_error_response("Context not found", "NOT_FOUND").dump(), "application/json");
                     return;
                 }
                 entry->publishFail = true;
                 res.set_content(make_response(nullptr, {}).dump(), "application/json");
             });

    svr.Post(R"(/context/([^/]+)/publish)",
             [](const httplib::Request& req, httplib::Response& res) {
                 std::string id = req.matches[1];

                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 if (entry->publishFail) {
                     res.status = 500;
                     res.set_content(
                         make_error_response("publish failed", "PUBLISH_ERROR").dump(),
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
                     res.status = 400;
                     res.set_content(
                         make_error_response(e.what(), "PUBLISH_ERROR").dump(),
                         "application/json");
                 }
             });

    svr.Post(R"(/context/([^/]+)/refresh)",
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

                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
                 if (!entry) {
                     res.status = 404;
                     res.set_content(
                         make_error_response("Context not found", "NOT_FOUND").dump(),
                         "application/json");
                     return;
                 }

                 size_t events_before = entry->collector->size();

                 try {
                     if (body.contains("newData")) {
                         absmartly::ContextData new_data = parse_context_data(body["newData"]);
                         entry->context->refresh(new_data);
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

                 std::lock_guard<std::mutex> lock(contexts_mu);
                 auto* entry = get_context(id);
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

                   {
                       std::lock_guard<std::mutex> lock(contexts_mu);
                       contexts.erase(id);
                   }

                   json resp = {{"result", "deleted"}};
                   res.set_content(resp.dump(), "application/json");
               });

    int port = 3000;
    const char* port_env = std::getenv("PORT");
    if (port_env) {
        port = std::atoi(port_env);
    }
    std::cout << "C++ SDK wrapper listening on port " << port << std::endl;
    svr.listen("0.0.0.0", port);

    return 0;
}
