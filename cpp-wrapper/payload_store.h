// Scenario payloads as the wrapper stores them: decoded with the SDK's own ContextData from_json
// for contexts built in-process, and kept as received for the mock collector, so every wire field
// (an explicit null included) reaches the SDK's own fetch unchanged.
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

struct StoredPayload {
    absmartly::ContextData data;
    nlohmann::ordered_json wire;
};

inline StoredPayload store(const nlohmann::ordered_json& data) {
    StoredPayload stored;
    stored.data = parse_context_data(data);
    stored.wire = stored.data;
    if (data.is_object()) {
        stored.wire = data;
        if (!stored.wire.contains("experiments") || !stored.wire["experiments"].is_array()) {
            stored.wire["experiments"] = nlohmann::ordered_json::array();
        }
    }
    return stored;
}

}
