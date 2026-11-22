package com.absmartly.wrapper;

import com.absmartly.sdk.ABsmartlyClient;
import com.absmartly.sdk.ContextState;
import com.absmartly.sdk.Exposure;
import com.absmartly.sdk.event.ContextEvent;
import com.absmartly.sdk.event.EventListener;
import com.absmartly.sdk.event.ExposureEvent;
import com.absmartly.sdk.internal.json.ContextPayload;
import com.absmartly.sdk.internal.json.Experiment;
import com.absmartly.sdk.internal.json.ExperimentApplication;
import com.absmartly.sdk.internal.json.ExperimentVariant;

import java.lang.reflect.Field;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

import static com.absmartly.wrapper.HoldoutProbeSupport.findField;

/**
 * Behavioral self-tests for the {@code assignmentRules} and {@code semverOperators} wrapper
 * capabilities (cross-SDK scenario groups "Custom Assignment Rules" and "Semver Operators",
 * ported from javascript-sdk#67 / java-sdk#17). Mirrors {@link HoldoutSelfTest}: build real
 * ContextPayload fixtures against the linked core-api, drive treatment and assert BOTH the
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

    // Each probe context gets its own in-memory client; closing the context closes it.
    private static final class ProbeContext implements AutoCloseable {
        final ABsmartlyClient client = SdkSupport.inMemoryClient();
        final com.absmartly.sdk.Context context;

        ProbeContext(ContextPayload contextData, List<ProbeExposure> exposures) {
            EventListener listener = event -> {
                if (event.type() == ContextEvent.Type.EXPOSURE) {
                    Exposure exposure = ((ExposureEvent) event).exposure();
                    exposures.add(new ProbeExposure(exposure.variant(), exposure.assigned(), exposure.ruleOverride(),
                        exposure.audienceMismatch()));
                }
            };
            context = SdkSupport.context(client, contextData, java.util.Collections.singletonMap(UNIT_TYPE, UID),
                listener);
        }

        @Override
        public void close() {
            try {
                context.close();
            } finally {
                client.close();
            }
        }
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
        ContextPayload contextData = new ContextPayload();
        contextData.experiments = new Experiment[] {experiment};

        List<ProbeExposure> exposures = new CopyOnWriteArrayList<>();
        try (ProbeContext probe = new ProbeContext(contextData, exposures)) {
            com.absmartly.sdk.Context context = probe.context;
            if (context.state() != ContextState.READY) {
                System.out.println(prefix + " FAILED: context not ready (state=" + context.state() + ")");
                return false;
            }
            int variant = context.treatment("probe_rule");
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
        ContextPayload matchData = new ContextPayload();
        matchData.experiments = new Experiment[] {matchExp};
        List<ProbeExposure> matchExposures = new CopyOnWriteArrayList<>();
        try (ProbeContext probe = new ProbeContext(matchData, matchExposures)) {
            com.absmartly.sdk.Context matchContext = probe.context;
            matchContext.setAttribute("app_version", "1.10.0");
            int variant = matchContext.treatment("probe_semver");
            if (variant != 1 || matchExposures.size() != 1 || matchExposures.get(0).audienceMismatch) {
                System.out.println(prefix + " FAILED (match): expected variant 1, audienceMismatch:false, got "
                    + variant + " / " + matchExposures);
                return false;
            }
        }

        Experiment missExp = buildExperiment(1, "probe_semver", null, audience, true);
        ContextPayload missData = new ContextPayload();
        missData.experiments = new Experiment[] {missExp};
        List<ProbeExposure> missExposures = new CopyOnWriteArrayList<>();
        try (ProbeContext probe = new ProbeContext(missData, missExposures)) {
            com.absmartly.sdk.Context missContext = probe.context;
            missContext.setAttribute("app_version", "1.2.0");
            int variant = missContext.treatment("probe_semver");
            if (variant != 0 || missExposures.size() != 1 || !missExposures.get(0).audienceMismatch) {
                System.out.println(prefix + " FAILED (mismatch): expected variant 0, audienceMismatch:true, got "
                    + variant + " / " + missExposures);
                return false;
            }
        }
        System.out.println(prefix + " PASSED");
        return true;
    }
}
