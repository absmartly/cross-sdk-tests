// Behavioral self-test behind the `switchback` capability (R33, cluster-defined switchbacks).
// Mirrors java-wrapper/SwitchbackSelfTest.java: a sample of the platform golden vectors through
// the SDK's own assign_switchback/switchback_permutation, then switchback serving through a real
// Context on an R33 payload decoded from JSON text with nlohmann::ordered_json (the way scenario
// payloads are decoded), so the cluster map's declared order is exercised too. The verdict comes
// only from observed treatments, exposures and publish payloads.
//
// Compiled only against an SDK that has absmartly/switchback.h; without it the capability is
// false (see server.cpp). Run once per process and cached. Fails closed: any exception is false.
#pragma once

#include <absmartly/context.h>
#include <absmartly/context_config.h>
#include <absmartly/context_event_logger.h>
#include <absmartly/hashing.h>
#include <absmartly/models.h>
#include <absmartly/switchback.h>
#include <nlohmann/json.hpp>

#include "payload_store.h"

#include <charconv>
#include <cstdint>
#include <iostream>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace switchback_self_test {

using json = nlohmann::json;
using ordered_json = nlohmann::ordered_json;

inline const char* PREFIX = "[switchback probe]";

// Grid constants shared with generate_switchback_scenarios.py: with a 400,000 h period "now"
// stays in period 0 until 2069.
constexpr int64_t PERIOD_MS = 400000LL * 3600000LL;
constexpr int64_t ANCHOR_PERIOD_0 = 1704067200000LL;

// hash_unit("serving-9"): base64url-no-pad MD5, as published in PublishEvent.units.
inline const char* SERVING_UNIT_HASH = "r5uxZRoi-JuM5yYJa0kmSQ";

// Java Long.parseLong: one optional sign ('+' or '-') and ASCII digits, within int64.
inline bool parse_exact_int64(const std::string& text, int64_t& out) {
    const char* first = text.data();
    const char* last = first + text.size();
    if (first != last && *first == '+') ++first;
    if (first == last || *first == '+' || (first != text.data() && *first == '-')) return false;
    const auto [ptr, ec] = std::from_chars(first, last, out);
    return ec == std::errc{} && ptr == last;
}

inline bool check_decimal_parsing() {
    const std::pair<const char*, std::optional<int64_t>> cases[] = {
        {"1", 1}, {"+1", 1}, {"-1", -1}, {"0", 0}, {"+9223372036854775807", INT64_MAX},
        {"-9223372036854775808", INT64_MIN}, {"", std::nullopt}, {"+", std::nullopt}, {"-", std::nullopt},
        {"++1", std::nullopt}, {"+-1", std::nullopt}, {"-+1", std::nullopt}, {"--1", std::nullopt},
        {" 1", std::nullopt}, {"1.0", std::nullopt}, {"9223372036854775808", std::nullopt},
    };
    for (const auto& [text, expected] : cases) {
        int64_t value = 0;
        const bool ok = parse_exact_int64(text, value);
        if (ok != expected.has_value() || (ok && value != *expected)) {
            std::cout << PREFIX << " decimal parsing FAILED for \"" << text << "\"" << std::endl;
            return false;
        }
    }
    return true;
}

struct AssignCase {
    const char* unit;
    int64_t period;
    int64_t seed;
    int arms;
    int expected;
};

struct PermutationCase {
    const char* unit;
    int64_t cycle;
    int64_t seed;
    std::vector<int> expected;
};

// Taken from the platform golden vectors, including INT64_MIN/INT64_MAX periods and seeds.
inline bool check_golden_sample() {
    const AssignCase assign_cases[] = {
        {"market_1", 0, 0, 2, 0},
        {"market_1", INT64_MIN, 1, 2, 1},
        {"market_1", INT64_MAX, 1, 2, 1},
        {"123456789", 0, 0, 2, 1},
        {"market_1", 10, 0, 3, 2},
        {"123456789", -2, -1, 3, 0},
        {"market_1", 5, 1, 10, 0},
        {"market_2", -11, INT64_MIN, 10, 3},
        {"market_2", INT64_MAX, INT64_MIN, 10, 2},
        {"market_1", 1, INT64_MAX, 2, 1},
    };
    for (const auto& c : assign_cases) {
        const int actual = absmartly::assign_switchback(c.unit, c.period, c.seed, c.arms);
        if (actual != c.expected) {
            std::cout << PREFIX << " golden assign FAILED for " << c.unit << "/" << c.period << "/" << c.seed << "/"
                      << c.arms << ": got " << actual << std::endl;
            return false;
        }
    }
    const PermutationCase permutation_cases[] = {
        {"market_1", 0, 0, {0, 3, 7, 5, 8, 2, 6, 4, 1, 9}},
        {"market_1", INT64_MIN, 0, {1, 0}},
        {"market_1", INT64_MAX, 1, {0, 1}},
        {"123456789", 0, 0, {2, 0, 1}},
    };
    for (const auto& c : permutation_cases) {
        const auto actual = absmartly::switchback_permutation(c.unit, c.cycle, c.seed,
                                                              static_cast<int>(c.expected.size()));
        if (actual != c.expected) {
            std::cout << PREFIX << " golden permutation FAILED for " << c.unit << "/" << c.cycle << "/" << c.seed
                      << ": got " << json(actual).dump() << std::endl;
            return false;
        }
    }
    return true;
}

