package com.absmartly.wrapper;

import com.absmartly.sdk.ABSmartly;
import com.absmartly.sdk.ABSmartlyConfig;
import com.absmartly.sdk.Context;
import com.absmartly.sdk.ContextConfig;
import com.absmartly.sdk.ContextEventLogger;
import com.absmartly.sdk.DefaultContextEventSerializer;
import com.absmartly.sdk.json.ContextData;
import com.absmartly.sdk.json.Experiment;
import com.absmartly.sdk.json.ExperimentApplication;
import com.absmartly.sdk.json.ExperimentVariant;
import com.absmartly.sdk.json.Exposure;
import com.absmartly.sdk.json.PublishEvent;
import com.absmartly.sdk.json.Unit;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java8.util.concurrent.CompletableFuture;

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

    // hashUnit("serving-9"): base64url-no-pad MD5, as published in PublishEvent.units.
    private static final String SERVING_UNIT_HASH = "r5uxZRoi-JuM5yYJa0kmSQ";

    // {key, seed, count, expectedBucket}: hashlib-computed vectors pinned by the platform contract.
    private static final String[][] BUCKET_CASES = {
        {"1tdwU5K8evYzMovqjExpBA", "0", "10", "6"},
        {"\u6771\u4eac", "-123456789012345", "50", "19"},
        {"a\u001fb", "-9223372036854775808", "100", "43"},
    };

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

    // Runs the probe lazily and exactly once so probe failures cannot prevent startup.
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
            if (!checkGoldenSample(SwitchbackFunctions.LINKED) || !checkContextServing()) {
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

    static boolean checkGoldenSample(SwitchbackFunctions functions) {
        if (!functions.isAvailable()) {
            System.out.println(PREFIX + " FAILED: linked core-api has no SwitchbackAssigner");
            return false;
        }
        for (String[] c : ASSIGN_CASES) {
            int actual = functions.assign(c[0], Long.parseLong(c[1]), Long.parseLong(c[2]),
                Integer.parseInt(c[3]));
            if (actual != Integer.parseInt(c[4])) {
                System.out.println(PREFIX + " golden assign FAILED for " + Arrays.toString(c) + ": got " + actual);
                return false;
            }
        }
        if (!functions.isBucketAvailable()) {
            System.out.println(PREFIX + " FAILED: linked core-api has no SwitchbackBuckets");
            return false;
        }
        for (String[] c : BUCKET_CASES) {
            int actual = functions.bucketOf(c[0], Long.parseLong(c[1]), Integer.parseInt(c[2]));
            if (actual != Integer.parseInt(c[3])) {
                System.out.println(PREFIX + " golden bucket FAILED for " + Arrays.toString(c) + ": got " + actual);
                return false;
            }
        }
        for (Object[] c : PERMUTATION_CASES) {
            int[] expected = (int[]) c[3];
            int[] actual = functions.permutation((String) c[0], Long.parseLong((String) c[1]),
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
            || HoldoutProbeSupport.findField(Experiment.class, "switchbackPeriodMs") == null
            || HoldoutProbeSupport.findField(Experiment.class, "switchbackClusterCount") == null
            || HoldoutProbeSupport.findField(Experiment.class, "switchbackClusterAttribute") == null) {
            System.out.println(PREFIX + " FAILED: linked core-api has no switchback wire model");
            return false;
        }

        // Each check uses its own unit type. The uids and expected variants are the ones the
        // Switchback scenarios pin: period checks use 10 buckets, the others 100.
        List<Experiment> experiments = new ArrayList<>();
        experiments.add(switchback(501, "sb_p0_a2", "unit_p0_a2", 3603515, 233373850, 2, ANCHOR_PERIOD_0, 0, 10));
        experiments.add(switchback(502, "sb_p0_a3", "unit_p0_a3", -559038737, 1234567890, 3, ANCHOR_PERIOD_0, 0, 10));
        experiments.add(switchback(503, "sb_p0_a10", "unit_p0_a10", 2147483647, -1, 10, ANCHOR_PERIOD_0, 0, 10));
        experiments.add(switchback(504, "sb_p1_a2", "unit_p1_a2", 3603515, 233373850, 2, ANCHOR_PERIOD_1, 0, 10));
        experiments.add(switchback(505, "sb_p1_a3", "unit_p1_a3", -559038737, 1234567890, 3, ANCHOR_PERIOD_1, 0, 10));
        experiments.add(switchback(506, "sb_p1_a10", "unit_p1_a10", 2147483647, -1, 10, ANCHOR_PERIOD_1, 0, 10));
        experiments.add(switchback(507, "sb_pre_anchor", "unit_store", -559038737, 1234567890, 3, ANCHOR_FUTURE, 0, 100));
        experiments.add(switchback(508, "sb_no_anchor", "unit_store", -559038737, 1234567890, 3, null, 0, 100));
        experiments.add(switchback(509, "sb_no_cluster_unit", "unit_absent", -559038737, 1234567890, 3,
            ANCHOR_PERIOD_0, 0, 100));
        // eligibility-0 draws 2 under every allowed count, so a fallback from 1000 to any of them fails.
        experiments.add(switchback(517, "sb_cluster_count_1000", "unit_store", -559038737, 1234567890, 3,
            ANCHOR_PERIOD_0, 0, 1000));
        experiments.add(switchback(510, "sb_dedup", "unit_serving", -559038737, 1234567890, 3, ANCHOR_PERIOD_0, 0, 100));
        // The period-0 draw for serving-9 is 2, so fullOnVariant 1 tells full-on apart from the draw.
        experiments.add(switchback(511, "sb_full_on", "unit_serving", -559038737, 1234567890, 3, ANCHOR_PERIOD_0, 1, 100));
        experiments.add(switchback(512, "sb_override", "unit_serving", -559038737, 1234567890, 3,
            ANCHOR_PERIOD_0, 0, 100));
        experiments.add(switchback(513, "sb_payload", "unit_serving", -559038737, 1234567890, 3, ANCHOR_PERIOD_0, 0, 100));
        Experiment attributeCity = switchback(515, "sb_attr_city", "unit_serving", -559038737, 1234567890, 3,
            ANCHOR_PERIOD_0, 0, 100);
        set(attributeCity, "switchbackClusterAttribute", "city");
        experiments.add(attributeCity);
        Experiment attributeMissing = switchback(516, "sb_attr_missing", "unit_serving", -559038737, 1234567890,
            3, ANCHOR_PERIOD_0, 0, 100);
        set(attributeMissing, "switchbackClusterAttribute", "zone");
        experiments.add(attributeMissing);
        Experiment unitBaseline = switchback(514, "sb_unit_baseline", "unit_serving", -559038737, 1234567890, 3,
            null, 0, null);
        set(unitBaseline, "randomizationType", null);
        set(unitBaseline, "switchbackClusterUnitType", null);
        set(unitBaseline, "switchbackPeriodMs", null);
        set(unitBaseline, "switchbackDesignType", null);
        experiments.add(unitBaseline);

        ContextData contextData = new ContextData();
        contextData.experiments = experiments.toArray(new Experiment[0]);

        List<Exposure> exposures = new CopyOnWriteArrayList<>();
        ContextEventLogger logger = (context, type, data) -> {
            if (type == ContextEventLogger.EventType.Exposure && data instanceof Exposure exposure) {
                exposures.add(exposure);
            }
        };

        ContextConfig contextConfig = ContextConfig.create()
            .setUnit("unit_p0_a2", "p0-a2-a-28")
            .setUnit("unit_p0_a3", "p0-a3-a-15")
            .setUnit("unit_p0_a10", "p0-a10-a-2")
            .setUnit("unit_p1_a2", "p1-a2-a-64")
            .setUnit("unit_p1_a3", "p1-a3-a-2")
            .setUnit("unit_p1_a10", "p1-a10-a-4")
            .setUnit("unit_store", "eligibility-0")
            .setUnit("unit_serving", "serving-9")
            .setPublishDelay(-1)
            .setRefreshInterval(0);

        List<PublishEvent> published = new CopyOnWriteArrayList<>();
        ABSmartlyConfig sdkConfig = ABSmartlyConfig.create()
            .setContextDataProvider(new DummyContextDataProvider())
            .setContextEventHandler((context, event) -> {
                published.add(event);
                return CompletableFuture.completedFuture(null);
            })
            .setContextEventLogger(logger);

        Context context = ABSmartly.create(sdkConfig).createContextWith(contextConfig, contextData);
        context.setAttribute("city", "city-29");
        context.waitUntilReady();
        if (!context.isReady() || context.isFailed()) {
            System.out.println(PREFIX + " FAILED: context not ready (failed=" + context.isFailed() + ")");
            return false;
        }

        try {
            return expect(context, exposures, "period 0, 2 arms", "sb_p0_a2", 1, served(501, "sb_p0_a2", 1))
                && expect(context, exposures, "period 0, 3 arms", "sb_p0_a3", 1, served(502, "sb_p0_a3", 1))
                && expect(context, exposures, "period 0, 10 arms", "sb_p0_a10", 1, served(503, "sb_p0_a10", 1))
                && expect(context, exposures, "period 1, 2 arms", "sb_p1_a2", 1, served(504, "sb_p1_a2", 1))
                && expect(context, exposures, "period 1, 3 arms", "sb_p1_a3", 1, served(505, "sb_p1_a3", 1))
                && expect(context, exposures, "period 1, 10 arms", "sb_p1_a10", 3, served(506, "sb_p1_a10", 3))
                && expect(context, exposures, "pre-anchor", "sb_pre_anchor", 0,
                    new Exposure(507, "sb_pre_anchor", "unit_store", 0, 0, true, false, false, false, false, false))
                && expect(context, exposures, "pre-anchor repeat", "sb_pre_anchor", 0)
                && expect(context, exposures, "missing anchor", "sb_no_anchor", 0,
                    new Exposure(508, "sb_no_anchor", "unit_store", 0, 0, true, false, false, false, false, false))
                && expect(context, exposures, "cluster unit not set", "sb_no_cluster_unit", 0,
                    new Exposure(509, "sb_no_cluster_unit", "unit_absent", 0, 0, false, true, false, false, false,
                        false))
                && expect(context, exposures, "cluster count 1000", "sb_cluster_count_1000", 0,
                    new Exposure(517, "sb_cluster_count_1000", "unit_store", 0, 0, true, false, false, false, false,
                        false))
                && expect(context, exposures, "same period", "sb_dedup", 2,
                    new Exposure(510, "sb_dedup", "unit_serving", 2, 0, true, true, false, false, false, false))
                && expect(context, exposures, "same period repeat", "sb_dedup", 2)
                && expect(context, exposures, "attribute source", "sb_attr_city", 1,
                    new Exposure(515, "sb_attr_city", "unit_serving", 1, 0, true, true, false, false, false, false))
                && expect(context, exposures, "attribute missing", "sb_attr_missing", 0,
                    new Exposure(516, "sb_attr_missing", "unit_serving", 0, 0, true, false, false, false, false,
                        false))
                && expect(context, exposures, "full-on", "sb_full_on", 1,
                    new Exposure(511, "sb_full_on", "unit_serving", 1, 0, true, true, false, true, false, false))
                && checkOverride(context, exposures)
                && checkPayloadShape(context, exposures, published);
        } finally {
            context.close();
        }
    }

    // Mirrors the payload scenario: expose and publish a unit baseline, then expose and publish a
    // switchback experiment on the same unit. The switchback exposure and its publish event must
    // have exactly the baseline's fields at every depth, and the published exposure must carry
    // the served values.
    private static boolean checkPayloadShape(Context context, List<Exposure> exposures,
            List<PublishEvent> published) throws Exception {
        // Drain the exposures queued by the earlier checks so each publish below carries one.
        context.publish();

        int before = exposures.size();
        context.getTreatment("sb_unit_baseline");
        PublishEvent baselineEvent = publishOne(context, published);
        context.getTreatment("sb_payload");
        PublishEvent switchbackEvent = publishOne(context, published);
        List<Exposure> emitted = new ArrayList<>(exposures.subList(before, exposures.size()));
        if (emitted.size() != 2 || baselineEvent == null || switchbackEvent == null) {
            System.out.println(PREFIX + " check payload shape FAILED: expected 2 exposures and 2 publish events, got "
                + emitted + " and " + baselineEvent + ", " + switchbackEvent);
            return false;
        }

        ObjectMapper mapper = new ObjectMapper();
        String exposureMismatch = shapeMismatch(mapper.valueToTree(emitted.get(0)), mapper.valueToTree(emitted.get(1)),
            "exposure");
        if (exposureMismatch != null) {
            System.out.println(PREFIX + " check payload shape FAILED: " + exposureMismatch);
            return false;
        }

        Exposure unitServed = new Exposure(514, "sb_unit_baseline", "unit_serving", 1, 0, true, true, false, false,
            false, false);
        Exposure switchbackServed = new Exposure(513, "sb_payload", "unit_serving", 2, 0, true, true, false, false,
            false, false);
        if (!publishedExactly(baselineEvent, unitServed) || !publishedExactly(switchbackEvent, switchbackServed)) {
            System.out.println(PREFIX + " check published exposures FAILED: baseline " + baselineEvent
                + ", switchback " + switchbackEvent);
            return false;
        }

        // Compare the bytes the SDK's own serializer would send, not the Java objects.
        DefaultContextEventSerializer serializer = new DefaultContextEventSerializer();
        JsonNode baselineJson = mapper.readTree(serializer.serialize(baselineEvent));
        JsonNode switchbackJson = mapper.readTree(serializer.serialize(switchbackEvent));
        String publishMismatch = shapeMismatch(baselineJson, switchbackJson, "publish");
        if (publishMismatch == null) {
            publishMismatch = serializedMismatch(baselineJson, unitServed, mapper);
        }
        if (publishMismatch == null) {
            publishMismatch = serializedMismatch(switchbackJson, switchbackServed, mapper);
        }
        if (publishMismatch != null) {
            System.out.println(PREFIX + " check publish payload FAILED: " + publishMismatch);
            return false;
        }
        return true;
    }

    private static final List<String> SERIALIZED_EXPOSURE_FIELDS = Arrays.asList("id", "name", "unit", "variant",
        "assigned", "eligible", "overridden", "fullOn", "custom", "audienceMismatch");

    // The serialized bytes, not just the PublishEvent object, must carry the served values: one
    // exposure equal to expected in every field but a positive integral exposedAt, published hashed
    // with the serving unit. Null when they do.
    static String serializedMismatch(JsonNode event, Exposure expected, ObjectMapper mapper) {
        if (!event.path("hashed").asBoolean(false)) {
            return "publish.hashed is " + event.get("hashed");
        }
        JsonNode exposures = event.path("exposures");
        if (!exposures.isArray() || exposures.size() != 1) {
            return "publish.exposures is " + exposures;
        }
        JsonNode actual = exposures.get(0);
        JsonNode wanted = mapper.valueToTree(expected);
        for (String field : SERIALIZED_EXPOSURE_FIELDS) {
            if (!wanted.path(field).equals(actual.path(field))) {
                return "publish.exposures[0]." + field + " is " + actual.get(field) + ", expected " + wanted.get(field);
            }
        }
        JsonNode exposedAt = actual.path("exposedAt");
        if (!exposedAt.isIntegralNumber() || exposedAt.asLong() <= 0) {
            return "publish.exposures[0].exposedAt is " + exposedAt;
        }
        for (JsonNode unit : event.path("units")) {
            if ("unit_serving".equals(unit.path("type").asText(null))
                    && SERVING_UNIT_HASH.equals(unit.path("uid").asText(null))) {
                return null;
            }
        }
        return "publish.units lacks the hashed serving unit: " + event.get("units");
    }

    private static PublishEvent publishOne(Context context, List<PublishEvent> published) {
        int before = published.size();
        context.publish();
        return published.size() == before + 1 ? published.get(before) : null;
    }

    // The publish event must carry exactly one exposure, equal to expected in every field but
    // exposedAt, which must be set, and must publish the hashed serving unit.
    private static boolean publishedExactly(PublishEvent event, Exposure expected) {
        if (!event.hashed || event.exposures == null || event.exposures.length != 1 || event.units == null) {
            return false;
        }
        boolean unitMatches = false;
        for (Unit unit : event.units) {
            unitMatches |= "unit_serving".equals(unit.type) && SERVING_UNIT_HASH.equals(unit.uid);
        }
        return servedAs(event.exposures[0], expected) && unitMatches;
    }

    // Every Exposure field equals expected, except exposedAt, which must be set.
    private static boolean servedAs(Exposure actual, Exposure expected) {
        return actual.id == expected.id && expected.name.equals(actual.name)
            && expected.unit.equals(actual.unit) && actual.variant == expected.variant
            && actual.assigned == expected.assigned && actual.eligible == expected.eligible
            && actual.overridden == expected.overridden && actual.fullOn == expected.fullOn
            && actual.custom == expected.custom && actual.audienceMismatch == expected.audienceMismatch
            && actual.exposedAt > 0;
    }

    // Object keys must match exactly and arrays must have equal length, recursively; scalar values
    // are checked separately.
    private static String shapeMismatch(JsonNode baseline, JsonNode actual, String path) {
        if (baseline.isObject() != actual.isObject() || baseline.isArray() != actual.isArray()) {
            return path + ": baseline is " + baseline.getNodeType() + ", got " + actual.getNodeType();
        }
        if (baseline.isObject()) {
            Set<String> baselineKeys = new TreeSet<>();
            baseline.fieldNames().forEachRemaining(baselineKeys::add);
            Set<String> actualKeys = new TreeSet<>();
            actual.fieldNames().forEachRemaining(actualKeys::add);
            if (!baselineKeys.equals(actualKeys)) {
                return path + ": baseline fields " + baselineKeys + ", got " + actualKeys;
            }
            for (String key : baselineKeys) {
                String mismatch = shapeMismatch(baseline.get(key), actual.get(key), path + "." + key);
                if (mismatch != null) {
                    return mismatch;
                }
            }
        } else if (baseline.isArray()) {
            if (baseline.size() != actual.size()) {
                return path + ": baseline length " + baseline.size() + ", got " + actual.size();
            }
            for (int i = 0; i < baseline.size(); i++) {
                String mismatch = shapeMismatch(baseline.get(i), actual.get(i), path + "[" + i + "]");
                if (mismatch != null) {
                    return mismatch;
                }
            }
        }
        return null;
    }

    private static boolean checkOverride(Context context, List<Exposure> exposures) {
        // The period-0 draw for serving-9 is 2, so the override serves 1.
        context.setOverride("sb_override", 1);
        return expect(context, exposures, "override", "sb_override", 1,
            new Exposure(512, "sb_override", "unit_serving", 1, 0, false, true, true, false, false, false));
    }

    private static Experiment switchback(int id, String name, String unitType, int seedHi, int seedLo, int arms,
            Long startAt, int fullOnVariant, Integer clusterCount) throws Exception {
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
        set(experiment, "switchbackClusterCount", clusterCount);
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

    // One getTreatment call: the treatment and every exposure it emits must match exactly.
    private static boolean expect(Context context, List<Exposure> exposures, String check,
            String experimentName, int expectedTreatment, Exposure... expectedExposures) {
        int before = exposures.size();
        int treatment = context.getTreatment(experimentName);
        List<Exposure> emitted = new ArrayList<>(exposures.subList(before, exposures.size()));

        boolean matches = treatment == expectedTreatment && emitted.size() == expectedExposures.length;
        for (int i = 0; matches && i < expectedExposures.length; i++) {
            matches = servedAs(emitted.get(i), expectedExposures[i]);
        }
        if (!matches) {
            System.out.println(PREFIX + " check " + check + " FAILED: expected treatment=" + expectedTreatment
                + " exposures=" + Arrays.toString(expectedExposures) + ", got treatment=" + treatment
                + " exposures=" + emitted);
        }
        return matches;
    }
}
