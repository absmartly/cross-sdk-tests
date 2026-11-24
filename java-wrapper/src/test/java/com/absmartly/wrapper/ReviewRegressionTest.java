package com.absmartly.wrapper;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.absmartly.sdk.ABsmartlyClient;
import com.absmartly.sdk.Context;
import com.absmartly.sdk.ContextData;
import com.absmartly.sdk.ContextState;
import com.absmartly.sdk.event.EventListener;
import com.absmartly.sdk.internal.json.ExposurePayload;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.http.HttpStatus;
import org.springframework.test.util.ReflectionTestUtils;

class ReviewRegressionTest {
    private static final ObjectMapper MAPPER = new ObjectMapper();

    @ParameterizedTest
    @ValueSource(strings = {"creation", "not-ready", "setter", "context-close"})
    void servingProbeAlwaysClosesItsClient(String failure) throws Exception {
        ABsmartlyClient client = mock(ABsmartlyClient.class);
        Context context = mock(Context.class);
        when(context.state()).thenReturn("not-ready".equals(failure) ? ContextState.LOADING : ContextState.READY);
        if ("setter".equals(failure)) doThrow(new IllegalArgumentException("setter failed"))
            .when(context).setAttribute(anyString(), any());
        if ("context-close".equals(failure)) doThrow(new IllegalStateException("close failed")).when(context).close();
        try (var sdk = mockStatic(SdkSupport.class)) {
            sdk.when(SdkSupport::inMemoryClient).thenReturn(client);
            sdk.when(() -> SdkSupport.context(eq(client), any(ContextData.class), anyMap(), any(EventListener.class)))
                .thenAnswer(call -> {
                    if ("creation".equals(failure)) throw new IllegalStateException("creation failed");
                    return context;
                });
            if (List.of("creation", "setter", "context-close").contains(failure)) {
                assertThrows(RuntimeException.class, SwitchbackSelfTest::checkContextServing);
            } else {
                assertFalse(SwitchbackSelfTest.checkContextServing());
            }
            verify(client).close();
            if (!"creation".equals(failure)) verify(context).close();
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {"sb_unit_baseline", "sb_payload"})
    void payloadProbeRejectsWrongTreatmentBeforeAcceptingCorrectPublishedVariants(String experiment) throws Exception {
        Context context = mock(Context.class);
        List<ExposurePayload> exposures = new ArrayList<>();
        List<JsonNode> published = new ArrayList<>();
        when(context.treatment(anyString())).thenAnswer(call -> {
            String name = call.getArgument(0);
            int id = "sb_unit_baseline".equals(name) ? 514 : 513;
            exposures.add(new ExposurePayload(id, name, "unit_serving", 1, 1700000000000L,
                true, true, false, false, false, false));
            return name.equals(experiment) ? 0 : 1;
        });
        doAnswer(call -> {
            if (!exposures.isEmpty()) published.add(event(exposures.get(exposures.size() - 1)));
            return null;
        }).when(context).publish();
        assertNull(SwitchbackSelfTest.checkPayloadShape(context, exposures, published));
        verify(context).treatment(experiment);
    }

    private static ObjectNode event(ExposurePayload exposure) {
        ObjectNode event = MAPPER.createObjectNode();
        event.put("hashed", true);
        event.putArray("units").addObject().put("type", "unit_serving").put("uid", "r5uxZRoi-JuM5yYJa0kmSQ");
        event.putArray("exposures").add(MAPPER.valueToTree(exposure));
        return event;
    }

    @ParameterizedTest
    @ValueSource(strings = {"correct", "id", "name"})
    void inertPublishIsValidatedBeforeIdentityNormalization(String corruption) throws Exception {
        Context context = mock(Context.class);
        List<ExposurePayload> exposures = new ArrayList<>();
        List<JsonNode> published = new ArrayList<>();
        ExposurePayload inert = new ExposurePayload(527, "sb_unit_with_fields", "unit_serving", 1,
            1700000000010L, true, true, false, false, false, false);
        ObjectNode actual = event(inert);
        ObjectNode exposure = (ObjectNode) actual.path("exposures").get(0);
        if ("id".equals(corruption)) exposure.put("id", 514);
        if ("name".equals(corruption)) exposure.put("name", "sb_unit_baseline");
        when(context.treatment("sb_unit_with_fields")).thenAnswer(call -> { exposures.add(inert); return 1; });
        doAnswer(call -> { published.add(actual); return null; }).when(context).publish();
        ObjectNode baseline = event(new ExposurePayload(514, "sb_unit_baseline", "unit_serving", 1,
            1700000000000L, true, true, false, false, false, false));
        assertEquals("correct".equals(corruption), SwitchbackSelfTest.checkUnitExperimentInert(
            context, exposures, published, MAPPER, baseline));
        assertEquals(527, exposures.get(0).id); // Correct callback must not hide corrupted wire identity.
        assertEquals(514, baseline.path("exposures").get(0).path("id").asInt());
    }

    @ParameterizedTest
    @ValueSource(booleans = {false, true})
    void malformedInlineDataClosesClientAndTransportWithoutMaskingDecoderFailure(boolean closeFails) {
        ABsmartlyClient client = mock(ABsmartlyClient.class);
        ABsmartlyClient.Builder builder = mock(ABsmartlyClient.Builder.class, RETURNS_SELF);
        when(builder.build()).thenReturn(client);
        if (closeFails) doThrow(new IllegalStateException("cleanup failed")).when(client).close();
        Map<String, Object> data = Map.of("experiments", "not an experiment array");
        RuntimeException decode = assertThrows(RuntimeException.class,
            () -> ContextData.fromJson("{\"experiments\":\"not an experiment array\"}"));
        try (var clients = mockStatic(ABsmartlyClient.class);
             var transports = mockConstruction(WrapperTransport.class)) {
            clients.when(ABsmartlyClient::builder).thenReturn(builder);
            var response = new WrapperController().createContext(Map.of("data", data,
                "endpoint", "http://example.test", "publishToEndpoint", true,
                "client", Map.of("application", "app")));
            assertEquals(HttpStatus.INTERNAL_SERVER_ERROR, response.getStatusCode());
            assertEquals(decode.getMessage(), ((Map<?, ?>) response.getBody()).get("error"));
            assertEquals(1, transports.constructed().size());
            verify(client).close();
            verify(transports.constructed().get(0)).close();
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {"inline", "inline-cleanup-fails", "fetch", "fetch-cleanup-fails"})
    void contextCreationFailureClosesClientAndTransportWithoutMaskingTheFailure(String mode) {
        boolean inline = mode.startsWith("inline");
        ABsmartlyClient client = mock(ABsmartlyClient.class);
        ABsmartlyClient.Builder builder = mock(ABsmartlyClient.Builder.class, RETURNS_SELF);
        when(builder.build()).thenReturn(client);
        IllegalStateException creation = new IllegalStateException("context creation failed");
        if (inline) when(client.context(any(), any())).thenThrow(creation);
        else when(client.newContext(any())).thenThrow(creation);
        if (mode.endsWith("cleanup-fails")) doThrow(new IllegalStateException("cleanup failed")).when(client).close();
        Map<String, Object> request = inline
            ? Map.of("data", Map.of("experiments", List.of()))
            : Map.of("endpoint", "http://example.test");
        WrapperController controller = new WrapperController();
        try (var clients = mockStatic(ABsmartlyClient.class);
             var transports = mockConstruction(WrapperTransport.class)) {
            clients.when(ABsmartlyClient::builder).thenReturn(builder);
            var response = controller.createContext(request);
            assertEquals(HttpStatus.INTERNAL_SERVER_ERROR, response.getStatusCode());
            assertEquals(creation.getMessage(), ((Map<?, ?>) response.getBody()).get("error"));
            assertEquals(1, transports.constructed().size());
            verify(client).close();
            verify(transports.constructed().get(0)).close();
            assertTrue(((Map<?, ?>) ReflectionTestUtils.getField(controller, "contexts")).isEmpty());
        }
    }
}