inline ordered_json base(int id, const std::string& name, int seed_hi, int seed_lo, int arms) {
    ordered_json split = ordered_json::array();
    ordered_json variants = ordered_json::array();
    for (int i = 0; i < arms; ++i) {
        split.push_back(1.0 / arms);
        variants.push_back({{"name", std::string(1, static_cast<char>('A' + i))}, {"config", nullptr}});
    }
    return {{"id", id},
            {"name", name},
            {"iteration", 1},
            {"unitType", "unit_serving"},
            {"seedHi", seed_hi},
            {"seedLo", seed_lo},
            {"split", split},
            {"trafficSeedHi", 1},
            {"trafficSeedLo", 2},
            {"trafficSplit", {0, 1}},
            {"fullOnVariant", 0},
            {"applications", {{{"name", "website"}}}},
            {"variants", variants},
            {"audienceStrict", false}};
}

inline ordered_json switchback(int id, const std::string& name, int seed_hi, int seed_lo, int arms,
                               const std::vector<std::pair<std::string, std::string>>& clusters) {
    auto experiment = base(id, name, seed_hi, seed_lo, arms);
    experiment["randomizationType"] = "switchback";
    if (!clusters.empty()) {
        ordered_json map = ordered_json::object();
        for (const auto& [type, path] : clusters) map[type] = path;
        experiment["switchbackClusters"] = map;
        experiment["unitType"] = clusters.front().first;
    }
    experiment["switchbackStartAt"] = ANCHOR_PERIOD_0;
    experiment["switchbackPeriodMs"] = PERIOD_MS;
    experiment["switchbackDesignType"] = "balanced_block";
    experiment["switchbackEstimand"] = "global_average_treatment_effect";
    return experiment;
}

// The unit baseline: seeds whose 3-arm unit draw for serving-9 is 1.
inline ordered_json unit(int id, const std::string& name) { return base(id, name, -559038737, 1234567890, 3); }

// The probe payload as the Collector serves it, as JSON text. Every expected variant is a
// platform golden vector, chosen (with the Python reference) to differ from every wrong model for
// both uids (serving-9, probe-user): the hashed key, the uid as key, the hashed uid as key, a
// Phase 1E bucket and the sticky unit assignment. See java-wrapper SwitchbackSelfTest.
//   assign_switchback("market_1", 0, 42, 10) = 3; assign_switchback("123456789", 0, 42, 10) = 6;
//   assign_switchback("market_1", 0, 1, 3) = 1 (payload shape only).
inline std::string probe_payload() {
    ordered_json experiments = ordered_json::array();
    experiments.push_back(switchback(521, "sb_agree", 0, 42, 10, {{"unit_serving", "city"}, {"unit_user", "profile/city"}}));
    experiments.push_back(
        switchback(522, "sb_declared_order", 0, 42, 10, {{"unit_user", "profile/city"}, {"unit_serving", "city"}}));
    experiments.push_back(switchback(523, "sb_disagree", 0, 42, 10, {{"unit_serving", "city"}, {"unit_user", "profile/zone"}}));
    experiments.push_back(switchback(524, "sb_whole_number", 0, 42, 10, {{"unit_serving", "store_number"}}));
    auto admit_all = switchback(528, "sb_allow_empty", 0, 42, 10, {{"unit_serving", "city"}});
    admit_all["switchbackClusterValues"] = ordered_json::array();
    experiments.push_back(admit_all);
    auto filtered = switchback(525, "sb_value_filtered", 0, 1, 10, {{"unit_serving", "city"}});
    filtered["switchbackClusterValues"] = {"market_2"};
    experiments.push_back(filtered);
    // Only the abandoned Phase 1E fields: no cluster map, so the legacy ineligible control.
    auto legacy = switchback(526, "sb_phase1e_only", 0, 1, 10, {});
    legacy["switchbackClusterUnitType"] = "unit_serving";
    legacy["switchbackClusterCount"] = 10;
    legacy["switchbackClusterAttribute"] = "city";
    experiments.push_back(legacy);
    // A float attribute never keys a cluster (scenario 411's 3.0): an ineligible control.
    experiments.push_back(switchback(529, "sb_float_value", 0, 42, 10, {{"unit_serving", "float_number"}}));
    experiments.push_back(switchback(513, "sb_payload", 0, 1, 3, {{"unit_serving", "city"}}));
    experiments.push_back(unit(514, "sb_unit_baseline"));
    auto with_fields = unit(527, "sb_unit_with_fields");
    with_fields["randomizationType"] = "unit";
    with_fields["switchbackClusters"] = {{"unit_serving", "city"}};
    with_fields["switchbackClusterValues"] = {"market_1"};
    with_fields["switchbackStartAt"] = ANCHOR_PERIOD_0;
    with_fields["switchbackPeriodMs"] = PERIOD_MS;
    with_fields["switchbackDesignType"] = "balanced_block";
    experiments.push_back(with_fields);
    return ordered_json{{"experiments", experiments}}.dump();
}

