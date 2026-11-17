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

    private static final String UNIT_TYPE = "session_id";
    // seedHi=1/seedLo=1 over [0.5, 0.5] puts this unit in arm 1 (not held out).
    private static final String UID = "svk-unit-1";
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

    private static ContextData contextData(Field holdoutIdsField, Field holdoutsField, int[] holdoutIds)
            throws Exception {
        Class<?> holdoutType = holdoutsField.getType().getComponentType();
        ContextData data = new ContextData();
        data.experiments = new Experiment[] {buildExperiment(801, "dedup_covered", UNIT_TYPE, 0, 0,
            new double[] {0, 1}, 0, TWO_VARIANTS, holdoutIds, holdoutIdsField)};
        Object holdouts = Array.newInstance(holdoutType, 1);
        Array.set(holdouts, 0, buildHoldoutEntry(holdoutType, 901, "dedup_holdout", UNIT_TYPE, 1, 1,
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
            ContextConfig.create().setUnit(UNIT_TYPE, UID).setPublishDelay(-1).setRefreshInterval(0),
            contextData(holdoutIdsField, holdoutsField, new int[] {901, 901}));
        context.waitUntilReady();

        String prefix = "[holdoutIdsDedup probe]";
        try {
            if (!verifyTreatmentAndExposures(prefix, "duplicate ids", context, exposures, "dedup_covered", 1,
                    new ProbeExposure(801, "dedup_covered", 1),
                    new ProbeExposure(901, "dedup_holdout", 1))) return false;
            provider.setNextData(contextData(holdoutIdsField, holdoutsField, new int[] {901}));
            context.refresh();
            if (!verifyTreatmentAndExposures(prefix, "refresh dropping the duplicate", context, exposures,
                    "dedup_covered", 1)) return false;
        } finally {
            context.close();
        }
        System.out.println(prefix + " behavioral self-test PASSED");
        return true;
    }
}
