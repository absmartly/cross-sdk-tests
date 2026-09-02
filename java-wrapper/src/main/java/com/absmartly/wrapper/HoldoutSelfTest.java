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
 * Behavioral self-test for all holdout semantics covered by scenarios 203-208 and 221. Reflection
 * is used only to build fixtures so this wrapper still compiles against pre-holdout core-api
 * releases; verdicts always come from observed treatments and exposures.
 */
final class HoldoutSelfTest {
    private HoldoutSelfTest() {}

    private static final String HELD_OUT_UNIT_TYPE = "session_id";
    // Same unit id used by the orchestrator's scenario 203/205/206/207/208 fixtures: relative to
    // a holdout with seedHi=13/seedLo=111/split=[0.1,0.9] it lands in variant 0 (held out).
    private static final String HELD_OUT_UID = "e791e240fcd3df7d238cfc285f475e8152fcc0ec";

    private static final String NOT_HELD_OUT_UNIT_TYPE = "session_id_b";
    // Same unit id used by the orchestrator's scenario 204 fixture: relative to the same
    // seedHi/seedLo/split it lands OUTSIDE the holdout's held-out arm (variant 1).
    private static final String NOT_HELD_OUT_UID = "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3";

    // A third unit type, deliberately NOT installed on the shared context at creation, for check
    // G (mirrors scenario 221): the covered experiment must be resolvable before this unit type
    // exists (full-on experiments never read the uid), and the holdout's own exposure must still
    // publish, exactly once, once setUnit installs it and the experiment is resolved again. Reuses
    // HELD_OUT_UID/seedHi=1/seedLo=222 (see holdoutG below), the same combination scenario 205/219
    // /220 use for a not-held-out (variant 1) 2-arm outcome.
    private static final String LATE_UNIT_TYPE = "user_id";

    private static final double[] EXPERIMENT_SPLIT = {0.5, 0.5};
    private static final double[] HOLDOUT_SPLIT = {0.1, 0.9};
    private static final String[] TWO_VARIANTS = {"A", "B"};
    private static final String[] THREE_VARIANTS = {"A", "B", "C"};

    private static volatile Boolean cachedResult;

