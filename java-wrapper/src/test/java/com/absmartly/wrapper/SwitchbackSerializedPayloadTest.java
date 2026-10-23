package com.absmartly.wrapper;

import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.absmartly.sdk.json.Exposure;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

// The self-test must reject serialized publish bytes whose values differ from what was served,
// even when the captured PublishEvent object and the serialized key structure are both correct.
class SwitchbackSerializedPayloadTest {
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final Exposure SERVED = new Exposure(513, "sb_payload", "unit_serving", 1, 0, true, true, false,
        false, false, false);

    private static ObjectNode correctEvent() throws Exception {
        return (ObjectNode) MAPPER.readTree("{\"hashed\":true,"
            + "\"units\":[{\"type\":\"unit_serving\",\"uid\":\"r5uxZRoi-JuM5yYJa0kmSQ\"}],"
            + "\"exposures\":[{\"id\":513,\"name\":\"sb_payload\",\"unit\":\"unit_serving\",\"variant\":1,"
            + "\"exposedAt\":1700000000000,\"assigned\":true,\"eligible\":true,\"overridden\":false,"
            + "\"fullOn\":false,\"custom\":false,\"audienceMismatch\":false}]}");
    }

    private static ObjectNode exposure(ObjectNode event) {
        return (ObjectNode) event.get("exposures").get(0);
    }

    @Test
    void acceptsBytesCarryingTheServedValues() throws Exception {
        assertNull(SwitchbackSelfTest.serializedMismatch(correctEvent(), SERVED, MAPPER));
    }

    @ParameterizedTest
    @ValueSource(strings = {"variant", "assigned", "eligible", "overridden", "fullOn", "custom", "audienceMismatch",
        "id", "name", "unit"})
    void rejectsACorruptedServedValue(String field) throws Exception {
        ObjectNode event = correctEvent();
        switch (field) {
            case "variant":
                exposure(event).put("variant", 0);
                break;
            case "id":
                exposure(event).put("id", 514);
                break;
            case "name":
            case "unit":
                exposure(event).put(field, "wrong");
                break;
            default:
                exposure(event).put(field, !exposure(event).get(field).asBoolean());
        }
        String mismatch = SwitchbackSelfTest.serializedMismatch(event, SERVED, MAPPER);
        assertNotNull(mismatch);
        assertTrue(mismatch.contains(field), mismatch);
    }

    @Test
    void rejectsAMissingOrNonIntegralExposedAt() throws Exception {
        ObjectNode event = correctEvent();
        exposure(event).put("exposedAt", "1700000000000");
        assertNotNull(SwitchbackSelfTest.serializedMismatch(event, SERVED, MAPPER));
        exposure(event).remove("exposedAt");
        assertNotNull(SwitchbackSelfTest.serializedMismatch(event, SERVED, MAPPER));
    }

    @Test
    void rejectsUnhashedOrMissingServingUnit() throws Exception {
        ObjectNode unhashed = correctEvent();
        unhashed.put("hashed", false);
        assertNotNull(SwitchbackSelfTest.serializedMismatch(unhashed, SERVED, MAPPER));

        ObjectNode wrongUnit = correctEvent();
        ((ObjectNode) wrongUnit.get("units").get(0)).put("uid", "raw-uid");
        assertNotNull(SwitchbackSelfTest.serializedMismatch(wrongUnit, SERVED, MAPPER));
    }

    @Test
    void rejectsADroppedOrDuplicatedExposure() throws Exception {
        ObjectNode dropped = correctEvent();
        dropped.putArray("exposures");
        assertNotNull(SwitchbackSelfTest.serializedMismatch(dropped, SERVED, MAPPER));

        ObjectNode duplicated = correctEvent();
        duplicated.withArray("exposures").add(exposure(correctEvent()));
        assertNotNull(SwitchbackSelfTest.serializedMismatch(duplicated, SERVED, MAPPER));
    }
}
