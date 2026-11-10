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

import com.absmartly.sdk.DefaultContextDataDeserializer;
import com.absmartly.sdk.json.ContextData;
import com.absmartly.sdk.json.Experiment;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

// The R33 wire fields must pass through the wrapper verbatim: the payload a scenario stores is
// deserialized into the SDK's ContextData and served back to the SDK's own fetch, so the ordered
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
        ResponseEntity<?> served = controller.mockApiContext("p");
        String json = MAPPER.writeValueAsString(served.getBody());
        JsonNode experiment = MAPPER.readTree(json).get("experiments").get(0);
        assertEquals(ORDER, keys(experiment.get("switchbackClusters")));
        assertEquals("[\"x\",\"y\"]", experiment.get("switchbackClusterValues").toString());

        // And the SDK's own model after its Client's deserializer parses the served bytes.
        byte[] bytes = json.getBytes(StandardCharsets.UTF_8);
        ContextData parsed = new DefaultContextDataDeserializer().deserialize(bytes, 0, bytes.length);
        Field clusters = Experiment.class.getField("switchbackClusters");
        assertEquals(ORDER, new ArrayList<>(((Map<String, String>) clusters.get(parsed.experiments[0])).keySet()));
    }

    @Test
    void legacyPhase1eFieldsAreIgnored() throws Exception {
        assumeTrue(SwitchbackSelfTest.hasR33WireModel(Experiment.class), "linked core-api has no R33 wire model");
        // Behaviour, not class structure: the legacy fields parse without error and do not survive.
        ContextData parsed = MAPPER.readValue(payload(), ContextData.class);
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

    // The linked SDK (java-sdk feat/switchback-phase1b in CI; head at time of writing: 91b9b31416)
    // must pass the whole probe: golden sample, R33 context serving, payload shape and
    // unit-experiment inertness.
    @Test
    void linkedSdkPassesTheSelfTest() {
        assumeTrue(SwitchbackSelfTest.hasR33WireModel(Experiment.class), "linked core-api has no R33 wire model");
        assertTrue(SwitchbackSelfTest.run());
    }
}
