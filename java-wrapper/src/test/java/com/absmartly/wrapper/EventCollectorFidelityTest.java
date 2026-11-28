package com.absmartly.wrapper;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.absmartly.sdk.Context;
import com.absmartly.sdk.ContextData;
import com.absmartly.sdk.Exposure;
import com.absmartly.sdk.event.ExposureEvent;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;

class EventCollectorFidelityTest {
    private static final ObjectMapper MAPPER = new ObjectMapper();

    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = {"user_id", " "})
    void preservesRawUnitThroughEventCollectionAndJsonSerialization(String rawUnit) throws Exception {
        // A null stand-in also checks fidelity for a future SDK that reports null instead of empty.
        Exposure raw = mock(Exposure.class);
        when(raw.unit()).thenReturn(rawUnit);
        EventCollector collector = new EventCollector();
        collector.onEvent(new ExposureEvent(null, 1700000000000L, raw));
        Map<?, ?> data = (Map<?, ?>) collector.getEvents().get(0).get("data");
        assertTrue(data.containsKey("unit"));
        assertEquals(raw.unit(), data.get("unit"));
        assertEquals(raw.unit(), SwitchbackSelfTest.payload(raw).unit);
        JsonNode wire = MAPPER.readTree(MAPPER.writeValueAsString(collector.getEvents())).get(0).get("data");
        assertTrue(wire.has("unit"));
        if (rawUnit == null) assertTrue(wire.get("unit").isNull());
        else {
            assertTrue(wire.get("unit").isTextual());
            assertEquals(rawUnit, wire.get("unit").textValue());
        }
    }

    @Test
    void actualNotRunningExposureReachesWrapperWithoutUnitRewriting() throws Exception {
        EventCollector collector = new EventCollector();
        CopyOnWriteArrayList<Exposure> raw = new CopyOnWriteArrayList<>();
        try (var client = SdkSupport.inMemoryClient()) {
            Context context = SdkSupport.context(client, ContextData.fromJson("{\"experiments\":[]}"),
                Map.of("user_id", "probe"), event -> {
                    if (event instanceof ExposureEvent exposure) raw.add(exposure.exposure());
                    collector.onEvent(event);
                });
            try {
                assertEquals(0, context.treatment("not_running"));
                collector.awaitEvent(0, 2000, "exposure");
                assertEquals(1, raw.size());
                // Whatever the linked SDK reports (2.x currently "", possibly null later) must reach the wire unchanged.
                Map<?, ?> observed = collector.getEvents().stream()
                    .filter(event -> "exposure".equals(event.get("type")))
                    .map(event -> (Map<?, ?>) event.get("data")).findFirst().orElseThrow();
                assertTrue(observed.containsKey("unit"));
                assertEquals(raw.get(0).unit(), observed.get("unit"));
                JsonNode wire = MAPPER.valueToTree(observed);
                assertTrue(wire.has("unit"));
                assertEquals(MAPPER.valueToTree(raw.get(0).unit()), wire.get("unit"));
            } finally {
                context.close();
            }
        }
    }
}
