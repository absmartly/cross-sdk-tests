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
import com.fasterxml.jackson.databind.ObjectMapper;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * Behavioral self-test behind the `switchback` capability. It mirrors the Switchback scenario
 * group: a sample of the platform golden vectors through SwitchbackAssigner, then switchback
 * serving through a real Context on the same clock-free grids the scenarios use. Switchback
 * fields are set by reflection so the wrapper still compiles against pre-switchback core-api;
 * the verdict always comes from observed treatments and exposures.
 */
final class SwitchbackSelfTest {
    private SwitchbackSelfTest() {}

    private static final String PREFIX = "[switchback probe]";

    // Grid constants shared with generate_switchback_scenarios.py: with a 400,000 h period "now"
    // stays in period 0 (or 1 for the earlier anchor) until 2069, and is before ANCHOR_FUTURE.
    private static final long PERIOD_MS = 400_000L * 3_600_000L;
    private static final long ANCHOR_PERIOD_0 = 1_704_067_200_000L;
    private static final long ANCHOR_PERIOD_1 = ANCHOR_PERIOD_0 - PERIOD_MS;
    private static final long ANCHOR_FUTURE = 4_102_444_800_000L;

    // {clusterUnit, periodIndex, seed, armCount, expectedVariant}, taken from the platform
    // golden vectors, including Long.MIN_VALUE/MAX_VALUE periods and seeds.
    private static final String[][] ASSIGN_CASES = {
        {"market_1", "0", "0", "2", "0"},
        {"market_1", "-9223372036854775808", "1", "2", "1"},
        {"market_1", "9223372036854775807", "1", "2", "1"},
        {"123456789", "0", "0", "2", "1"},
        {"market_1", "10", "0", "3", "2"},
        {"123456789", "-2", "-1", "3", "0"},
        {"market_1", "5", "1", "10", "0"},
        {"market_2", "-11", "-9223372036854775808", "10", "3"},
        {"market_2", "9223372036854775807", "-9223372036854775808", "10", "2"},
    };

    // {clusterUnit, cycleIndex, seed, expectedPermutation}, from the same fixture.
    private static final Object[][] PERMUTATION_CASES = {
        {"market_1", "0", "0", new int[] {0, 3, 7, 5, 8, 2, 6, 4, 1, 9}},
        {"market_1", "-9223372036854775808", "0", new int[] {1, 0}},
        {"123456789", "0", "0", new int[] {2, 0, 1}},
    };

    private static volatile Boolean cachedResult;

