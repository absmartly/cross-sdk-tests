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
import java.util.concurrent.CopyOnWriteArrayList;

import static com.absmartly.wrapper.HoldoutProbeSupport.ProbeExposure;
import static com.absmartly.wrapper.HoldoutProbeSupport.buildExperiment;
import static com.absmartly.wrapper.HoldoutProbeSupport.buildHoldoutEntry;
import static com.absmartly.wrapper.HoldoutProbeSupport.findField;
import static com.absmartly.wrapper.HoldoutProbeSupport.verifyTreatmentAndExposures;

/**
 * Behavioral self-test for three-arm holdout semantics (scenarios 214-216: a 3-arm holdout's
 * variant 0 holds out every covered experiment, variant 1 holds out only non-full-on covered
 * experiments while a full-on covered experiment takes its normal path, and variant 2 defers to
 * the normal path for every covered experiment). Gated structurally on {@link HoldoutSelfTest}:
 * three-arm support cannot exist without the two-arm foundation it verifies, so this probe
 * short-circuits to false without even attempting its own fixture whenever the base battery
 * fails - a build that fails HoldoutSelfTest can never report holdout_arms true, by construction
 * rather than by two independent checks that happen to agree.
 */
final class HoldoutArmsSelfTest {
    private HoldoutArmsSelfTest() {}

    // Same unit id used by scenarios 214-220/222; relative to the seedHi/seedLo pairs below it
    // deterministically lands in each holdout's variant 0, 1, and 2 respectively.
    private static final String UNIT_TYPE = "session_id";
    private static final String UID = "e791e240fcd3df7d238cfc285f475e8152fcc0ec";

    private static final double[] COVERED_SPLIT = {0.5, 0.5};
    private static final double[] THREE_ARM_SPLIT = {0.3, 0.3, 0.4};
    private static final String[] TWO_VARIANTS = {"A", "B"};
    private static final String[] THREE_VARIANTS = {"A", "B", "C"};
    private static final int COVERED_FULL_ON_VARIANT = 2;

    private static volatile Boolean cachedResult;

