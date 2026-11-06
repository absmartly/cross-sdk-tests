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

import java.lang.reflect.Field;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

import static com.absmartly.wrapper.HoldoutProbeSupport.findField;

/**
 * Behavioral self-tests for the {@code assignmentRules} and {@code semverOperators} wrapper
 * capabilities (cross-SDK scenario groups "Custom Assignment Rules" and "Semver Operators",
 * ported from javascript-sdk#67 / java-sdk#17). Mirrors {@link HoldoutSelfTest}: build real
 * ContextData fixtures against the linked core-api, drive getTreatment and assert BOTH the
 * returned variant and the exposure's flags. A build without custom assignment rules (no
 * {@code Experiment.assignmentRules} / {@code Exposure.ruleOverride}) or without the semver
 * operators simply cannot reproduce the expected behaviour, so each probe fails closed and the
 * capability is reported {@code false} rather than crashing the wrapper.
 */
final class AssignmentRulesSelfTest {
    private AssignmentRulesSelfTest() {}

    private static final String UNIT_TYPE = "session_id";
    private static final String UID = "svk-unit-1";
    private static final double[] ONE_HOT_SPLIT = {0.0, 1.0};
    private static final String[] TWO_VARIANTS = {"A", "B"};

    private static volatile Boolean cachedRules;
    private static volatile Boolean cachedSemver;

    static boolean runAssignmentRules() {
        Boolean result = cachedRules;
        if (result == null) {
            synchronized (AssignmentRulesSelfTest.class) {
                result = cachedRules;
                if (result == null) {
                    result = runSafely("assignmentRules", AssignmentRulesSelfTest::runRulesBattery);
                    cachedRules = result;
                }
            }
        }
        return result;
    }

    static boolean runSemverOperators() {
        Boolean result = cachedSemver;
        if (result == null) {
            synchronized (AssignmentRulesSelfTest.class) {
                result = cachedSemver;
                if (result == null) {
                    result = runSafely("semverOperators", AssignmentRulesSelfTest::runSemverBattery);
                    cachedSemver = result;
                }
            }
        }
        return result;
    }

    private interface Battery {
        boolean run() throws Exception;
    }

    private static boolean runSafely(String label, Battery battery) {
        try {
            return battery.run();
        } catch (Throwable t) {
            System.out.println("[" + label + " probe] behavioral self-test FAILED with throwable: " + t);
            return false;
        }
    }

    private static Experiment buildExperiment(int id, String name, String assignmentRules, String audience,
            boolean audienceStrict) throws Exception {
        Field assignmentRulesField = findField(Experiment.class, "assignmentRules");
        if (assignmentRulesField == null) {
            return null; // linked core-api predates custom assignment rules
        }
        Experiment experiment = new Experiment();
        experiment.id = id;
        experiment.name = name;
        experiment.iteration = 1;
        experiment.unitType = UNIT_TYPE;
        experiment.seedHi = 0;
        experiment.seedLo = 0;
        experiment.split = ONE_HOT_SPLIT;
        experiment.trafficSeedHi = 0;
        experiment.trafficSeedLo = 0;
        experiment.trafficSplit = new double[] {0, 1};
        experiment.fullOnVariant = 0;
        experiment.applications = new ExperimentApplication[] {new ExperimentApplication("website")};
        experiment.variants = new ExperimentVariant[] {
            new ExperimentVariant(TWO_VARIANTS[0], null), new ExperimentVariant(TWO_VARIANTS[1], null)
        };
        experiment.audience = audience;
        experiment.audienceStrict = audienceStrict;
        if (assignmentRules != null) {
            assignmentRulesField.set(experiment, assignmentRules);
        }
        return experiment;
    }

    private static final class ProbeExposure {
        final int variant;
        final boolean assigned;
        final boolean ruleOverride;
        final boolean audienceMismatch;

        ProbeExposure(int variant, boolean assigned, boolean ruleOverride, boolean audienceMismatch) {
            this.variant = variant;
            this.assigned = assigned;
            this.ruleOverride = ruleOverride;
            this.audienceMismatch = audienceMismatch;
        }

        @Override
        public String toString() {
            return "{variant=" + variant + ", assigned=" + assigned + ", ruleOverride=" + ruleOverride
                + ", audienceMismatch=" + audienceMismatch + "}";
        }
    }

