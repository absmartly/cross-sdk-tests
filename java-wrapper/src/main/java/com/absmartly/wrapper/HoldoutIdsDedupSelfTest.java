package com.absmartly.wrapper;

import com.absmartly.sdk.ABSmartly;
import com.absmartly.sdk.ABSmartlyConfig;
import com.absmartly.sdk.ContextConfig;
import com.absmartly.sdk.ContextEventLogger;
import com.absmartly.sdk.json.ContextData;
import com.absmartly.sdk.json.Experiment;
import com.absmartly.sdk.json.Exposure;

import java.lang.reflect.Array;
import java.lang.reflect.Field;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CopyOnWriteArrayList;

import static com.absmartly.wrapper.HoldoutProbeSupport.ProbeExposure;
import static com.absmartly.wrapper.HoldoutProbeSupport.buildExperiment;
import static com.absmartly.wrapper.HoldoutProbeSupport.buildHoldoutEntry;
import static com.absmartly.wrapper.HoldoutProbeSupport.findField;
import static com.absmartly.wrapper.HoldoutProbeSupport.verifyTreatmentAndExposures;

/**
 * Behavioral self-test for `holdoutIdsDedup` (mirrors the Holdout Conformance duplicate-ids
 * scenario): a holdoutIds list naming one holdout twice is one holdout. getTreatment fires one
 * holdout exposure, and a refresh that drops the duplicate is not a coverage change, so the covered
 * experiment keeps its exposure state and re-exposes nothing. Gated on {@link HoldoutSelfTest}.
 */
final class HoldoutIdsDedupSelfTest {
    private HoldoutIdsDedupSelfTest() {}

    // None of the holdouts holds its unit out: seedHi=1/seedLo=1 puts svk-unit-1 in arm 1 of
    // [0.5, 0.5], and seedLo 2 and 4 do the same for svk-user-1. The non-adjacent repeat is on its own
    // unit type so its holdouts' exposures are not already spent by the adjacent one.
    private static final String SESSION = "session_id";
    private static final String USER = "user_id";
    private static final String[] TWO_VARIANTS = {"A", "B"};

    private static volatile Boolean cachedResult;

    static boolean run() {
        Boolean result = cachedResult;
        if (result == null) {
            synchronized (HoldoutIdsDedupSelfTest.class) {
                result = cachedResult;
                if (result == null) {
                    result = runSafely();
                    cachedResult = result;
                }
            }
        }
        return result;
    }

    private static boolean runSafely() {
        try {
            if (!HoldoutSelfTest.run()) {
                System.out.println("[holdoutIdsDedup probe] skipped: base holdouts battery did not pass");
                return false;
            }
            return runBattery();
        } catch (Throwable t) {
            System.out.println("[holdoutIdsDedup probe] behavioral self-test FAILED with throwable: " + t);
            return false;
        }
    }

    private static ContextData contextData(Field holdoutIdsField, Field holdoutsField, int[] adjacent,
            int[] apart) throws Exception {
        Class<?> holdoutType = holdoutsField.getType().getComponentType();
        ContextData data = new ContextData();
        data.experiments = new Experiment[] {
            buildExperiment(801, "dedup_covered", SESSION, 0, 0, new double[] {0, 1}, 0, TWO_VARIANTS, adjacent,
                holdoutIdsField),
            buildExperiment(802, "dedup_apart", USER, 0, 0, new double[] {0, 1}, 0, TWO_VARIANTS, apart,
                holdoutIdsField)};
        Object holdouts = Array.newInstance(holdoutType, 3);
        Array.set(holdouts, 0, buildHoldoutEntry(holdoutType, 901, "dedup_holdout", SESSION, 1, 1,
            new double[] {0.5, 0.5}, "full", TWO_VARIANTS));
        Array.set(holdouts, 1, buildHoldoutEntry(holdoutType, 902, "dedup_apart_a", USER, 1, 2,
            new double[] {0.5, 0.5}, "full", TWO_VARIANTS));
        Array.set(holdouts, 2, buildHoldoutEntry(holdoutType, 903, "dedup_apart_b", USER, 1, 4,
            new double[] {0.5, 0.5}, "full", TWO_VARIANTS));
        holdoutsField.set(data, holdouts);
        return data;
    }

    private static boolean runBattery() throws Exception {
        Field holdoutIdsField = findField(Experiment.class, "holdoutIds");
        Field holdoutsField = findField(ContextData.class, "holdouts");

        List<ProbeExposure> exposures = new CopyOnWriteArrayList<>();
        ContextEventLogger logger = (context, type, data) -> {
            if (type == ContextEventLogger.EventType.Exposure && data instanceof Exposure) {
                Exposure exposure = (Exposure) data;
                exposures.add(new ProbeExposure(exposure.id, exposure.name, exposure.variant));
            }
        };
        DummyContextDataProvider provider = new DummyContextDataProvider();
        ABSmartly sdk = ABSmartly.create(ABSmartlyConfig.create()
            .setContextDataProvider(provider)
            .setContextEventHandler((context, event) -> CompletableFuture.completedFuture(null))
            .setContextEventLogger(logger));
        com.absmartly.sdk.Context context = sdk.createContextWith(
            ContextConfig.create().setUnit(SESSION, "svk-unit-1").setUnit(USER, "svk-user-1")
                .setPublishDelay(-1).setRefreshInterval(0),
            contextData(holdoutIdsField, holdoutsField, new int[] {901, 901}, new int[] {902, 903, 902, 903}));
        context.waitUntilReady();

        String prefix = "[holdoutIdsDedup probe]";
        try {
            if (!verifyTreatmentAndExposures(prefix, "adjacent duplicate", context, exposures, "dedup_covered", 1,
                    new ProbeExposure(801, "dedup_covered", 1),
                    new ProbeExposure(901, "dedup_holdout", 1))) return false;
            if (!verifyTreatmentAndExposures(prefix, "non-adjacent duplicate", context, exposures, "dedup_apart", 1,
                    new ProbeExposure(802, "dedup_apart", 1),
                    new ProbeExposure(902, "dedup_apart_a", 1),
                    new ProbeExposure(903, "dedup_apart_b", 1))) return false;
            provider.setNextData(contextData(holdoutIdsField, holdoutsField, new int[] {901}, new int[] {902, 903}));
            context.refresh();
            if (!verifyTreatmentAndExposures(prefix, "refresh dropping the adjacent duplicate", context, exposures,
                    "dedup_covered", 1)) return false;
            if (!verifyTreatmentAndExposures(prefix, "refresh dropping the non-adjacent duplicate", context,
                    exposures, "dedup_apart", 1)) return false;
        } finally {
            context.close();
        }
        System.out.println(prefix + " behavioral self-test PASSED");
        return true;
    }
}
