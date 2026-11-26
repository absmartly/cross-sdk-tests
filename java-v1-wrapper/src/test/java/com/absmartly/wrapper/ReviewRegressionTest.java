package com.absmartly.wrapper;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.absmartly.sdk.ABSmartly;
import com.absmartly.sdk.Client;
import com.absmartly.sdk.Context;
import com.absmartly.sdk.json.Experiment;
import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ScheduledExecutorService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.http.HttpStatus;
import org.springframework.test.util.ReflectionTestUtils;

class ReviewRegressionTest {
    @Test
    void holdoutFixtureSetsPrimitiveIteration() throws Exception {
        Experiment fixture = (Experiment) HoldoutProbeSupport.buildHoldoutEntry(Experiment.class, 418,
            "holdout", "user_id", 1, 222, new double[] {0.5, 0.5}, "holdout", new String[] {"A", "B"});
        assertEquals(1, fixture.iteration);
        assertEquals(418, fixture.id);
        assertEquals("holdout", fixture.name);
        HoldoutProbeSupport.setIfPresent(fixture, "iteration", "incompatible");
        HoldoutProbeSupport.setIfPresent(fixture, "absentOptionalField", 1);
        assertEquals(1, fixture.iteration);
        HoldoutProbeSupport.setIfPresent(fixture, "iteration", null);
        assertEquals(1, fixture.iteration);
    }

    @Test
    void lateUnitProbeRejectsExposuresFromSetter() {
        Context context = mock(Context.class);
        List<HoldoutProbeSupport.ProbeExposure> exposures = new ArrayList<>();
        when(context.peekTreatment("chk_g_late_unit")).thenReturn(2);
        doAnswer(call -> {
            exposures.add(new HoldoutProbeSupport.ProbeExposure(418, "chk_g_holdout", 1));
            return null;
        }).when(context).setUnit(anyString(), anyString());
        assertFalse(HoldoutSelfTest.checkLateUnitPublishesHoldoutExposure("test", context, exposures));
        verify(context, never()).getTreatment(anyString());
    }