    private static com.absmartly.sdk.Context createContext(ContextData contextData,
            List<ProbeExposure> exposures) {
        Field ruleOverrideField = findField(Exposure.class, "ruleOverride");
        ContextEventLogger logger = (context, type, data) -> {
            if (type == ContextEventLogger.EventType.Exposure && data instanceof Exposure) {
                Exposure exposure = (Exposure) data;
                boolean ruleOverride = false;
                if (ruleOverrideField != null) {
                    try {
                        ruleOverride = ruleOverrideField.getBoolean(exposure);
                    } catch (IllegalAccessException ignored) {
                        // leave false; the probe will fail its ruleOverride assertion
                    }
                }
                exposures.add(new ProbeExposure(exposure.variant, exposure.assigned, ruleOverride,
                    exposure.audienceMismatch));
            }
        };
        ContextConfig contextConfig = ContextConfig.create()
            .setUnit(UNIT_TYPE, UID)
            .setPublishDelay(-1)
            .setRefreshInterval(0);
        ABSmartlyConfig sdkConfig = ABSmartlyConfig.create()
            .setContextDataProvider(new DummyContextDataProvider())
            .setContextEventHandler((context, event) ->
                java8.util.concurrent.CompletableFuture.completedFuture(null))
            .setContextEventLogger(logger);
        ABSmartly sdk = ABSmartly.create(sdkConfig);
        com.absmartly.sdk.Context context = sdk.createContextWith(contextConfig, contextData);
        context.waitUntilReady();
        return context;
    }

    private static boolean runRulesBattery() throws Exception {
        String prefix = "[assignmentRules probe]";
        // A single assign rule: variant 1 with no conditions, so it always matches.
        String rules = "{\"rules\":[{\"name\":\"r\",\"type\":\"assign\",\"variant\":1}]}";
        Experiment experiment = buildExperiment(1, "probe_rule", rules, null, false);
        if (experiment == null) {
            System.out.println(prefix + " FAILED: linked core-api has no Experiment.assignmentRules field");
            return false;
        }
        if (findField(Exposure.class, "ruleOverride") == null) {
            System.out.println(prefix + " FAILED: linked core-api has no Exposure.ruleOverride field");
            return false;
        }
        ContextData contextData = new ContextData();
        contextData.experiments = new Experiment[] {experiment};

        List<ProbeExposure> exposures = new CopyOnWriteArrayList<>();
        com.absmartly.sdk.Context context = createContext(contextData, exposures);
        try {
            if (!context.isReady() || context.isFailed()) {
                System.out.println(prefix + " FAILED: context not ready (failed=" + context.isFailed() + ")");
                return false;
            }
            int variant = context.getTreatment("probe_rule");
            if (variant != 1 || exposures.size() != 1) {
                System.out.println(prefix + " FAILED: expected treatment 1 with one exposure, got "
                    + variant + " / " + exposures);
                return false;
            }
            ProbeExposure exposure = exposures.get(0);
            // A rule win: variant 1, ruleOverride:true, assigned:false (excluded from analysis).
            if (exposure.variant != 1 || !exposure.ruleOverride || exposure.assigned) {
                System.out.println(prefix + " FAILED: expected variant 1, ruleOverride:true, assigned:false, got "
                    + exposure);
                return false;
            }
        } finally {
            context.close();
        }
        System.out.println(prefix + " PASSED");
        return true;
    }

    private static boolean runSemverBattery() throws Exception {
        String prefix = "[semverOperators probe]";
        // 1.10.0 > 1.9.0 is true only with numeric (not lexicographic) identifier comparison.
        String audience = "{\"filter\":[{\"semver_gt\":[{\"var\":\"app_version\"},{\"value\":\"1.9.0\"}]}]}";

        Experiment matchExp = buildExperiment(1, "probe_semver", null, audience, true);
        if (matchExp == null) {
            System.out.println(prefix + " FAILED: linked core-api has no Experiment.assignmentRules field "
                + "(cannot build the fixture)");
            return false;
        }
        ContextData matchData = new ContextData();
        matchData.experiments = new Experiment[] {matchExp};
        List<ProbeExposure> matchExposures = new CopyOnWriteArrayList<>();
        com.absmartly.sdk.Context matchContext = createContext(matchData, matchExposures);
        try {
            matchContext.setAttribute("app_version", "1.10.0");
            int variant = matchContext.getTreatment("probe_semver");
            if (variant != 1 || matchExposures.size() != 1 || matchExposures.get(0).audienceMismatch) {
                System.out.println(prefix + " FAILED (match): expected variant 1, audienceMismatch:false, got "
                    + variant + " / " + matchExposures);
                return false;
            }
        } finally {
            matchContext.close();
        }

        Experiment missExp = buildExperiment(1, "probe_semver", null, audience, true);
        ContextData missData = new ContextData();
        missData.experiments = new Experiment[] {missExp};
        List<ProbeExposure> missExposures = new CopyOnWriteArrayList<>();
        com.absmartly.sdk.Context missContext = createContext(missData, missExposures);
        try {
            missContext.setAttribute("app_version", "1.2.0");
            int variant = missContext.getTreatment("probe_semver");
            if (variant != 0 || missExposures.size() != 1 || !missExposures.get(0).audienceMismatch) {
                System.out.println(prefix + " FAILED (mismatch): expected variant 0, audienceMismatch:true, got "
                    + variant + " / " + missExposures);
                return false;
            }
        } finally {
            missContext.close();
        }
        System.out.println(prefix + " PASSED");
        return true;
    }
}