class ExposureLog : public absmartly::ContextEventLogger {
public:
    void handle_event(absmartly::Context&, const std::string& type, const json& data) override {
        if (type == "exposure") exposures.push_back(data);
    }
    std::vector<json> exposures;
};

inline json served(int id, const std::string& name, const std::string& unit_type, int variant) {
    return {{"id", id},           {"name", name},       {"unit", unit_type},   {"variant", variant},
            {"assigned", true},   {"eligible", true},   {"overridden", false}, {"fullOn", false},
            {"custom", false},    {"audienceMismatch", false}, {"ruleOverride", false}};
}

inline bool served_as(json actual, const json& expected) {
    if (!actual.contains("exposedAt") || !actual["exposedAt"].is_number_integer() ||
        actual["exposedAt"].get<int64_t>() <= 0) {
        return false;
    }
    actual.erase("exposedAt");
    return actual == expected;
}

inline bool expect(absmartly::Context& context, ExposureLog& log, const char* check, const std::string& name,
                   int expected_treatment, const std::vector<json>& expected_exposures) {
    const size_t before = log.exposures.size();
    const int treatment = context.treatment(name);
    const std::vector<json> emitted(log.exposures.begin() + static_cast<long>(before), log.exposures.end());
    bool matches = treatment == expected_treatment && emitted.size() == expected_exposures.size();
    for (size_t i = 0; matches && i < emitted.size(); ++i) matches = served_as(emitted[i], expected_exposures[i]);
    if (!matches) {
        std::cout << PREFIX << " check " << check << " FAILED: expected treatment=" << expected_treatment
                  << " exposures=" << json(expected_exposures).dump() << ", got treatment=" << treatment
                  << " exposures=" << json(emitted).dump() << std::endl;
    }
    return matches;
}

// The keys at every depth, so a switchback-only field anywhere in a payload is caught.
inline json shape(const json& value) {
    if (value.is_object()) {
        json out = json::object();
        for (auto it = value.begin(); it != value.end(); ++it) out[it.key()] = shape(it.value());
        return out;
    }
    if (value.is_array()) {
        json out = json::array();
        for (const auto& item : value) out.push_back(shape(item));
        return out;
    }
    return value.type_name();
}

inline bool published_exactly(const absmartly::PublishEvent& event, const json& expected) {
    if (!event.hashed || event.exposures.size() != 1) return false;
    bool unit_matches = false;
    for (const auto& u : event.units) unit_matches |= u.type == "unit_serving" && u.uid == SERVING_UNIT_HASH;
    json exposure = event.exposures[0];
    return unit_matches && served_as(exposure, expected);
}

