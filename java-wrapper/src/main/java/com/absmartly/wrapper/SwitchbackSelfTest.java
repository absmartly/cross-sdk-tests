package com.absmartly.wrapper;

import com.absmartly.sdk.ABSmartly;
import com.absmartly.sdk.ABSmartlyConfig;
import com.absmartly.sdk.Context;
import com.absmartly.sdk.ContextConfig;
import com.absmartly.sdk.ContextEventLogger;
import com.absmartly.sdk.DefaultContextEventSerializer;
import com.absmartly.sdk.json.ContextData;
import com.absmartly.sdk.json.Experiment;
import com.absmartly.sdk.json.Exposure;
import com.absmartly.sdk.json.PublishEvent;
import com.absmartly.sdk.json.Unit;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.concurrent.CompletableFuture;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * Behavioral self-test behind the `switchback` capability (R33, cluster-defined switchbacks). It
 * mirrors the Switchback scenario group: a sample of the platform golden vectors through
 * SwitchbackAssigner, then switchback serving through a real Context on an R33 payload (ordered
 * switchbackClusters, switchbackClusterValues) deserialized from JSON on the same clock-free grid
 * the scenarios use. Fields are read by reflection so the wrapper still compiles against
 * pre-switchback core-api; the verdict always comes from observed treatments and exposures.
 */
final class SwitchbackSelfTest {
    private SwitchbackSelfTest() {}

    private static final String PREFIX = "[switchback probe]";

    // Grid constants shared with generate_switchback_scenarios.py: with a 400,000 h period "now"
    // stays in period 0 until 2069.
    private static final long PERIOD_MS = 400_000L * 3_600_000L;
    private static final long ANCHOR_PERIOD_0 = 1_704_067_200_000L;

