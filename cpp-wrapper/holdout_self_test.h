// Behavioral self-test for the `holdouts` / `holdout_arms` wrapper capabilities. Mirrors
// javascript-wrapper/holdoutSelfTest.js (and the java-wrapper HoldoutSelfTest /
// HoldoutArmsSelfTest): build ContextData fixtures ported from the scenario battery
// (test_scenarios_complete.json), construct a real absmartly::Context, call treatment() and
// require BOTH the returned variant and the exact ordered (id, name, variant) sequence of
// exposure events that call fires.
//
// The fixtures are decoded from JSON through the SDK's own from_json, so this compiles against
// an SDK without holdout support too: such an SDK ignores `holdouts`/`holdoutIds`, assigns the
// covered experiments normally and the checks fail, so the capability reports false.
//
// Run once per process and cached. Fails closed: any exception reports false.
#pragma once

#include <absmartly/context.h>
#include <absmartly/context_config.h>
#include <absmartly/context_event_logger.h>
#include <absmartly/models.h>
#include <nlohmann/json.hpp>

#include <iostream>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

namespace holdout_self_test {

using json = nlohmann::json;

struct Seen {
    int id;
    std::string name;
    int variant;
};

class ExposureLog : public absmartly::ContextEventLogger {
public:
    void handle_event(absmartly::Context&, const std::string& type, const json& data) override {
        if (type == "exposure") {
            seen.push_back({data.value("id", 0), data.value("name", std::string()), data.value("variant", -1)});
        }
    }
    std::vector<Seen> seen;
};

inline json experiment(int id, const std::string& name, const std::string& unit_type, int seed_hi, int seed_lo,
                       const std::vector<double>& split, int full_on_variant,
                       const std::vector<std::string>& variant_names, const json& holdout_ids) {
    json variants = json::array();
    for (const auto& variant_name : variant_names) {
        variants.push_back({{"name", variant_name}, {"config", nullptr}});
    }
    return {{"id", id}, {"name", name}, {"iteration", 1}, {"unitType", unit_type}, {"seedHi", seed_hi},
            {"seedLo", seed_lo}, {"split", split}, {"trafficSeedHi", 1}, {"trafficSeedLo", 2},
            {"trafficSplit", {0, 1}}, {"fullOnVariant", full_on_variant},
            {"applications", {{{"name", "website"}}}}, {"variants", variants}, {"audience", nullptr},
            {"audienceStrict", false}, {"customFieldValues", nullptr}, {"holdoutIds", holdout_ids}};
}

inline json holdout(int id, const std::string& name, const std::string& unit_type, int seed_hi, int seed_lo,
                    const std::vector<double>& split, const std::string& holdout_type) {
    json variants = json::array();
    for (size_t i = 0; i < split.size(); ++i) {
        variants.push_back({{"name", std::string(1, static_cast<char>('A' + i))}, {"config", nullptr}});
    }
    return {{"id", id}, {"name", name}, {"iteration", 1}, {"unitType", unit_type}, {"seedHi", seed_hi},
            {"seedLo", seed_lo}, {"split", split}, {"trafficSeedHi", 0}, {"trafficSeedLo", 0},
            {"trafficSplit", {0, 1}}, {"fullOnVariant", 0}, {"applications", json::array()},
            {"variants", variants}, {"audience", nullptr}, {"audienceStrict", false},
            {"customFieldValues", nullptr}, {"holdoutType", holdout_type}};
}

inline std::string describe(const std::vector<Seen>& seen) {
    json out = json::array();
    for (const auto& s : seen) out.push_back({{"id", s.id}, {"name", s.name}, {"variant", s.variant}});
    return out.dump();
}

// Calls treatment() once and requires the variant and the exposures that call fired.
inline bool check(const std::string& prefix, const std::string& label, absmartly::Context& context, ExposureLog& log,
                  const std::string& experiment_name, int expected_treatment, const std::vector<Seen>& expected) {
    const size_t before = log.seen.size();
    const int treatment = context.treatment(experiment_name);
    const std::vector<Seen> fired(log.seen.begin() + static_cast<long>(before), log.seen.end());

    bool ok = treatment == expected_treatment && fired.size() == expected.size();
    for (size_t i = 0; ok && i < expected.size(); ++i) {
        ok = fired[i].id == expected[i].id && fired[i].name == expected[i].name &&
             fired[i].variant == expected[i].variant;
    }
    if (!ok) {
        std::cout << prefix << " check " << label << " FAILED: expected treatment=" << expected_treatment
                  << " exposures=" << describe(expected) << ", got treatment=" << treatment
                  << " exposures=" << describe(fired) << std::endl;
    }
    return ok;
}

// Two-arm battery: checks A-G mirror scenarios 203-208 and 221 with synthetic ids (301-308,
// 401-408) so they share one context; seeds, splits, unit ids and full-on variants are the
// scenarios' own.
inline bool run_holdouts_battery() {
    const std::string held_out_type = "session_id";
    const std::string held_out_uid = "e791e240fcd3df7d238cfc285f475e8152fcc0ec";
    const std::string not_held_out_type = "session_id_b";
    const std::string not_held_out_uid = "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3";
    const std::string late_type = "user_id";

    const json data = {
        {"experiments",
         {experiment(301, "probe_a_covered", held_out_type, 100, 200, {0.5, 0.5}, 0, {"A", "B"}, {401}),
          experiment(302, "probe_b_covered", not_held_out_type, 100, 200, {0.5, 0.5}, 0, {"A", "B"}, {402}),
          experiment(304, "probe_c_union", held_out_type, 100, 200, {0.5, 0.5}, 0, {"A", "B"}, {404, 405}),
          experiment(305, "probe_d_covered", held_out_type, 100, 200, {0.5, 0.5}, 0, {"A", "B"}, {406}),
          experiment(306, "probe_d_uncovered_sibling", held_out_type, 100, 200, {0.5, 0.5}, 0, {"A", "B"}, nullptr),
          experiment(307, "probe_e_dangling_plus_valid", held_out_type, 100, 200, {0.5, 0.5}, 0, {"A", "B"},
                     {999, 407}),
          experiment(303, "probe_f_fullon", held_out_type, 100, 200, {0.5, 0.5}, 2, {"A", "B", "C"}, {403}),
          experiment(308, "probe_g_late_unit", late_type, 100, 200, {0.5, 0.5, 0.0}, 2, {"A", "B", "C"}, {408})}},
        {"holdouts",
         {holdout(401, "probe_a_holdout", held_out_type, 13, 111, {0.1, 0.9}, "full"),
          holdout(402, "probe_b_holdout", not_held_out_type, 13, 111, {0.1, 0.9}, "full"),
          holdout(404, "probe_c_holdout_low", held_out_type, 1, 222, {0.1, 0.9}, "full"),
          holdout(405, "probe_c_holdout_high", held_out_type, 13, 111, {0.1, 0.9}, "full"),
          holdout(406, "probe_d_holdout", held_out_type, 13, 111, {0.1, 0.9}, "full"),
          holdout(407, "probe_e_holdout", held_out_type, 13, 111, {0.1, 0.9}, "full"),
          holdout(403, "probe_f_holdout", held_out_type, 13, 111, {0.1, 0.9}, "full"),
          holdout(408, "probe_g_holdout", late_type, 1, 222, {0.1, 0.9}, "full")}}};

    absmartly::ContextConfig config;
    // late_type is deliberately absent; check G sets it.
    config.units = {{held_out_type, held_out_uid}, {not_held_out_type, not_held_out_uid}};
    auto log = std::make_shared<ExposureLog>();
    absmartly::Context context(config, data.get<absmartly::ContextData>(), log);

    const std::string p = "[holdouts probe]";
    if (!check(p, "A (mirrors 203)", context, *log, "probe_a_covered", 0, {{401, "probe_a_holdout", 0}})) return false;
    if (!check(p, "B (mirrors 204)", context, *log, "probe_b_covered", 0,
               {{302, "probe_b_covered", 0}, {402, "probe_b_holdout", 1}}))
        return false;
    if (!check(p, "C (mirrors 205)", context, *log, "probe_c_union", 0,
               {{404, "probe_c_holdout_low", 1}, {405, "probe_c_holdout_high", 0}}))
        return false;
    if (!check(p, "D (mirrors 206, covered)", context, *log, "probe_d_covered", 0, {{406, "probe_d_holdout", 0}}))
        return false;
    if (!check(p, "D (mirrors 206, uncovered sibling)", context, *log, "probe_d_uncovered_sibling", 1,
               {{306, "probe_d_uncovered_sibling", 1}}))
        return false;
    if (!check(p, "E (mirrors 207)", context, *log, "probe_e_dangling_plus_valid", 0, {{407, "probe_e_holdout", 0}}))
        return false;
    if (!check(p, "F (mirrors 208)", context, *log, "probe_f_fullon", 0, {{403, "probe_f_holdout", 0}})) return false;

    // G (mirrors 221): peek before the unit exists exposes nothing; once set, treatment publishes
    // the experiment and the holdout exactly once each.
    const size_t before = log->seen.size();
    const int peeked = context.peek("probe_g_late_unit");
    if (peeked != 2 || log->seen.size() != before) {
        std::cout << p << " check G (mirrors 221, pre-unit peek) FAILED: treatment=" << peeked << std::endl;
        return false;
    }
    const size_t before_unit = log->seen.size();
    context.set_unit(late_type, held_out_uid);
    if (log->seen.size() != before_unit) {
        std::cout << p << " check G (mirrors 221, set-unit) FAILED: setter emitted exposures" << std::endl;
        return false;
    }
    if (!check(p, "G (mirrors 221, post-unit treatment)", context, *log, "probe_g_late_unit", 2,
               {{308, "probe_g_late_unit", 2}, {408, "probe_g_holdout", 1}}))
        return false;

    std::cout << p << " behavioral self-test PASSED: all 7 checks mirroring scenarios 203-208 and 221 passed"
              << std::endl;
    return true;
}

// Three-arm battery: checks 0-2 mirror scenarios 214-216 with their own ids.
inline bool run_holdout_arms_battery() {
    const std::string unit_type = "session_id";
    const std::vector<double> covered = {0.5, 0.5};
    const std::vector<double> three_arm = {0.3, 0.3, 0.4};

    const json data = {
        {"experiments",
         {experiment(601, "arms_0_non_fullon", unit_type, 100, 200, covered, 0, {"A", "B"}, {621}),
          experiment(602, "arms_0_fullon", unit_type, 100, 200, covered, 2, {"A", "B", "C"}, {621}),
          experiment(603, "arms_1_non_fullon", unit_type, 100, 200, covered, 0, {"A", "B"}, {622}),
          experiment(604, "arms_1_fullon", unit_type, 100, 200, covered, 2, {"A", "B", "C"}, {622}),
          experiment(605, "arms_2_non_fullon", unit_type, 100, 200, covered, 0, {"A", "B"}, {623}),
          experiment(606, "arms_2_fullon", unit_type, 100, 200, covered, 2, {"A", "B", "C"}, {623})}},
        {"holdouts",
         {holdout(621, "arms_holdout_0", unit_type, 0, 1, three_arm, "all_full_on"),
          holdout(622, "arms_holdout_1", unit_type, 0, 3, three_arm, "all_full_on"),
          holdout(623, "arms_holdout_2", unit_type, 0, 0, three_arm, "all_full_on")}}};

    absmartly::ContextConfig config;
    config.units = {{unit_type, "e791e240fcd3df7d238cfc285f475e8152fcc0ec"}};
    auto log = std::make_shared<ExposureLog>();
    absmartly::Context context(config, data.get<absmartly::ContextData>(), log);

    const std::string p = "[holdout_arms probe]";
    if (!check(p, "0 non-full-on (mirrors 214)", context, *log, "arms_0_non_fullon", 0, {{621, "arms_holdout_0", 0}}))
        return false;
    if (!check(p, "0 full-on (mirrors 214)", context, *log, "arms_0_fullon", 0, {})) return false;
    if (!check(p, "1 non-full-on (mirrors 215)", context, *log, "arms_1_non_fullon", 0, {{622, "arms_holdout_1", 1}}))
        return false;
    if (!check(p, "1 full-on (mirrors 215)", context, *log, "arms_1_fullon", 2, {{604, "arms_1_fullon", 2}}))
        return false;
    if (!check(p, "2 non-full-on (mirrors 216)", context, *log, "arms_2_non_fullon", 1,
               {{605, "arms_2_non_fullon", 1}, {623, "arms_holdout_2", 2}}))
        return false;
    if (!check(p, "2 full-on (mirrors 216)", context, *log, "arms_2_fullon", 2, {{606, "arms_2_fullon", 2}}))
        return false;

    std::cout << p << " behavioral self-test PASSED: all 3 arm checks mirroring scenarios 214-216 passed" << std::endl;
    return true;
}

template <typename Battery>
bool run_safely(const char* prefix, Battery battery) {
    try {
        return battery();
    } catch (const std::exception& e) {
        std::cout << prefix << " behavioral self-test FAILED with error: " << e.what() << std::endl;
    } catch (...) {
        std::cout << prefix << " behavioral self-test FAILED with an unknown error" << std::endl;
    }
    return false;
}

inline bool holdouts() {
    static std::once_flag once;
    static bool result = false;
    std::call_once(once, [] { result = run_safely("[holdouts probe]", run_holdouts_battery); });
    return result;
}

// Three-arm support cannot exist without the two-arm battery passing.
inline bool holdout_arms() {
    static std::once_flag once;
    static bool result = false;
    std::call_once(once, [] {
        if (!holdouts()) {
            std::cout << "[holdout_arms probe] skipped: base holdouts battery did not pass" << std::endl;
            return;
        }
        result = run_safely("[holdout_arms probe]", run_holdout_arms_battery);
    });
    return result;
}

} // namespace holdout_self_test
