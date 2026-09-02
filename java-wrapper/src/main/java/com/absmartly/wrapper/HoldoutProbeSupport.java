package com.absmartly.wrapper;

import com.absmartly.sdk.json.Experiment;
import com.absmartly.sdk.json.ExperimentApplication;
import com.absmartly.sdk.json.ExperimentVariant;

import java.lang.reflect.Field;
import java.util.List;

/**
 * Reflection-based fixture builders and treatment/exposure assertions shared by {@link
 * HoldoutSelfTest} (two-arm holdout semantics, scenarios 203-208/221) and {@link
 * HoldoutArmsSelfTest} (three-arm semantics, scenarios 214-216). Kept in one place so both probes
 * construct wire fixtures identically and fail closed identically; see HoldoutSelfTest's class
 * javadoc for why reflection is used at all.
 */
final class HoldoutProbeSupport {
    private HoldoutProbeSupport() {}

    /**
     * Builds one experiment. holdoutIds is set via reflection (see class javadoc); every other
     * field is a direct compile-time reference since these have existed on Experiment since
     * before holdout support.
     */
    static Experiment buildExperiment(int id, String name, String unitType, int seedHi, int seedLo,
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
    static Object buildHoldoutEntry(Class<?> holdoutType, int id, String name, String unitType, int seedHi,
            int seedLo, double[] split, String holdoutTypeName, String[] variantNames) throws Exception {
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

    static void setIfPresent(Object target, String fieldName, Object value) throws Exception {
        Field field = findField(target.getClass(), fieldName);
        if (field != null && field.getType().isInstance(value)) {
            field.set(target, value);
        }
    }

    static Field findField(Class<?> type, String name) {
        try {
            return type.getField(name);
        } catch (NoSuchFieldException e) {
            return null;
        }
    }

    /**
     * Calls getTreatment(experimentName) exactly once, then requires BOTH the returned treatment
     * AND the exact ordered id/name/variant sequence of exposure events appended by that single
     * call to match the expected sequence byte-for-byte. Order and every variant matter (see
     * HoldoutSelfTest's original javadoc for why) - a check that only compared id/name/count could
     * pass against an SDK that resolves the right ids with the wrong variants or the wrong order.
     */
    static boolean verifyTreatmentAndExposures(String logPrefix, String checkLabel,
            com.absmartly.sdk.Context context, List<ProbeExposure> exposures, String experimentName,
            int expectedTreatment, ProbeExposure... expectedSequence) {
        int before = exposures.size();
        int treatment = context.getTreatment(experimentName);
        List<ProbeExposure> newExposures = exposures.subList(before, exposures.size());

        if (treatment != expectedTreatment || !matchesSequence(newExposures, expectedSequence)) {
            System.out.println(logPrefix + " check " + checkLabel + " FAILED: expected treatment="
                + expectedTreatment + " with exposure sequence " + java.util.Arrays.toString(expectedSequence)
                + ", got treatment=" + treatment + " exposures=" + newExposures);
            return false;
        }
        return true;
    }

    static boolean matchesSequence(List<ProbeExposure> actual, ProbeExposure[] expected) {
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
    static final class ProbeExposure {
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
}
