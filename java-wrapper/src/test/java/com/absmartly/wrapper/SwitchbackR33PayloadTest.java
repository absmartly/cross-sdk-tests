package com.absmartly.wrapper;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

import com.absmartly.sdk.internal.Json;
import com.absmartly.sdk.internal.json.ContextPayload;
import com.absmartly.sdk.internal.json.Experiment;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

// The R33 wire fields must pass through the wrapper verbatim: the payload a scenario stores is
// deserialized into the SDK's context payload and served back to the SDK's own fetch, so the ordered
// switchbackClusters map must keep its key order across serialize -> parse.
class SwitchbackR33PayloadTest {
    private static final ObjectMapper MAPPER = new ObjectMapper();

    // Key order deliberately not alphabetical and not insertion-sorted by any hash.
    private static final List<String> ORDER = Arrays.asList("user_id", "session_id", "device_id", "account_id");

    private static String payload() {
        StringBuilder clusters = new StringBuilder();
        for (String key : ORDER) {
            clusters.append(clusters.length() == 0 ? "" : ",").append('"').append(key).append("\":\"")
                .append(key, 0, key.indexOf('_')).append("/city\"");
        }
        return "{\"experiments\":[{\"id\":1,\"name\":\"sb\",\"iteration\":1,\"unitType\":\"user_id\","
            + "\"seedHi\":0,\"seedLo\":1,\"split\":[0.5,0.5],\"trafficSeedHi\":1,\"trafficSeedLo\":2,"
            + "\"trafficSplit\":[0,1],\"fullOnVariant\":0,\"applications\":[{\"name\":\"website\"}],"
            + "\"variants\":[{\"name\":\"A\",\"config\":null},{\"name\":\"B\",\"config\":null}],"
            + "\"audienceStrict\":false,\"randomizationType\":\"switchback\","
            + "\"switchbackClusters\":{" + clusters + "},\"switchbackClusterValues\":[\"x\",\"y\"],"
            + "\"switchbackStartAt\":1704067200000,\"switchbackPeriodMs\":1440000000000,"
            + "\"switchbackClusterUnitType\":\"session_id\",\"switchbackClusterCount\":10,"
            + "\"switchbackClusterAttribute\":\"city\"}]}";
    }

    private static List<String> keys(JsonNode node) {
        List<String> keys = new ArrayList<>();
        node.fieldNames().forEachRemaining(keys::add);
        return keys;
    }

    @SuppressWarnings("unchecked")
    @Test
    void orderedClustersSurviveTheWrapperRoundTrip() throws Exception {
        assumeTrue(SwitchbackSelfTest.hasR33WireModel(Experiment.class), "linked core-api has no R33 wire model");
        WrapperController controller = new WrapperController();
        Map<String, Object> request = Map.of("data", MAPPER.readValue(payload(), Map.class));
        assertEquals(HttpStatus.OK, controller.storePayload("p", request).getStatusCode());

        // What the SDK's fetch receives back from /context_payload/{id}/context.
        ResponseEntity<?> served = controller.mockApiContext("p", Map.of());
        // Served verbatim: the stored JSON text, so key order is exactly what the scenario sent.
        String json = (String) served.getBody();
        JsonNode experiment = MAPPER.readTree(json).get("experiments").get(0);
        assertEquals(ORDER, keys(experiment.get("switchbackClusters")));
        assertEquals("[\"x\",\"y\"]", experiment.get("switchbackClusterValues").toString());

        // And the SDK's own model after its Client's deserializer parses the served bytes.
        byte[] bytes = json.getBytes(StandardCharsets.UTF_8);
        ContextPayload parsed = Json.decodeContext(bytes);
        Field clusters = Experiment.class.getField("switchbackClusters");
        assertEquals(ORDER, new ArrayList<>(((Map<String, String>) clusters.get(parsed.experiments[0])).keySet()));
    }

    @Test
    void legacyPhase1eFieldsAreIgnored() throws Exception {
        assumeTrue(SwitchbackSelfTest.hasR33WireModel(Experiment.class), "linked core-api has no R33 wire model");
        // Behaviour, not class structure: the SDK's own deserializer (what its Client uses) parses
        // the legacy fields without error and they do not survive.
        byte[] bytes = payload().getBytes(StandardCharsets.UTF_8);
        ContextPayload parsed = Json.decodeContext(bytes);
        assertEquals(1, parsed.experiments.length);
        JsonNode reserialized = MAPPER.valueToTree(parsed).get("experiments").get(0);
        assertFalse(reserialized.has("switchbackClusterCount"));
        assertFalse(reserialized.has("switchbackClusterUnitType"));
        assertFalse(reserialized.has("switchbackClusterAttribute"));
    }

