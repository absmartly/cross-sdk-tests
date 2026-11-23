// Build with the wrapper's SDK and JSON include/link settings.
#include "holdout_self_test.h"
#include "switchback_self_test.h"
#include <stdexcept>

int main() {
    using switchback_self_test::has_declared_cluster_order;
    using ordered_json = nlohmann::ordered_json;
    const auto require = [](bool condition) {
        if (!condition) throw std::runtime_error("probe regression failed");
    };
    require(!has_declared_cluster_order(ordered_json::object()));
    require(!has_declared_cluster_order(nullptr));
    require(!has_declared_cluster_order(ordered_json::array()));
    require(!has_declared_cluster_order(ordered_json{{"unit_user", "profile/city"}}));
    require(!has_declared_cluster_order(ordered_json{{"unit_serving", "city"}, {"unit_user", "profile/city"}}));
    require(has_declared_cluster_order(ordered_json{{"unit_user", "profile/city"}, {"unit_serving", "city"}}));
    require(holdout_self_test::run_holdouts_battery());
    require(switchback_self_test::run_safely());
}