    /** Runs the probe lazily and exactly once so probe failures cannot prevent startup. */
    static boolean run() {
        Boolean result = cachedResult;
        if (result == null) {
            synchronized (HoldoutArmsSelfTest.class) {
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
            // Structural gate: three-arm support cannot exist without basic holdout support, so
            // this is a hard precondition rather than a second, independently-passable check that
            // merely happens to agree with `holdouts`.
            if (!HoldoutSelfTest.run()) {
                System.out.println("[holdout_arms probe] skipped: base holdouts battery did not pass");
                return false;
            }
            return runBattery();
        } catch (Throwable t) {
            // Incompatible SDKs can fail with linkage errors; the capability must fail closed.
            System.out.println("[holdout_arms probe] behavioral self-test FAILED with throwable: " + t);
            return false;
        }
    }

    private static boolean runBattery() throws Exception {
        Field holdoutIdsField = findField(Experiment.class, "holdoutIds");
        Field holdoutsField = findField(ContextData.class, "holdouts");
        if (holdoutIdsField == null || holdoutsField == null) {
            // HoldoutSelfTest.run() already returned true, so this is unreachable in practice,
            // but the probe must still fail closed rather than NPE if reflection ever disagrees.
            System.out.println("[holdout_arms probe] FAILED: linked core-api has no holdout wire model");
            return false;
        }
        Class<?> holdoutElementType = holdoutsField.getType().getComponentType();

        // --- Check 0 fixture (mirrors scenario 214: arm 0 holds out both) ---
        Experiment exp0NonFullOn = buildExperiment(601, "arms_0_non_fullon", UNIT_TYPE, 100, 200,
            COVERED_SPLIT, 0, TWO_VARIANTS, new int[] {621}, holdoutIdsField);
        Experiment exp0FullOn = buildExperiment(602, "arms_0_fullon", UNIT_TYPE, 100, 200,
            COVERED_SPLIT, COVERED_FULL_ON_VARIANT, THREE_VARIANTS, new int[] {621}, holdoutIdsField);
        Object holdout0 = buildHoldoutEntry(holdoutElementType, 621, "arms_holdout_0", UNIT_TYPE,
            0, 1, THREE_ARM_SPLIT, "all_full_on", THREE_VARIANTS);

        // --- Check 1 fixture (mirrors scenario 215: arm 1 holds out only the non-full-on one) ---
        Experiment exp1NonFullOn = buildExperiment(603, "arms_1_non_fullon", UNIT_TYPE, 100, 200,
            COVERED_SPLIT, 0, TWO_VARIANTS, new int[] {622}, holdoutIdsField);
        Experiment exp1FullOn = buildExperiment(604, "arms_1_fullon", UNIT_TYPE, 100, 200,
            COVERED_SPLIT, COVERED_FULL_ON_VARIANT, THREE_VARIANTS, new int[] {622}, holdoutIdsField);
        Object holdout1 = buildHoldoutEntry(holdoutElementType, 622, "arms_holdout_1", UNIT_TYPE,
            0, 3, THREE_ARM_SPLIT, "all_full_on", THREE_VARIANTS);

        // --- Check 2 fixture (mirrors scenario 216: arm 2 evaluates both normally) ---
        Experiment exp2NonFullOn = buildExperiment(605, "arms_2_non_fullon", UNIT_TYPE, 100, 200,
            COVERED_SPLIT, 0, TWO_VARIANTS, new int[] {623}, holdoutIdsField);
        Experiment exp2FullOn = buildExperiment(606, "arms_2_fullon", UNIT_TYPE, 100, 200,
            COVERED_SPLIT, COVERED_FULL_ON_VARIANT, THREE_VARIANTS, new int[] {623}, holdoutIdsField);
        Object holdout2 = buildHoldoutEntry(holdoutElementType, 623, "arms_holdout_2", UNIT_TYPE,
            0, 0, THREE_ARM_SPLIT, "all_full_on", THREE_VARIANTS);

        if (holdout0 == null || holdout1 == null || holdout2 == null) {
            System.out.println("[holdout_arms probe] FAILED: linked core-api's holdout entry type is "
                + "missing one of the fields (id/seedHi/seedLo/split) the variant assigner requires - "
                + "cannot construct the fixture");
            return false;
        }

        ContextData contextData = new ContextData();
        contextData.experiments = new Experiment[] {
            exp0NonFullOn, exp0FullOn, exp1NonFullOn, exp1FullOn, exp2NonFullOn, exp2FullOn
        };
        Object holdoutsArray = Array.newInstance(holdoutElementType, 3);
        Array.set(holdoutsArray, 0, holdout0);
        Array.set(holdoutsArray, 1, holdout1);
        Array.set(holdoutsArray, 2, holdout2);
        holdoutsField.set(contextData, holdoutsArray);

        List<ProbeExposure> exposures = new CopyOnWriteArrayList<>();
        ContextEventLogger logger = (context, type, data) -> {
            if (type == ContextEventLogger.EventType.Exposure && data instanceof Exposure) {
                Exposure exposure = (Exposure) data;
                exposures.add(new ProbeExposure(exposure.id, exposure.name, exposure.variant));
            }
        };

        ContextConfig contextConfig = ContextConfig.create()
            .setUnit(UNIT_TYPE, UID)
            .setPublishDelay(-1)
            .setRefreshInterval(0);

        ABSmartlyConfig sdkConfig = ABSmartlyConfig.create()
            .setContextDataProvider(new DummyContextDataProvider())
            .setContextEventHandler((context, event) -> java8.util.concurrent.CompletableFuture.completedFuture(null))
            .setContextEventLogger(logger);

        ABSmartly sdk = ABSmartly.create(sdkConfig);
        com.absmartly.sdk.Context context = sdk.createContextWith(contextConfig, contextData);
        context.waitUntilReady();

        if (!context.isReady() || context.isFailed()) {
            System.out.println("[holdout_arms probe] FAILED: context not ready (failed="
                + context.isFailed() + ")");
            return false;
        }

        String prefix = "[holdout_arms probe]";
        try {
            // Check 0 (mirrors 214): arm 0 holds out both the non-full-on and the full-on
            // covered experiment; the holdout's own exposure fires once, at variant 0, and
            // neither covered experiment emits its own exposure.
            if (!verifyTreatmentAndExposures(prefix, "0 non-full-on (mirrors 214)", context, exposures,
                    "arms_0_non_fullon", 0, new ProbeExposure(621, "arms_holdout_0", 0))) {
                return false;
            }
            if (!verifyTreatmentAndExposures(prefix, "0 full-on (mirrors 214)", context, exposures,
                    "arms_0_fullon", 0)) {
                return false;
            }

            // Check 1 (mirrors 215): arm 1 holds out only the non-full-on experiment (fullOn
            // variant == 0 branch of isHeldOutBy); the full-on experiment takes its normal path
            // and is assigned its own fullOnVariant with its own exposure.
            if (!verifyTreatmentAndExposures(prefix, "1 non-full-on (mirrors 215)", context, exposures,
                    "arms_1_non_fullon", 0, new ProbeExposure(622, "arms_holdout_1", 1))) {
                return false;
            }
            if (!verifyTreatmentAndExposures(prefix, "1 full-on (mirrors 215)", context, exposures,
                    "arms_1_fullon", COVERED_FULL_ON_VARIANT,
                    new ProbeExposure(604, "arms_1_fullon", COVERED_FULL_ON_VARIANT))) {
                return false;
            }

            // Check 2 (mirrors 216): arm 2 (normal traffic) evaluates both covered experiments
            // exactly as if uncovered; the holdout's own exposure fires once, at variant 2, and
            // each covered experiment emits its own exposure with its normally-assigned variant.
            // The non-full-on variant (1) is pinned to the value the same
            // unit/seedHi/seedLo/split/trafficSplit combination resolves to elsewhere in this
            // wrapper's probes (see HoldoutSelfTest's uncovered-sibling check).
            if (!verifyTreatmentAndExposures(prefix, "2 non-full-on (mirrors 216)", context, exposures,
                    "arms_2_non_fullon", 1,
                    new ProbeExposure(605, "arms_2_non_fullon", 1),
                    new ProbeExposure(623, "arms_holdout_2", 2))) {
                return false;
            }
            if (!verifyTreatmentAndExposures(prefix, "2 full-on (mirrors 216)", context, exposures,
                    "arms_2_fullon", COVERED_FULL_ON_VARIANT,
                    new ProbeExposure(606, "arms_2_fullon", COVERED_FULL_ON_VARIANT))) {
                return false;
            }
        } finally {
            context.close();
        }

        System.out.println("[holdout_arms probe] behavioral self-test PASSED: all 3 arm checks "
            + "mirroring scenarios 214-216 passed");
        return true;
    }
}