    /** Runs the probe lazily and exactly once so probe failures cannot prevent startup. */
    static boolean run() {
        Boolean result = cachedResult;
        if (result == null) {
            synchronized (HoldoutSelfTest.class) {
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
            return runBattery();
        } catch (Throwable t) {
            // Incompatible SDKs can fail with linkage errors; the capability must fail closed.
            System.out.println("[holdouts probe] behavioral self-test FAILED with throwable: " + t);
            return false;
        }
    }

    private static boolean runBattery() throws Exception {
        Field holdoutIdsField = findField(Experiment.class, "holdoutIds");
        Field holdoutsField = findField(ContextData.class, "holdouts");
        if (holdoutIdsField == null || holdoutsField == null) {
            System.out.println("[holdouts probe] behavioral self-test FAILED: linked core-api has no "
                + "holdout wire model (Experiment.holdoutIds / ContextData.holdouts absent) - cannot "
                + "even construct the fixture, so holdout semantics cannot be present either");
            return false;
        }
        Class<?> holdoutElementType = holdoutsField.getType().getComponentType();

        // --- Check A fixture (mirrors scenario 203) ---
        Experiment expA = buildExperiment(311, "chk_a_covered", HELD_OUT_UNIT_TYPE, 100, 200,
            EXPERIMENT_SPLIT, 0, TWO_VARIANTS, new int[] {411}, holdoutIdsField);
        Object holdoutA = buildHoldoutEntry(holdoutElementType, 411, "chk_a_holdout", HELD_OUT_UNIT_TYPE,
            13, 111, HOLDOUT_SPLIT, "full", TWO_VARIANTS);

        // --- Check B fixture (mirrors scenario 204) ---
        Experiment expB = buildExperiment(312, "chk_b_covered", NOT_HELD_OUT_UNIT_TYPE, 100, 200,
            EXPERIMENT_SPLIT, 0, TWO_VARIANTS, new int[] {412}, holdoutIdsField);
        Object holdoutB = buildHoldoutEntry(holdoutElementType, 412, "chk_b_holdout", NOT_HELD_OUT_UNIT_TYPE,
            13, 111, HOLDOUT_SPLIT, "full", TWO_VARIANTS);

        // --- Check C fixture (mirrors scenario 205: union, held out via the HIGHER id only) ---
        Experiment expC = buildExperiment(313, "chk_c_union", HELD_OUT_UNIT_TYPE, 100, 200,
            EXPERIMENT_SPLIT, 0, TWO_VARIANTS, new int[] {413, 414}, holdoutIdsField);
        Object holdoutCLow = buildHoldoutEntry(holdoutElementType, 413, "chk_c_holdout_low", HELD_OUT_UNIT_TYPE,
            1, 222, HOLDOUT_SPLIT, "full", TWO_VARIANTS);
        Object holdoutCHigh = buildHoldoutEntry(holdoutElementType, 414, "chk_c_holdout_high", HELD_OUT_UNIT_TYPE,
            13, 111, HOLDOUT_SPLIT, "full", TWO_VARIANTS);

        // --- Check D fixture (mirrors scenario 206: coverage is opt-in per experiment) ---
        Experiment expDCovered = buildExperiment(314, "chk_d_covered", HELD_OUT_UNIT_TYPE, 100, 200,
            EXPERIMENT_SPLIT, 0, TWO_VARIANTS, new int[] {415}, holdoutIdsField);
        Experiment expDSibling = buildExperiment(315, "chk_d_uncovered_sibling", HELD_OUT_UNIT_TYPE, 100, 200,
            EXPERIMENT_SPLIT, 0, TWO_VARIANTS, null, holdoutIdsField);
        Object holdoutD = buildHoldoutEntry(holdoutElementType, 415, "chk_d_holdout", HELD_OUT_UNIT_TYPE,
            13, 111, HOLDOUT_SPLIT, "full", TWO_VARIANTS);

        // --- Check E fixture (mirrors scenario 207: dangling id ignored, valid id still applies) ---
        Experiment expE = buildExperiment(316, "chk_e_dangling_plus_valid", HELD_OUT_UNIT_TYPE, 100, 200,
            EXPERIMENT_SPLIT, 0, TWO_VARIANTS, new int[] {999, 416}, holdoutIdsField);
        Object holdoutE = buildHoldoutEntry(holdoutElementType, 416, "chk_e_holdout", HELD_OUT_UNIT_TYPE,
            13, 111, HOLDOUT_SPLIT, "full", TWO_VARIANTS);

        // --- Check F fixture (mirrors scenario 208: suppresses a full-on experiment) ---
        Experiment expF = buildExperiment(317, "chk_f_fullon", HELD_OUT_UNIT_TYPE, 100, 200,
            EXPERIMENT_SPLIT, 2, THREE_VARIANTS, new int[] {417}, holdoutIdsField);
        Object holdoutF = buildHoldoutEntry(holdoutElementType, 417, "chk_f_holdout", HELD_OUT_UNIT_TYPE,
            13, 111, HOLDOUT_SPLIT, "full", TWO_VARIANTS);

        // --- Check G fixture (mirrors scenario 221: late-unit lost-exposure) ---
        // fullOnVariant != 0 so the experiment resolves without ever reading LATE_UNIT_TYPE's
        // uid; the holdout is covered but cannot be evaluated until setUnit runs.
        Experiment expG = buildExperiment(318, "chk_g_late_unit", LATE_UNIT_TYPE, 100, 200,
            EXPERIMENT_SPLIT, 2, THREE_VARIANTS, new int[] {418}, holdoutIdsField);
        Object holdoutG = buildHoldoutEntry(holdoutElementType, 418, "chk_g_holdout", LATE_UNIT_TYPE,
            1, 222, HOLDOUT_SPLIT, "full", TWO_VARIANTS);

        if (holdoutA == null || holdoutB == null || holdoutCLow == null || holdoutCHigh == null
            || holdoutD == null || holdoutE == null || holdoutF == null || holdoutG == null) {
            System.out.println("[holdouts probe] behavioral self-test FAILED: linked core-api's holdout "
                + "entry type is missing one of the fields (id/seedHi/seedLo/split) the variant "
                + "assigner requires - cannot construct the fixture");
            return false;
        }

        ContextData contextData = new ContextData();
        contextData.experiments = new Experiment[] {
            expA, expB, expC, expDCovered, expDSibling, expE, expF, expG
        };
        Object holdoutsArray = Array.newInstance(holdoutElementType, 8);
        Array.set(holdoutsArray, 0, holdoutA);
        Array.set(holdoutsArray, 1, holdoutB);
        Array.set(holdoutsArray, 2, holdoutCLow);
        Array.set(holdoutsArray, 3, holdoutCHigh);
        Array.set(holdoutsArray, 4, holdoutD);
        Array.set(holdoutsArray, 5, holdoutE);
        Array.set(holdoutsArray, 6, holdoutF);
        Array.set(holdoutsArray, 7, holdoutG);
        holdoutsField.set(contextData, holdoutsArray);

        List<ProbeExposure> exposures = new CopyOnWriteArrayList<>();
        ContextEventLogger logger = (context, type, data) -> {
            if (type == ContextEventLogger.EventType.Exposure && data instanceof Exposure) {
                Exposure exposure = (Exposure) data;
                exposures.add(new ProbeExposure(exposure.id, exposure.name, exposure.variant));
            }
        };

        // LATE_UNIT_TYPE is deliberately absent here - see check G below.
        ContextConfig contextConfig = ContextConfig.create()
            .setUnit(HELD_OUT_UNIT_TYPE, HELD_OUT_UID)
            .setUnit(NOT_HELD_OUT_UNIT_TYPE, NOT_HELD_OUT_UID)
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
            System.out.println("[holdouts probe] behavioral self-test FAILED: context not ready ("
                + "failed=" + context.isFailed() + ")");
            return false;
        }

        String prefix = "[holdouts probe]";
        try {
            if (!verifyTreatmentAndExposures(prefix, "A (mirrors 203)", context, exposures, "chk_a_covered", 0,
                    new ProbeExposure(411, "chk_a_holdout", 0))) return false;
            if (!verifyTreatmentAndExposures(prefix, "B (mirrors 204)", context, exposures, "chk_b_covered", 0,
                    new ProbeExposure(312, "chk_b_covered", 0),
                    new ProbeExposure(412, "chk_b_holdout", 1))) return false;
            if (!verifyTreatmentAndExposures(prefix, "C (mirrors 205)", context, exposures, "chk_c_union", 0,
                    new ProbeExposure(413, "chk_c_holdout_low", 1),
                    new ProbeExposure(414, "chk_c_holdout_high", 0))) return false;
            if (!checkCoverageOptInPerExperiment(prefix, context, exposures)) return false;
            if (!verifyTreatmentAndExposures(prefix, "E (mirrors 207)", context, exposures,
                    "chk_e_dangling_plus_valid", 0,
                    new ProbeExposure(416, "chk_e_holdout", 0))) return false;
            if (!verifyTreatmentAndExposures(prefix, "F (mirrors 208)", context, exposures, "chk_f_fullon", 0,
                    new ProbeExposure(417, "chk_f_holdout", 0))) return false;
            if (!checkLateUnitPublishesHoldoutExposure(prefix, context, exposures)) return false;
        } finally {
            context.close();
        }

        System.out.println("[holdouts probe] behavioral self-test PASSED: all 7 checks mirroring "
            + "scenarios 203-208 and 221 passed");
        return true;
    }