    // hashUnit("serving-9"): base64url-no-pad MD5, as published in PublishEvent.units.
    private static final String SERVING_UNIT_HASH = "r5uxZRoi-JuM5yYJa0kmSQ";

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
        {"market_1", "1", "9223372036854775807", "2", "1"},
    };

    // {clusterUnit, cycleIndex, seed, expectedPermutation}, from the same fixture, including
    // Long.MIN_VALUE/MAX_VALUE cycles.
    private static final Object[][] PERMUTATION_CASES = {
        {"market_1", "0", "0", new int[] {0, 3, 7, 5, 8, 2, 6, 4, 1, 9}},
        {"market_1", "-9223372036854775808", "0", new int[] {1, 0}},
        {"market_1", "9223372036854775807", "1", new int[] {0, 1}},
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

    // The R33 wire model: an ordered switchbackClusters map and switchbackClusterValues, and none of
    // the abandoned Phase 1E bucket fields. A core-api that still has switchbackClusterCount serves
    // the bucket design, which the Collector no longer emits.
    static boolean hasR33WireModel(Class<?> experimentClass) {
        return HoldoutProbeSupport.findField(experimentClass, "randomizationType") != null
            && HoldoutProbeSupport.findField(experimentClass, "switchbackClusters") != null
            && HoldoutProbeSupport.findField(experimentClass, "switchbackClusterValues") != null
            && HoldoutProbeSupport.findField(experimentClass, "switchbackStartAt") != null
            && HoldoutProbeSupport.findField(experimentClass, "switchbackPeriodMs") != null
            && HoldoutProbeSupport.findField(experimentClass, "switchbackClusterCount") == null;
    }

    // The probe payload, as the Collector serves it (R33). Built as JSON and deserialized the way
    // the wrapper deserializes scenario payloads, so the cluster map's key order is exercised too.
    // Every expected variant is a platform golden vector. The 10-arm ones are chosen (with the
    // Python reference) to differ from every wrong model for both context uids (serving-9,
    // probe-user): the hashed key, the uid as key, the hashed uid as key, a Phase 1E bucket and the
    // sticky unit assignment. sb_payload (3 arms, seed 1) exists for the payload shape and is not
    // discriminating on its own: its bucket draw equals the correct one (sb_agree catches that).
    //   assignSwitchback("market_1", 0, 42, 10) = 3: hashed key 5, bucket 8; serving-9 uid 8,
    //     hashed uid 8, sticky 5; probe-user uid 9, hashed uid 9, sticky 7.
    //   assignSwitchback("123456789", 0, 42, 10) = 6: "123456789.0" 4, "1.23456789E8" 7,
    //     "1.23456789e+08" 0, hashed key 0, bucket 4; serving-9 uid 8, hashed uid 8, sticky 5.
    //   assignSwitchback("market_1", 0, 1, 3) = 1 (payload shape; hashed key 2, bucket 1 (caught by
    //     sb_agree); serving-9 uid 0, hashed uid 0, sticky 0).
    static String probePayload() throws Exception {
        ObjectMapper mapper = new ObjectMapper();
        ObjectNode data = mapper.createObjectNode();
        ArrayNode experiments = data.putArray("experiments");
        experiments.add(switchback(mapper, 521, "sb_agree", 0, 42, 10,
            new String[][] {{"unit_serving", "city"}, {"unit_user", "profile/city"}}));
        experiments.add(switchback(mapper, 522, "sb_declared_order", 0, 42, 10,
            new String[][] {{"unit_user", "profile/city"}, {"unit_serving", "city"}}));
        experiments.add(switchback(mapper, 523, "sb_disagree", 0, 42, 10,
            new String[][] {{"unit_serving", "city"}, {"unit_user", "profile/zone"}}));
        experiments.add(switchback(mapper, 524, "sb_whole_number", 0, 42, 10,
            new String[][] {{"unit_serving", "store_number"}}));
        // switchbackClusterValues [] admits every value, like null.
        ObjectNode admitAll = switchback(mapper, 528, "sb_allow_empty", 0, 42, 10,
            new String[][] {{"unit_serving", "city"}});
        admitAll.putArray("switchbackClusterValues");
        experiments.add(admitAll);
        ObjectNode filtered = switchback(mapper, 525, "sb_value_filtered", 0, 1, 10,
            new String[][] {{"unit_serving", "city"}});
        filtered.putArray("switchbackClusterValues").add("market_2");
        experiments.add(filtered);
        // Only the abandoned Phase 1E fields: no cluster map, so the legacy ineligible control.
        ObjectNode legacy = switchback(mapper, 526, "sb_phase1e_only", 0, 1, 10, null);
        legacy.put("switchbackClusterUnitType", "unit_serving");
        legacy.put("switchbackClusterCount", 10);
        legacy.put("switchbackClusterAttribute", "city");
        experiments.add(legacy);
        experiments.add(switchback(mapper, 513, "sb_payload", 0, 1, 3, new String[][] {{"unit_serving", "city"}}));
        experiments.add(unit(mapper, 514, "sb_unit_baseline"));
        ObjectNode withFields = unit(mapper, 527, "sb_unit_with_fields");
        withFields.put("randomizationType", "unit");
        ObjectNode clusters = withFields.putObject("switchbackClusters");
        clusters.put("unit_serving", "city");
        withFields.putArray("switchbackClusterValues").add("market_1");
        withFields.put("switchbackStartAt", ANCHOR_PERIOD_0);
        withFields.put("switchbackPeriodMs", PERIOD_MS);
        withFields.put("switchbackDesignType", "balanced_block");
        experiments.add(withFields);
        return mapper.writeValueAsString(data);
    }

    private static boolean checkContextServing() throws Exception {
        if (!hasR33WireModel(Experiment.class)) {
            System.out.println(PREFIX + " FAILED: linked core-api has no R33 switchback wire model "
                + "(switchbackClusters + switchbackClusterValues, no switchbackClusterCount)");
            return false;
        }

        ObjectMapper mapper = new ObjectMapper();
        ContextData contextData = mapper.readValue(probePayload(), ContextData.class);
        if (!clusterKeys(contextData, "sb_declared_order").equals(Arrays.asList("unit_user", "unit_serving"))) {
            System.out.println(PREFIX + " FAILED: switchbackClusters lost its declared order: "
                + clusterKeys(contextData, "sb_declared_order"));
            return false;
        }

        List<Exposure> exposures = new CopyOnWriteArrayList<>();
        ContextEventLogger logger = (context, type, data) -> {
            if (type == ContextEventLogger.EventType.Exposure && data instanceof Exposure exposure) {
                exposures.add(exposure);
            }
        };

        // unit_user is set first, so declared order, not context order, must pick the serving unit.
        ContextConfig contextConfig = ContextConfig.create()
            .setUnit("unit_user", "probe-user")
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
        context.waitUntilReady();
        if (!context.isReady() || context.isFailed()) {
            System.out.println(PREFIX + " FAILED: context not ready (failed=" + context.isFailed() + ")");
            return false;
        }
        context.setAttribute("city", "market_1");
        context.setAttribute("profile", Map.of("city", "market_1", "zone", "market_2"));
        context.setAttribute("store_number", 123456789);

        try {
            boolean served = expect(context, exposures, "agreement", "sb_agree", 3,
                    new Exposure(521, "sb_agree", "unit_serving", 3, 0, true, true, false, false, false, false))
                && expect(context, exposures, "agreement repeat", "sb_agree", 3)
                && expect(context, exposures, "declared order", "sb_declared_order", 3,
                    new Exposure(522, "sb_declared_order", "unit_user", 3, 0, true, true, false, false, false, false))
                && expect(context, exposures, "disagreement", "sb_disagree", 0)
                && expect(context, exposures, "whole number", "sb_whole_number", 6,
                    new Exposure(524, "sb_whole_number", "unit_serving", 6, 0, true, true, false, false, false, false))
                && expect(context, exposures, "value filtered out", "sb_value_filtered", 0)
                && expect(context, exposures, "empty allow-list admits all", "sb_allow_empty", 3,
                    new Exposure(528, "sb_allow_empty", "unit_serving", 3, 0, true, true, false, false, false, false))
                && expect(context, exposures, "Phase 1E fields only", "sb_phase1e_only", 0);
            if (!served) {
                return false;
            }
            PublishEvent baselineEvent = checkPayloadShape(context, exposures, published);
            return baselineEvent != null
                && checkUnitExperimentInert(context, exposures, published, mapper, baselineEvent);
        } finally {
            context.close();
        }
    }

    private static List<String> clusterKeys(ContextData data, String experimentName) throws Exception {
        Field field = HoldoutProbeSupport.findField(Experiment.class, "switchbackClusters");
        for (Experiment experiment : data.experiments) {
            if (experimentName.equals(experiment.name)) {
                Object clusters = field.get(experiment);
                return (clusters instanceof Map<?, ?> map)
                    ? new ArrayList<>(map.keySet().stream().map(String::valueOf).toList()) : List.of();
            }
        }
        return List.of();
    }

    // A unit experiment carrying every R33 field (randomizationType "unit") must be served, exposed
    // and published exactly like the plain baseline on the same unit and seeds: the serialized
    // publish bytes are identical once id, name and exposedAt are aligned.
    private static boolean checkUnitExperimentInert(Context context, List<Exposure> exposures,
            List<PublishEvent> published, ObjectMapper mapper, PublishEvent baseline) throws Exception {
        int before = exposures.size();
        if (context.getTreatment("sb_unit_with_fields") != 1) {
            System.out.println(PREFIX + " check unit inertness FAILED: sb_unit_with_fields did not serve 1");
            return false;
        }
        PublishEvent event = publishOne(context, published);
        if (event == null || exposures.size() != before + 1 || !servedAs(exposures.get(before),
                new Exposure(527, "sb_unit_with_fields", "unit_serving", 1, 0, true, true, false, false, false, false))) {
            System.out.println(PREFIX + " check unit inertness FAILED: exposures " + exposures.subList(before,
                exposures.size()) + ", publish " + event);
            return false;
        }
        DefaultContextEventSerializer serializer = new DefaultContextEventSerializer();
        ObjectNode baselineJson = (ObjectNode) mapper.readTree(serializer.serialize(baseline));
        ObjectNode actualJson = (ObjectNode) mapper.readTree(serializer.serialize(event));
        for (ObjectNode json : new ObjectNode[] {baselineJson, actualJson}) {
            json.remove("publishedAt");
            ObjectNode exposure = (ObjectNode) json.path("exposures").path(0);
            exposure.put("id", 0);
            exposure.put("name", "");
            exposure.put("exposedAt", 0);
        }
        if (!mapper.writeValueAsString(baselineJson).equals(mapper.writeValueAsString(actualJson))) {
            System.out.println(PREFIX + " check unit inertness FAILED: publish " + actualJson + " differs from "
                + baselineJson);
            return false;
        }
        return true;
    }

    // Mirrors the payload scenario: expose and publish a unit baseline, then expose and publish a
    // switchback experiment on the same unit. The switchback exposure and its publish event must
    // have exactly the baseline's fields at every depth, and the published exposure must carry
    // the served values. Returns the baseline publish event, or null when a check fails.
    private static PublishEvent checkPayloadShape(Context context, List<Exposure> exposures,
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
            return null;
        }

        ObjectMapper mapper = new ObjectMapper();
        String exposureMismatch = shapeMismatch(mapper.valueToTree(emitted.get(0)), mapper.valueToTree(emitted.get(1)),
            "exposure");
        if (exposureMismatch != null) {
            System.out.println(PREFIX + " check payload shape FAILED: " + exposureMismatch);
            return null;
        }

        Exposure unitServed = new Exposure(514, "sb_unit_baseline", "unit_serving", 1, 0, true, true, false, false,
            false, false);
        // assignSwitchback("market_1", 0, 1, 3) = 1; bucket 1 too (caught by sb_agree).
        Exposure switchbackServed = new Exposure(513, "sb_payload", "unit_serving", 1, 0, true, true, false, false,
            false, false);
        if (!publishedExactly(baselineEvent, unitServed) || !publishedExactly(switchbackEvent, switchbackServed)) {
            System.out.println(PREFIX + " check published exposures FAILED: baseline " + baselineEvent
                + ", switchback " + switchbackEvent);
            return null;
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
            return null;
        }
        return baselineEvent;
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

    private static ObjectNode base(ObjectMapper mapper, int id, String name, int seedHi, int seedLo, int arms) {
        ObjectNode experiment = mapper.createObjectNode();
        experiment.put("id", id);
        experiment.put("name", name);
        experiment.put("iteration", 1);
        experiment.put("unitType", "unit_serving");
        experiment.put("seedHi", seedHi);
        experiment.put("seedLo", seedLo);
        ArrayNode split = experiment.putArray("split");
        for (int i = 0; i < arms; i++) {
            split.add(1.0 / arms);
        }
        experiment.put("trafficSeedHi", 1);
        experiment.put("trafficSeedLo", 2);
        experiment.putArray("trafficSplit").add(0).add(1);
        experiment.put("fullOnVariant", 0);
        experiment.putArray("applications").addObject().put("name", "website");
        ArrayNode variants = experiment.putArray("variants");
        for (int i = 0; i < arms; i++) {
            variants.addObject().put("name", String.valueOf((char) ('A' + i))).putNull("config");
        }
        experiment.put("audienceStrict", false);
        return experiment;
    }

    // clusters is the switchbackClusters map in declared order; null omits it.
    private static ObjectNode switchback(ObjectMapper mapper, int id, String name, int seedHi, int seedLo, int arms,
            String[][] clusters) {
        ObjectNode experiment = base(mapper, id, name, seedHi, seedLo, arms);
        experiment.put("randomizationType", "switchback");
        if (clusters != null) {
            ObjectNode map = experiment.putObject("switchbackClusters");
            for (String[] entry : clusters) {
                map.put(entry[0], entry[1]);
            }
            experiment.put("unitType", clusters[0][0]);
        }
        experiment.put("switchbackStartAt", ANCHOR_PERIOD_0);
        experiment.put("switchbackPeriodMs", PERIOD_MS);
        experiment.put("switchbackDesignType", "balanced_block");
        experiment.put("switchbackEstimand", "global_average_treatment_effect");
        return experiment;
    }

    // The unit baseline: seeds whose 3-arm unit draw for serving-9 is 1.
    private static ObjectNode unit(ObjectMapper mapper, int id, String name) {
        return base(mapper, id, name, -559038737, 1234567890, 3);
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