    // Stand-ins for core-api Experiment shapes: the capability needs the R33 fields and no bucket field.
    static class R33Experiment {
        public String randomizationType;
        public Map<String, String> switchbackClusters;
        public String[] switchbackClusterValues;
        public Long switchbackStartAt;
        public Long switchbackPeriodMs;
    }

    static class Phase1eExperiment {
        public String randomizationType;
        public String switchbackClusterUnitType;
        public Integer switchbackClusterCount;
        public String switchbackClusterAttribute;
        public Long switchbackStartAt;
        public Long switchbackPeriodMs;
    }

    static class BothExperiment extends R33Experiment {
        public Integer switchbackClusterCount;
    }

    static class NoValuesExperiment {
        public String randomizationType;
        public Map<String, String> switchbackClusters;
        public Long switchbackStartAt;
        public Long switchbackPeriodMs;
    }

    static class UnitOnlyExperiment {
        public String unitType;
    }

    @Test
    void capabilityIsWithheldUnlessTheSdkHasTheR33WireModel() {
        assertTrue(SwitchbackSelfTest.hasR33WireModel(R33Experiment.class));
        assertFalse(SwitchbackSelfTest.hasR33WireModel(Phase1eExperiment.class));
        assertFalse(SwitchbackSelfTest.hasR33WireModel(BothExperiment.class));
        assertFalse(SwitchbackSelfTest.hasR33WireModel(NoValuesExperiment.class));
        assertFalse(SwitchbackSelfTest.hasR33WireModel(UnitOnlyExperiment.class));
    }

    @Test
    void probePayloadDeclaresItsClustersInOrder() throws Exception {
        JsonNode experiments = MAPPER.readTree(SwitchbackSelfTest.probePayload()).get("experiments");
        for (JsonNode experiment : experiments) {
            if ("sb_declared_order".equals(experiment.get("name").asText())) {
                assertEquals(Arrays.asList("unit_user", "unit_serving"), keys(experiment.get("switchbackClusters")));
                return;
            }
        }
        throw new AssertionError("probe payload has no sb_declared_order");
    }

    // The linked SDK (java-sdk feat/switchback-phase1b in CI; head at time of writing: d5b1a6e5;
    // requires >= d5b1a6e5) must pass the whole probe: golden sample, R33 context serving, payload
    // shape and unit-experiment inertness.
    @Test
    void linkedSdkPassesTheSelfTest() {
        assumeTrue(SwitchbackSelfTest.hasR33WireModel(Experiment.class), "linked core-api has no R33 wire model");
        assertTrue(SwitchbackSelfTest.run());
    }

    @Test
    void unitInertnessFixtureCarriesEveryR33Field() throws Exception {
        JsonNode experiments = MAPPER.readTree(SwitchbackSelfTest.probePayload()).get("experiments");
        for (JsonNode experiment : experiments) {
            if ("sb_unit_with_fields".equals(experiment.path("name").asText())) {
                assertEquals("unit", experiment.path("randomizationType").asText());
                assertTrue(experiment.has("switchbackClusters"));
                assertTrue(experiment.has("switchbackClusterValues"));
                assertTrue(experiment.has("switchbackStartAt"));
                assertTrue(experiment.has("switchbackPeriodMs"));
                assertEquals("balanced_block", experiment.path("switchbackDesignType").asText());
                assertEquals("global_average_treatment_effect", experiment.path("switchbackEstimand").asText());
                return;
            }
        }
        throw new AssertionError("probe payload has no inert unit experiment");
    }

    @Test
    void missingFirstSourceFixtureDistinguishesServingUnitFromLegacyUnitType() throws Exception {
        JsonNode experiments = MAPPER.readTree(SwitchbackSelfTest.probePayload()).get("experiments");
        for (JsonNode experiment : experiments) {
            if ("sb_missing_first".equals(experiment.path("name").asText())) {
                assertEquals(Arrays.asList("unit_user", "unit_serving"), keys(experiment.get("switchbackClusters")));
                assertEquals("missing_city", experiment.path("switchbackClusters").path("unit_user").asText());
                assertEquals("city", experiment.path("switchbackClusters").path("unit_serving").asText());
                assertEquals("unit_user", experiment.path("unitType").asText());
                return;
            }
        }
        throw new AssertionError("probe payload has no missing-first-source experiment");
    }
}