    /** Check D - mirrors scenario 206: holdout coverage never leaks to an uncovered sibling. */
    private static boolean checkCoverageOptInPerExperiment(String prefix, com.absmartly.sdk.Context context,
            List<ProbeExposure> exposures) {
        if (!verifyTreatmentAndExposures(prefix, "D (mirrors 206, covered)", context, exposures,
                "chk_d_covered", 0, new ProbeExposure(415, "chk_d_holdout", 0))) {
            return false;
        }
        // The uncovered sibling has no holdoutIds, so it must assign and expose exactly as it
        // would with no holdout in the payload at all - variant 1, unaffected by the holdout
        // covering its sibling.
        return verifyTreatmentAndExposures(prefix, "D (mirrors 206, uncovered sibling)", context, exposures,
            "chk_d_uncovered_sibling", 1,
            new ProbeExposure(315, "chk_d_uncovered_sibling", 1));
    }

    /**
     * Check G - mirrors scenario 221: peek resolves the full-on covered experiment before its
     * unit type is installed (full-on experiments never read the uid, so this succeeds and must
     * not itself expose anything); setUnit then installs it; a second resolution - via
     * getTreatment, which does expose - must publish BOTH the experiment's own exposure and the
     * holdout's own exposure exactly once each. This is what distinguishes the capability from a
     * probe that only exercises checks A-F: an SDK that resolves a covered full-on experiment
     * correctly before its unit exists, but pins that decision so the holdout's own exposure is
     * silently lost once the unit later arrives, passes A-F yet fails this check and 221.
     */
    private static boolean checkLateUnitPublishesHoldoutExposure(String prefix, com.absmartly.sdk.Context context,
            List<ProbeExposure> exposures) {
        int before = exposures.size();
        int peeked = context.peekTreatment("chk_g_late_unit");
        List<ProbeExposure> peekExposures = exposures.subList(before, exposures.size());
        if (peeked != 2 || !peekExposures.isEmpty()) {
            System.out.println(prefix + " check G (mirrors 221, pre-unit peek) FAILED: expected "
                + "treatment=2 with no exposures, got treatment=" + peeked + " exposures=" + peekExposures);
            return false;
        }

        context.setUnit(LATE_UNIT_TYPE, HELD_OUT_UID);

        return verifyTreatmentAndExposures(prefix, "G (mirrors 221, post-unit treatment)", context, exposures,
            "chk_g_late_unit", 2,
            new ProbeExposure(318, "chk_g_late_unit", 2),
            new ProbeExposure(418, "chk_g_holdout", 1));
    }
}
