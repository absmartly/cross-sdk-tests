package com.absmartly.wrapper;

import com.absmartly.sdk.ABSmartly;
import com.absmartly.sdk.ABSmartlyConfig;
import com.absmartly.sdk.ContextConfig;
import com.absmartly.sdk.ContextEventLogger;
import com.absmartly.sdk.json.ContextData;
import com.absmartly.sdk.json.Experiment;
import com.absmartly.sdk.json.ExperimentApplication;
import com.absmartly.sdk.json.ExperimentVariant;
import com.absmartly.sdk.json.Exposure;

import java.lang.reflect.Array;
import java.lang.reflect.Field;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * Behavioral self-test for all holdout semantics covered by scenarios 203-208. Reflection is used
 * only to build fixtures so this wrapper still compiles against pre-holdout core-api releases;
 * verdicts always come from observed treatments and exposures.
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
            13, 111, HOLDOUT_SPLIT, "full_on", TWO_VARIANTS);

        if (holdoutA == null || holdoutB == null || holdoutCLow == null || holdoutCHigh == null
            || holdoutD == null || holdoutE == null || holdoutF == null) {
            System.out.println("[holdouts probe] behavioral self-test FAILED: linked core-api's holdout "
                + "entry type is missing one of the fields (id/seedHi/seedLo/split) the variant "
                + "assigner requires - cannot construct the fixture");
            return false;
        }

        ContextData contextData = new ContextData();
        contextData.experiments = new Experiment[] {expA, expB, expC, expDCovered, expDSibling, expE, expF};
        Object holdoutsArray = Array.newInstance(holdoutElementType, 7);
        Array.set(holdoutsArray, 0, holdoutA);
        Array.set(holdoutsArray, 1, holdoutB);
        Array.set(holdoutsArray, 2, holdoutCLow);
        Array.set(holdoutsArray, 3, holdoutCHigh);
        Array.set(holdoutsArray, 4, holdoutD);
        Array.set(holdoutsArray, 5, holdoutE);
        Array.set(holdoutsArray, 6, holdoutF);
        holdoutsField.set(contextData, holdoutsArray);

        List<ProbeExposure> exposures = new CopyOnWriteArrayList<>();
        ContextEventLogger logger = (context, type, data) -> {
            if (type == ContextEventLogger.EventType.Exposure && data instanceof Exposure) {
                Exposure exposure = (Exposure) data;
                exposures.add(new ProbeExposure(exposure.id, exposure.name, exposure.variant));
            }
        };

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

        try {
            if (!verifyTreatmentAndExposures("A (mirrors 203)", context, exposures, "chk_a_covered", 0,
                    new ProbeExposure(411, "chk_a_holdout", 0))) return false;
            if (!verifyTreatmentAndExposures("B (mirrors 204)", context, exposures, "chk_b_covered", 0,
                    new ProbeExposure(312, "chk_b_covered", 0),
                    new ProbeExposure(412, "chk_b_holdout", 1))) return false;
            if (!verifyTreatmentAndExposures("C (mirrors 205)", context, exposures, "chk_c_union", 0,
                    new ProbeExposure(413, "chk_c_holdout_low", 1),
                    new ProbeExposure(414, "chk_c_holdout_high", 0))) return false;
            if (!checkCoverageOptInPerExperiment(context, exposures)) return false;
            if (!verifyTreatmentAndExposures("E (mirrors 207)", context, exposures,
                    "chk_e_dangling_plus_valid", 0,
                    new ProbeExposure(416, "chk_e_holdout", 0))) return false;
            if (!verifyTreatmentAndExposures("F (mirrors 208)", context, exposures, "chk_f_fullon", 0,
                    new ProbeExposure(417, "chk_f_holdout", 0))) return false;
        } finally {
            context.close();
        }

        System.out.println("[holdouts probe] behavioral self-test PASSED: all 6 checks mirroring "
            + "scenarios 203-208 passed");
        return true;
    }

    /** Check D - mirrors scenario 206: holdout coverage never leaks to an uncovered sibling. */
    private static boolean checkCoverageOptInPerExperiment(com.absmartly.sdk.Context context,
            List<ProbeExposure> exposures) {
        if (!verifyTreatmentAndExposures("D (mirrors 206, covered)", context, exposures, "chk_d_covered", 0,
                new ProbeExposure(415, "chk_d_holdout", 0))) {
            return false;
        }
        // The uncovered sibling has no holdoutIds, so it must assign and expose exactly as it
        // would with no holdout in the payload at all - variant 1, unaffected by the holdout
        // covering its sibling.
        return verifyTreatmentAndExposures("D (mirrors 206, uncovered sibling)", context, exposures,
            "chk_d_uncovered_sibling", 1,
            new ProbeExposure(315, "chk_d_uncovered_sibling", 1));
    }

    /**
     * Shared check body for every A-F check: calls getTreatment(experimentName) exactly once,
     * then requires BOTH the returned treatment AND the exact ordered id/name/variant sequence of
     * exposure events appended by that single call to match what the mirrored scenario
     * (203-208 in test_scenarios_complete.json) pins byte-for-byte. Order matters (e.g. check B's
     * experiment-exposure-then-holdout-exposure order, check C's low-id-then-high-id order) and so
     * does every variant (e.g. check C's variants are what prove suppression came through the
     * HIGHER-id holdout, not just "some" holdout) - a check that only compared id/name/count could
     * pass against an SDK that resolves the right ids with the wrong variants or the wrong order.
     */
    private static boolean verifyTreatmentAndExposures(String checkLabel, com.absmartly.sdk.Context context,
            List<ProbeExposure> exposures, String experimentName, int expectedTreatment,
            ProbeExposure... expectedSequence) {
        int before = exposures.size();
        int treatment = context.getTreatment(experimentName);
        List<ProbeExposure> newExposures = exposures.subList(before, exposures.size());

        if (treatment != expectedTreatment || !matchesSequence(newExposures, expectedSequence)) {
            System.out.println("[holdouts probe] check " + checkLabel + " FAILED: expected treatment="
                + expectedTreatment + " with exposure sequence " + java.util.Arrays.toString(expectedSequence)
                + ", got treatment=" + treatment + " exposures=" + newExposures);
            return false;
        }
        return true;
    }

    private static boolean matchesSequence(List<ProbeExposure> actual, ProbeExposure[] expected) {
        if (actual.size() != expected.length) {
            return false;
        }
        for (int i = 0; i < expected.length; i++) {
            ProbeExposure exposure = actual.get(i);
            ProbeExposure exp = expected[i];
            if (exp.id != exposure.id || !exp.name.equals(exposure.name) || exp.variant != exposure.variant) {
                return false;
            }
        }
        return true;
    }

    /** One position in an actual or expected ordered exposure sequence. */
    private static final class ProbeExposure {
        final int id;
        final String name;
        final int variant;

        ProbeExposure(int id, String name, int variant) {
            this.id = id;
            this.name = name;
            this.variant = variant;
        }

        @Override
        public String toString() {
            return "{id=" + id + ", name=" + name + ", variant=" + variant + "}";
        }
    }

    /**
     * Builds one covered experiment. holdoutIds is set via reflection (see class javadoc); every
     * other field is a direct compile-time reference since these have existed on Experiment since
     * before holdout support.
     */
    private static Experiment buildExperiment(int id, String name, String unitType, int seedHi, int seedLo,
            double[] split, int fullOnVariant, String[] variantNames, int[] holdoutIds, Field holdoutIdsField)
            throws Exception {
        Experiment experiment = new Experiment();
        experiment.id = id;
        experiment.name = name;
        experiment.iteration = 1;
        experiment.unitType = unitType;
        experiment.seedHi = seedHi;
        experiment.seedLo = seedLo;
        experiment.split = split;
        experiment.trafficSeedHi = 1;
        experiment.trafficSeedLo = 2;
        experiment.trafficSplit = new double[] {0, 1};
        experiment.fullOnVariant = fullOnVariant;
        experiment.applications = new ExperimentApplication[] {new ExperimentApplication("website")};
        ExperimentVariant[] variants = new ExperimentVariant[variantNames.length];
        for (int i = 0; i < variantNames.length; i++) {
            variants[i] = new ExperimentVariant(variantNames[i], null);
        }
        experiment.variants = variants;
        if (holdoutIds != null) {
            holdoutIdsField.set(experiment, holdoutIds);
        }
        return experiment;
    }

    /**
     * Constructs one holdout entry of whatever type ContextData.holdouts actually holds on the
     * linked core-api. Every holdout wire shape this SDK has ever shipped carries at minimum
     * id/seedHi/seedLo/split (the fields the variant assigner needs); anything else (name,
     * iteration, unitType, trafficSeedHi/Lo/Split, fullOnVariant, applications, variants, a
     * holdoutType discriminator, ...) is set opportunistically when present so the fixture
     * matches the live wire shape as closely as possible, but their absence does not fail fixture
     * construction. Returns null if id/seedHi/seedLo/split are unavailable - the caller treats
     * that as "cannot build the fixture" and fails the probe closed.
     */
    private static Object buildHoldoutEntry(Class<?> holdoutType, int id, String name, String unitType,
            int seedHi, int seedLo, double[] split, String holdoutTypeName, String[] variantNames) throws Exception {
        Object holdout = holdoutType.getDeclaredConstructor().newInstance();

        Field idField = findField(holdoutType, "id");
        Field seedHiField = findField(holdoutType, "seedHi");
        Field seedLoField = findField(holdoutType, "seedLo");
        Field splitField = findField(holdoutType, "split");
        if (idField == null || seedHiField == null || seedLoField == null || splitField == null) {
            return null;
        }
        idField.set(holdout, id);
        seedHiField.set(holdout, seedHi);
        seedLoField.set(holdout, seedLo);
        splitField.set(holdout, split);

        setIfPresent(holdout, "name", name);
        setIfPresent(holdout, "iteration", 1);
        setIfPresent(holdout, "unitType", unitType);
        setIfPresent(holdout, "trafficSeedHi", 0);
        setIfPresent(holdout, "trafficSeedLo", 0);
        setIfPresent(holdout, "trafficSplit", new double[] {0, 1});
        setIfPresent(holdout, "fullOnVariant", 0);
        setIfPresent(holdout, "applications", new ExperimentApplication[0]);
        ExperimentVariant[] variants = new ExperimentVariant[variantNames.length];
        for (int i = 0; i < variantNames.length; i++) {
            variants[i] = new ExperimentVariant(variantNames[i], null);
        }
        setIfPresent(holdout, "variants", variants);
        setIfPresent(holdout, "holdoutType", holdoutTypeName);

        return holdout;
    }

    private static void setIfPresent(Object target, String fieldName, Object value) throws Exception {
        Field field = findField(target.getClass(), fieldName);
        if (field != null && field.getType().isInstance(value)) {
            field.set(target, value);
        }
    }

    private static Field findField(Class<?> type, String name) {
        try {
            return type.getField(name);
        } catch (NoSuchFieldException e) {
            return null;
        }
    }
}
