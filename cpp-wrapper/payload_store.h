// Scenario payloads: decoded with the SDK's own ContextData from_json for contexts built
// in-process, and stored as received for the mock collector.
#pragma once

#include <absmartly/models.h>
#include <nlohmann/json.hpp>

namespace payload_store {

// ordered_json keeps each switchbackClusters map in its declared order, which decides the
// serving unit; nlohmann::json would sort it.
inline absmartly::ContextData parse_context_data(const nlohmann::ordered_json& j) {
    if (!j.is_object()) {
        return {};
    }
    nlohmann::ordered_json payload = j;
    if (!payload.contains("experiments") || !payload["experiments"].is_array()) {
        payload["experiments"] = nlohmann::ordered_json::array();
    }
    return payload.get<absmartly::ContextData>();
}

// The payload the mock collector serves: the JSON as received, with a missing or non-array
// experiments list normalised to [], so every wire field (an explicit null included) reaches the
// SDK's own fetch unchanged.
inline nlohmann::ordered_json store(const nlohmann::ordered_json& data) {
    nlohmann::ordered_json stored = data.is_object() ? data : nlohmann::ordered_json::object();
    if (!stored.contains("experiments") || !stored["experiments"].is_array()) {
        stored["experiments"] = nlohmann::ordered_json::array();
    }
    return stored;
}

}