    /** Runs the probe lazily and exactly once so probe failures cannot prevent startup. */
    static boolean run() {
        Boolean result = cachedResult;
        if (result == null) {
            synchronized (SwitchbackSelfTest.class) {
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
            if (!checkGoldenSample() || !checkContextServing()) {
                return false;
            }
            System.out.println(PREFIX + " behavioral self-test PASSED");
            return true;
        } catch (Throwable t) {
            // Incompatible SDKs can fail with linkage errors; the capability must fail closed.
            System.out.println(PREFIX + " behavioral self-test FAILED with throwable: " + t);
            return false;
        }
    }

    private static boolean checkGoldenSample() {
        if (!SwitchbackAssignerAccess.isAvailable()) {
            System.out.println(PREFIX + " FAILED: linked core-api has no SwitchbackAssigner");
            return false;
        }
        for (String[] c : ASSIGN_CASES) {
            int actual = SwitchbackAssignerAccess.assign(c[0], Long.parseLong(c[1]), Long.parseLong(c[2]),
                Integer.parseInt(c[3]));
            if (actual != Integer.parseInt(c[4])) {
                System.out.println(PREFIX + " golden assign FAILED for " + Arrays.toString(c) + ": got " + actual);
                return false;
            }
        }
        for (Object[] c : PERMUTATION_CASES) {
            int[] expected = (int[]) c[3];
            int[] actual = SwitchbackAssignerAccess.permutation((String) c[0], Long.parseLong((String) c[1]),
                Long.parseLong((String) c[2]), expected.length);
            if (!Arrays.equals(expected, actual)) {
                System.out.println(PREFIX + " golden permutation FAILED for " + c[0] + "/" + c[1] + "/" + c[2]
                    + ": expected " + Arrays.toString(expected) + ", got " + Arrays.toString(actual));
                return false;
            }
        }
        return true;
    }

    private static boolean checkContextServing() throws Exception {
        if (HoldoutProbeSupport.findField(Experiment.class, "randomizationType") == null
            || HoldoutProbeSupport.findField(Experiment.class, "switchbackStartAt") == null
            || HoldoutProbeSupport.findField(Experiment.class, "switchbackPeriodMs") == null) {
            System.out.println(PREFIX + " FAILED: linked core-api has no switchback wire model");
            return false;
        }

        // Each check uses its own unit type so the hashed cluster units never collide. The uids
        // and expected variants are the ones the Switchback scenarios pin.
        List<Experiment> experiments = new ArrayList<>();
        experiments.add(switchback(501, "sb_p0_a2", "unit_p0_a2", 3603515, 233373850, 2, ANCHOR_PERIOD_0, 0));
        experiments.add(switchback(502, "sb_p0_a3", "unit_p0_a3", -559038737, 1234567890, 3, ANCHOR_PERIOD_0, 0));
        experiments.add(switchback(503, "sb_p0_a10", "unit_p0_a10", 2147483647, -1, 10, ANCHOR_PERIOD_0, 0));
        experiments.add(switchback(504, "sb_p1_a2", "unit_p1_a2", 3603515, 233373850, 2, ANCHOR_PERIOD_1, 0));
        experiments.add(switchback(505, "sb_p1_a3", "unit_p1_a3", -559038737, 1234567890, 3, ANCHOR_PERIOD_1, 0));
        experiments.add(switchback(506, "sb_p1_a10", "unit_p1_a10", 2147483647, -1, 10, ANCHOR_PERIOD_1, 0));
        experiments.add(switchback(507, "sb_pre_anchor", "unit_store", -559038737, 1234567890, 3, ANCHOR_FUTURE, 0));
        experiments.add(switchback(508, "sb_no_anchor", "unit_store", -559038737, 1234567890, 3, null, 0));
        experiments.add(switchback(509, "sb_no_cluster_unit", "unit_absent", -559038737, 1234567890, 3,
            ANCHOR_PERIOD_0, 0));
        experiments.add(switchback(510, "sb_dedup", "unit_serving", -559038737, 1234567890, 3, ANCHOR_PERIOD_0, 0));
        experiments.add(switchback(511, "sb_full_on", "unit_serving", -559038737, 1234567890, 3, ANCHOR_PERIOD_0, 2));
        experiments.add(switchback(512, "sb_override", "unit_serving", -559038737, 1234567890, 3,
            ANCHOR_PERIOD_0, 0));
        experiments.add(switchback(513, "sb_payload", "unit_serving", -559038737, 1234567890, 3, ANCHOR_PERIOD_0, 0));
        Experiment unitBaseline = switchback(514, "sb_unit_baseline", "unit_serving", -559038737, 1234567890, 3,
            null, 0);
        set(unitBaseline, "randomizationType", null);
        set(unitBaseline, "switchbackClusterUnitType", null);
        set(unitBaseline, "switchbackPeriodMs", null);
        set(unitBaseline, "switchbackDesignType", null);
        experiments.add(unitBaseline);

        ContextData contextData = new ContextData();
        contextData.experiments = experiments.toArray(new Experiment[0]);

        List<Exposure> exposures = new CopyOnWriteArrayList<>();
        ContextEventLogger logger = (context, type, data) -> {
            if (type == ContextEventLogger.EventType.Exposure && data instanceof Exposure) {
                exposures.add((Exposure) data);
            }
        };

        ContextConfig contextConfig = ContextConfig.create()
            .setUnit("unit_p0_a2", "p0-a2-a-3")
            .setUnit("unit_p0_a3", "p0-a3-a-1")
            .setUnit("unit_p0_a10", "p0-a10-a-0")
            .setUnit("unit_p1_a2", "p1-a2-a-8")
            .setUnit("unit_p1_a3", "p1-a3-a-2")
            .setUnit("unit_p1_a10", "p1-a10-a-0")
            .setUnit("unit_store", "eligibility-0")
            .setUnit("unit_serving", "serving-1")
            .setPublishDelay(-1)
            .setRefreshInterval(0);

        ABSmartlyConfig sdkConfig = ABSmartlyConfig.create()
            .setContextDataProvider(new DummyContextDataProvider())
            .setContextEventHandler((context, event) -> java8.util.concurrent.CompletableFuture.completedFuture(null))
            .setContextEventLogger(logger);

        com.absmartly.sdk.Context context = ABSmartly.create(sdkConfig).createContextWith(contextConfig, contextData);
        context.waitUntilReady();
        if (!context.isReady() || context.isFailed()) {
            System.out.println(PREFIX + " FAILED: context not ready (failed=" + context.isFailed() + ")");
            return false;
        }

        try {
            return expect(context, exposures, "period 0, 2 arms", "sb_p0_a2", 1, served(501, "sb_p0_a2", 1))
                && expect(context, exposures, "period 0, 3 arms", "sb_p0_a3", 1, served(502, "sb_p0_a3", 1))
                && expect(context, exposures, "period 0, 10 arms", "sb_p0_a10", 3, served(503, "sb_p0_a10", 3))
                && expect(context, exposures, "period 1, 2 arms", "sb_p1_a2", 1, served(504, "sb_p1_a2", 1))
                && expect(context, exposures, "period 1, 3 arms", "sb_p1_a3", 2, served(505, "sb_p1_a3", 2))
                && expect(context, exposures, "period 1, 10 arms", "sb_p1_a10", 6, served(506, "sb_p1_a10", 6))
                && expect(context, exposures, "pre-anchor", "sb_pre_anchor", 0,
                    new Exposure(507, "sb_pre_anchor", "unit_store", 0, 0, true, false, false, false, false, false))
                && expect(context, exposures, "pre-anchor repeat", "sb_pre_anchor", 0)
                && expect(context, exposures, "missing anchor", "sb_no_anchor", 0,
                    new Exposure(508, "sb_no_anchor", "unit_store", 0, 0, true, false, false, false, false, false))
                && expect(context, exposures, "cluster unit not set", "sb_no_cluster_unit", 0,
                    new Exposure(509, "sb_no_cluster_unit", "unit_absent", 0, 0, false, true, false, false, false,
                        false))
                && expect(context, exposures, "same period", "sb_dedup", 1,
                    new Exposure(510, "sb_dedup", "unit_serving", 1, 0, true, true, false, false, false, false))
                && expect(context, exposures, "same period repeat", "sb_dedup", 1)
                && expect(context, exposures, "full-on", "sb_full_on", 2,
                    new Exposure(511, "sb_full_on", "unit_serving", 2, 0, true, true, false, true, false, false))
                && checkOverride(context, exposures)
                && checkPayloadShape(context, exposures);
        } finally {
            context.close();
        }
    }

    // Mirrors the payload scenario: a switchback exposure serializes to exactly the fields of a
    // unit exposure on the same unit.
    private static boolean checkPayloadShape(com.absmartly.sdk.Context context, List<Exposure> exposures) {
        int before = exposures.size();
        context.getTreatment("sb_unit_baseline");
        context.getTreatment("sb_payload");
        List<Exposure> emitted = new ArrayList<>(exposures.subList(before, exposures.size()));
        if (emitted.size() != 2) {
            System.out.println(PREFIX + " check payload shape FAILED: expected 2 exposures, got " + emitted);
            return false;
        }
        ObjectMapper mapper = new ObjectMapper();
        Set<String> unitFields = new TreeSet<>();
        mapper.valueToTree(emitted.get(0)).fieldNames().forEachRemaining(unitFields::add);
        Set<String> switchbackFields = new TreeSet<>();
        mapper.valueToTree(emitted.get(1)).fieldNames().forEachRemaining(switchbackFields::add);
        if (!unitFields.equals(switchbackFields)) {
            System.out.println(PREFIX + " check payload shape FAILED: unit exposure fields " + unitFields
                + ", switchback exposure fields " + switchbackFields);
            return false;
        }
        return true;
    }

    private static boolean checkOverride(com.absmartly.sdk.Context context, List<Exposure> exposures) {
        context.setOverride("sb_override", 2);
        return expect(context, exposures, "override", "sb_override", 2,
            new Exposure(512, "sb_override", "unit_serving", 2, 0, false, true, true, false, false, false));
    }

    private static Experiment switchback(int id, String name, String unitType, int seedHi, int seedLo, int arms,
            Long startAt, int fullOnVariant) throws Exception {
        Experiment experiment = new Experiment();
        experiment.id = id;
        experiment.name = name;
        experiment.iteration = 1;
        experiment.unitType = unitType;
        experiment.seedHi = seedHi;
        experiment.seedLo = seedLo;
        double[] split = new double[arms];
        Arrays.fill(split, 1.0 / arms);
        experiment.split = split;
        experiment.trafficSeedHi = 1;
        experiment.trafficSeedLo = 2;
        experiment.trafficSplit = new double[] {0, 1};
        experiment.fullOnVariant = fullOnVariant;
        experiment.applications = new ExperimentApplication[] {new ExperimentApplication("website")};
        ExperimentVariant[] variants = new ExperimentVariant[arms];
        for (int i = 0; i < arms; i++) {
            variants[i] = new ExperimentVariant(String.valueOf((char) ('A' + i)), null);
        }
        experiment.variants = variants;

        set(experiment, "randomizationType", "switchback");
        set(experiment, "switchbackClusterUnitType", unitType);
        set(experiment, "switchbackStartAt", startAt);
        set(experiment, "switchbackPeriodMs", PERIOD_MS);
        set(experiment, "switchbackDesignType", "balanced_block");
        return experiment;
    }

    private static void set(Experiment experiment, String fieldName, Object value) throws Exception {
        Field field = HoldoutProbeSupport.findField(Experiment.class, fieldName);
        if (field == null) {
            throw new NoSuchFieldException(fieldName);
        }
        field.set(experiment, value);
    }

    private static Exposure served(int id, String name, int variant) {
        String unitType = "unit_" + name.substring("sb_".length());
        return new Exposure(id, name, unitType, variant, 0, true, true, false, false, false, false);
    }

    /**
     * One getTreatment call: the treatment and the exposures it emits must match exactly, every
     * Exposure field except exposedAt included.
     */
    private static boolean expect(com.absmartly.sdk.Context context, List<Exposure> exposures, String check,
            String experimentName, int expectedTreatment, Exposure... expectedExposures) {
        int before = exposures.size();
        int treatment = context.getTreatment(experimentName);
        List<Exposure> emitted = new ArrayList<>(exposures.subList(before, exposures.size()));

        boolean matches = treatment == expectedTreatment && emitted.size() == expectedExposures.length;
        for (int i = 0; matches && i < expectedExposures.length; i++) {
            Exposure actual = emitted.get(i);
            Exposure expected = expectedExposures[i];
            matches = actual.id == expected.id && expected.name.equals(actual.name)
                && expected.unit.equals(actual.unit) && actual.variant == expected.variant
                && actual.assigned == expected.assigned && actual.eligible == expected.eligible
                && actual.overridden == expected.overridden && actual.fullOn == expected.fullOn
                && actual.custom == expected.custom && actual.audienceMismatch == expected.audienceMismatch
                && actual.exposedAt > 0;
        }
        if (!matches) {
            System.out.println(PREFIX + " check " + check + " FAILED: expected treatment=" + expectedTreatment
                + " exposures=" + Arrays.toString(expectedExposures) + ", got treatment=" + treatment
                + " exposures=" + emitted);
        }
        return matches;
    }
}