    @Test
    void lateUnitProbeStillRequiresTheExactTreatmentExposureSequence() {
        Context context = mock(Context.class);
        List<HoldoutProbeSupport.ProbeExposure> exposures = new ArrayList<>();
        when(context.peekTreatment("chk_g_late_unit")).thenReturn(2);
        when(context.getTreatment("chk_g_late_unit")).thenAnswer(call -> {
            exposures.add(new HoldoutProbeSupport.ProbeExposure(318, "chk_g_late_unit", 2));
            exposures.add(new HoldoutProbeSupport.ProbeExposure(418, "chk_g_holdout", 1));
            return 2;
        });
        assertTrue(HoldoutSelfTest.checkLateUnitPublishesHoldoutExposure("test", context, exposures));
    }

    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = {" ", "\t\n"})
    void e2eRejectsEachMissingOrBlankRequiredSettingBeforeClientCreation(String invalid) {
        for (String key : List.of("ABSMARTLY_E2E_ENDPOINT", "ABSMARTLY_E2E_API_KEY",
                "ABSMARTLY_E2E_APPLICATION", "ABSMARTLY_E2E_ENVIRONMENT")) {
            Map<String, String> environment = new HashMap<>(Map.of("ABSMARTLY_E2E_ENDPOINT", "http://localhost",
                "ABSMARTLY_E2E_API_KEY", "key", "ABSMARTLY_E2E_APPLICATION", "app",
                "ABSMARTLY_E2E_ENVIRONMENT", "env"));
            environment.put(key, invalid);
            try (var clients = mockStatic(Client.class)) {
                var response = new WrapperController(SwitchbackFunctions.LINKED, environment::get)
                    .createContext(Map.of("mode", "e2e"));
                assertEquals(HttpStatus.NOT_IMPLEMENTED, response.getStatusCode(), key);
                assertEquals(Map.of("error", "e2e mode not configured"), response.getBody());
                clients.verifyNoInteractions();
            }
        }
    }

    @Test
    void e2ePreservesNonBlankRequiredSettingsVerbatim() {
        Client client = mock(Client.class);
        ABSmartly sdk = mock(ABSmartly.class);
        Context context = mock(Context.class);
        when(sdk.createContext(any())).thenReturn(context);
        Map<String, String> environment = Map.of("ABSMARTLY_E2E_ENDPOINT", "https://example.test/v1",
            "ABSMARTLY_E2E_API_KEY", " key ", "ABSMARTLY_E2E_APPLICATION", " app ",
            "ABSMARTLY_E2E_ENVIRONMENT", " env ");
        WrapperController controller = new WrapperController(SwitchbackFunctions.LINKED, environment::get);
        try (var clients = mockStatic(Client.class); var owners = mockStatic(ABSmartly.class)) {
            clients.when(() -> Client.create(any())).thenAnswer(call -> {
                com.absmartly.sdk.ClientConfig config = call.getArgument(0);
                assertEquals(environment.get("ABSMARTLY_E2E_ENDPOINT"), config.getEndpoint());
                assertEquals(" key ", config.getAPIKey());
                assertEquals(" app ", config.getApplication());
                assertEquals(" env ", config.getEnvironment());
                return client;
            });
            owners.when(() -> ABSmartly.create(any())).thenReturn(sdk);
            var response = controller.createContext(Map.of("mode", "e2e"));
            assertEquals(HttpStatus.OK, response.getStatusCode());
            String id = (String) ((Map<?, ?>) ((Map<?, ?>) response.getBody()).get("result")).get("contextId");
            controller.deleteContext(id);
            verify(sdk).close();
            verify(client).close();
        } catch (IOException e) {
            throw new AssertionError(e);
        }
    }

    @Test
    void closingContinuesThroughContextAndSdkFailures() throws Exception {
        Context context = mock(Context.class);
        ABSmartly sdk = mock(ABSmartly.class);
        Client client = mock(Client.class);
        doThrow(new IllegalStateException("context close failed")).when(context).close();
        doThrow(new IOException("sdk close failed")).when(sdk).close();
        ContextWrapper wrapper = new ContextWrapper(context, new EventCollector(), null);
        wrapper.setSdk(sdk);
        wrapper.setClient(client);
        assertDoesNotThrow(wrapper::closeClient);
        var order = inOrder(context, sdk, client);
        order.verify(context).close();
        order.verify(sdk).close();
        order.verify(client).close();
    }

    @ParameterizedTest
    @ValueSource(booleans = {false, true})
    void failedContextCreationReleasesItsOwnClientButNotRegisteredOnes(boolean inline) throws Exception {
        List<Client> created = new ArrayList<>();
        WrapperController controller = new WrapperController();
        Map<String, Object> source = inline ? Map.of("data", Map.of("experiments", List.of())) : Map.of();
        try (var clients = mockStatic(Client.class)) {
            clients.when(() -> Client.create(any())).thenAnswer(call -> {
                Client client = mock(Client.class);
                created.add(client);
                return client;
            });
            Map<String, Object> good = new HashMap<>(source);
            good.put("units", Map.of("user_id", "u1"));
            var registered = controller.createContext(good);
            assertEquals(HttpStatus.OK, registered.getStatusCode());

            Map<String, Object> blank = new HashMap<>(source);
            blank.put("units", Map.of("user_id", " "));
            var response = controller.createContext(blank);
            assertEquals(HttpStatus.INTERNAL_SERVER_ERROR, response.getStatusCode());
            assertEquals("Unit 'user_id' UID must not be blank.", ((Map<?, ?>) response.getBody()).get("error"));
            assertEquals(2, created.size());
            verify(created.get(1)).close();
            verify(created.get(0), never()).close();
            assertEquals(1, ((Map<?, ?>) ReflectionTestUtils.getField(controller, "contexts")).size());

            String id = (String) ((Map<?, ?>) ((Map<?, ?>) registered.getBody()).get("result")).get("contextId");
            controller.deleteContext(id);
            verify(created.get(0)).close();
        }
    }

    @ParameterizedTest
    @ValueSource(booleans = {false, true})
    void failedContextConstructionReleasesItsSdkAndClient(boolean inline) throws Exception {
        Client client = mock(Client.class);
        ABSmartly sdk = mock(ABSmartly.class);
        IllegalStateException creation = new IllegalStateException("context construction failed");
        if (inline) when(sdk.createContextWith(any(), any())).thenThrow(creation);
        else when(sdk.createContext(any())).thenThrow(creation);
        Map<String, Object> request = inline ? Map.of("data", Map.of("experiments", List.of())) : Map.of();
        WrapperController controller = new WrapperController();
        try (var clients = mockStatic(Client.class); var owners = mockStatic(ABSmartly.class)) {
            clients.when(() -> Client.create(any())).thenReturn(client);
            owners.when(() -> ABSmartly.create(any())).thenReturn(sdk);
            var response = controller.createContext(request);
            assertEquals(HttpStatus.INTERNAL_SERVER_ERROR, response.getStatusCode());
            assertEquals(creation.getMessage(), ((Map<?, ?>) response.getBody()).get("error"));
            // The SDK owns the scheduler: it was built before construction failed, so it must be released too.
            verify(sdk).close();
            verify(client).close();
            assertTrue(((Map<?, ?>) ReflectionTestUtils.getField(controller, "contexts")).isEmpty());
        }
    }

    @Test
    void deletingTimedContextShutsDownItsSdkOwnedSchedulerEvenAfterFinalize() {
        for (boolean finalizeFirst : List.of(false, true)) {
            WrapperController controller = new WrapperController();
            var response = controller.createContext(Map.of("data", Map.of("experiments", List.of()),
                "options", Map.of("publishDelay", 60000, "refreshPeriod", 60000)));
            assertEquals(HttpStatus.OK, response.getStatusCode());
            Map<?, ?> body = (Map<?, ?>) response.getBody();
            String id = (String) ((Map<?, ?>) body.get("result")).get("contextId");
            Map<?, ?> contexts = (Map<?, ?>) ReflectionTestUtils.getField(controller, "contexts");
            ContextWrapper wrapper = (ContextWrapper) contexts.get(id);
            ABSmartly sdk = (ABSmartly) ReflectionTestUtils.getField(wrapper, "sdk");
            ScheduledExecutorService scheduler = (ScheduledExecutorService) ReflectionTestUtils.getField(sdk, "scheduler_");
            assertFalse(scheduler.isShutdown());
            if (finalizeFirst) wrapper.getContext().close();
            assertEquals(HttpStatus.OK, controller.deleteContext(id).getStatusCode());
            assertTrue(wrapper.getContext().isClosed());
            assertTrue(scheduler.isShutdown());
            assertTrue(scheduler.isTerminated());
            assertFalse(contexts.containsKey(id));
        }
    }
}