inline bool check_context_serving() {
    const auto data = ordered_json::parse(probe_payload()).get<absmartly::ContextData>();
    for (const auto& experiment : data.experiments) {
        if (experiment.name == "sb_declared_order" &&
            (!experiment.switchbackClusters.is_object() || experiment.switchbackClusters.begin().key() != "unit_user")) {
            std::cout << PREFIX << " FAILED: switchbackClusters lost its declared order: "
                      << experiment.switchbackClusters.dump() << std::endl;
            return false;
        }
    }

    // unit_user is set first, so the declared order, not the context's, picks the serving unit.
    absmartly::ContextConfig config;
    config.units["unit_user"] = "probe-user";
    config.units["unit_serving"] = "serving-9";
    auto log = std::make_shared<ExposureLog>();
    absmartly::Context context(config, data, log);
    context.set_attribute("city", "market_1");
    context.set_attribute("profile", {{"city", "market_1"}, {"zone", "market_2"}});
    context.set_attribute("store_number", 123456789);
    context.set_attribute("float_number", json::parse("3.0"));

    const bool serving =
        expect(context, *log, "agreement", "sb_agree", 3, {served(521, "sb_agree", "unit_serving", 3)}) &&
        expect(context, *log, "agreement repeat", "sb_agree", 3, {}) &&
        expect(context, *log, "declared order", "sb_declared_order", 3,
               {served(522, "sb_declared_order", "unit_user", 3)}) &&
        expect(context, *log, "disagreement", "sb_disagree", 0, {}) &&
        expect(context, *log, "whole number", "sb_whole_number", 6,
               {served(524, "sb_whole_number", "unit_serving", 6)}) &&
        expect(context, *log, "value filtered out", "sb_value_filtered", 0, {}) &&
        expect(context, *log, "empty allow-list admits all", "sb_allow_empty", 3,
               {served(528, "sb_allow_empty", "unit_serving", 3)}) &&
        expect(context, *log, "Phase 1E fields only", "sb_phase1e_only", 0, {}) &&
        expect(context, *log, "float value", "sb_float_value", 0, {});
    if (!serving) return false;

    context.publish();
    context.treatment("sb_unit_baseline");
    const auto baseline = context.publish();
    context.treatment("sb_payload");
    const auto payload = context.publish();
    if (!published_exactly(baseline, served(514, "sb_unit_baseline", "unit_serving", 1)) ||
        !published_exactly(payload, served(513, "sb_payload", "unit_serving", 1))) {
        std::cout << PREFIX << " check published exposures FAILED: baseline " << json(baseline).dump()
                  << ", switchback " << json(payload).dump() << std::endl;
        return false;
    }
    if (shape(json(baseline)) != shape(json(payload))) {
        std::cout << PREFIX << " check publish payload FAILED: " << json(payload).dump() << " differs in shape from "
                  << json(baseline).dump() << std::endl;
        return false;
    }

    if (context.treatment("sb_unit_with_fields") != 1) {
        std::cout << PREFIX << " check unit inertness FAILED: sb_unit_with_fields did not serve 1" << std::endl;
        return false;
    }
    const auto inert = context.publish();
    if (!published_exactly(inert, served(527, "sb_unit_with_fields", "unit_serving", 1))) {
        std::cout << PREFIX << " check unit inertness FAILED: " << json(inert).dump() << std::endl;
        return false;
    }
    json a = baseline;
    json b = inert;
    for (json* event : {&a, &b}) {
        event->erase("publishedAt");
        auto& exposure = (*event)["exposures"][0];
        exposure["id"] = 0;
        exposure["name"] = "";
        exposure["exposedAt"] = 0;
    }
    if (a != b) {
        std::cout << PREFIX << " check unit inertness FAILED: publish " << b.dump() << " differs from " << a.dump()
                  << std::endl;
        return false;
    }
    return true;
}

// The R33 wire fields reach the SDK's fetch unchanged through the payload store (scenario 413's
// explicit null among them): absent and null both admit every value, so serving alone cannot tell.
inline bool check_wire_round_trip() {
    auto payload = ordered_json::parse(probe_payload());
    payload["experiments"][0]["switchbackClusterValues"] = nullptr;
    payload["experiments"][0]["switchbackCarryoverDurationMs"] = 0;
    payload["experiments"][0]["switchbackBurnInDurationMs"] = 0;
    payload["experiments"][0]["switchbackBurnOutDurationMs"] = 0;
    const auto served = payload_store::store(payload).dump();
    if (served != payload.dump()) {
        std::cout << PREFIX << " check wire round trip FAILED: stored " << payload.dump() << ", served " << served
                  << std::endl;
        return false;
    }
    return true;
}

inline bool run_safely() {
    try {
        if (!check_decimal_parsing() || !check_golden_sample() || !check_wire_round_trip() ||
            !check_context_serving()) {
            return false;
        }
        std::cout << PREFIX << " behavioral self-test PASSED" << std::endl;
        return true;
    } catch (const std::exception& e) {
        std::cout << PREFIX << " behavioral self-test FAILED with exception: " << e.what() << std::endl;
        return false;
    } catch (...) {
        std::cout << PREFIX << " behavioral self-test FAILED with an unknown exception" << std::endl;
        return false;
    }
}

inline bool switchback() {
    static const bool result = run_safely();
    return result;
}

} // namespace switchback_self_test
