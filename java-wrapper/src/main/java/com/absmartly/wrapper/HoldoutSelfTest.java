package com.absmartly.wrapper;

import com.absmartly.sdk.ContextConfig;
import com.absmartly.sdk.ContextEventLogger;
import com.absmartly.sdk.deprecated.ABSmartly;
import com.absmartly.sdk.deprecated.ABSmartlyConfig;
import com.absmartly.sdk.json.ContextData;
import com.absmartly.sdk.json.Experiment;
import com.absmartly.sdk.json.ExperimentApplication;
import com.absmartly.sdk.json.ExperimentVariant;
import com.absmartly.sdk.json.Exposure;

import java.lang.reflect.Array;
import java.lang.reflect.Field;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * Behavioral self-test for the `holdouts` capability.
 *
 * A capability flag that is merely "the wire model has a holdoutIds field" is not proof the
 * underlying SDK implements holdout SEMANTICS: a build can carry the field, deserialize it
 * without error, and still resolve treatments/exposures as if holdouts did not exist - or worse,
 * implement a shape that satisfies the field's presence while disagreeing with the current
 * contract on what gets exposed. Either way the wrapper would advertise holdouts:true and fail
 * every scenario that depends on suppression. The only way to know the SDK actually implements
 * the contract is to exercise it: construct a minimal in-memory context around a single holdout
 * that is guaranteed (by construction of the unit id relative to the holdout's split/seed) to
 * hold out the covered experiment's unit, then check the two externally-observable invariants of
 * holdout suppression -
 *   1. the covered experiment's treatment resolves to the control variant (0), and
 *   2. evaluating it produces exactly one exposure - for the holdout itself, not the covered
 *      experiment - so a suppressed assignment never masquerades as an ordinary one.
 * Only a pass on both counts justifies advertising the capability; any exception, missing field,
 * or observable mismatch means the SDK cannot be trusted to honor the contract and the
 * capability is reported false.
 *
 * The fixture's holdout entry is built via reflection (rather than a compile-time field
 * reference) purely so this same wrapper source compiles against both pre-holdout and
 * holdout-aware core-api releases; reflection here is only ever used to ASSEMBLE the input, never
 * to decide the verdict - the verdict always comes from observing real getTreatment()/exposure
 * output of the linked SDK.
 */
final class HoldoutSelfTest {
    private HoldoutSelfTest() {}

    private static final String EXPERIMENT_NAME = "__holdout_self_test_experiment";
    private static final String HOLDOUT_NAME = "__holdout_self_test_holdout";
    private static final int EXPERIMENT_ID = 1;
    private static final int HOLDOUT_ID = 11;
    private static final String UNIT_TYPE = "session_id";
    // Same unit id used by the orchestrator's scenario 203 fixture: relative to the holdout's
    // seed/split below it lands in the holdout's variant 0 (the held-out arm).
    private static final String UNIT_ID = "e791e240fcd3df7d238cfc285f475e8152fcc0ec";

    static boolean run() {
        try {
            return runUnsafe();
        } catch (Exception e) {
            System.out.println("[holdouts probe] behavioral self-test FAILED with exception: " + e);
            return false;
        }
    }

    private static boolean runUnsafe() throws Exception {
        ContextData contextData = buildFixture();
        if (contextData == null) {
            System.out.println("[holdouts probe] behavioral self-test FAILED: linked core-api has no "
                + "holdout wire model (Experiment.holdoutIds / ContextData.holdouts absent) - cannot "
                + "even construct the fixture, so holdout semantics cannot be present either");
            return false;
        }

        List<Map<String, Object>> exposures = new CopyOnWriteArrayList<>();
        ContextEventLogger logger = (context, type, data) -> {
            if (type == ContextEventLogger.EventType.Exposure && data instanceof Exposure) {
                Exposure exposure = (Exposure) data;
                Map<String, Object> record = new HashMap<>();
                record.put("id", exposure.id);
                record.put("name", exposure.name);
                record.put("variant", exposure.variant);
                exposures.add(record);
            }
        };

        ContextConfig contextConfig = ContextConfig.create()
            .setUnit(UNIT_TYPE, UNIT_ID)
            .setPublishDelay(-1)
            .setRefreshInterval(0);

        ABSmartlyConfig sdkConfig = ABSmartlyConfig.create()
            .setContextDataProvider(new DummyContextDataProvider())
            .setContextPublisher((context, event) -> java8.util.concurrent.CompletableFuture.completedFuture(null))
            .setContextEventLogger(logger);

        ABSmartly sdk = ABSmartly.create(sdkConfig);
        com.absmartly.sdk.Context context = sdk.createContextWith(contextConfig, contextData);
        context.waitUntilReady();

        if (!context.isReady() || context.isFailed()) {
            System.out.println("[holdouts probe] behavioral self-test FAILED: context not ready ("
                + "failed=" + context.isFailed() + ")");
            return false;
        }

        int treatment = context.getTreatment(EXPERIMENT_NAME);
        context.close();

        // Invariant 1: the held-out unit gets the control value for the covered experiment.
        if (treatment != 0) {
            System.out.println("[holdouts probe] behavioral self-test FAILED: expected control "
                + "treatment (0) for held-out unit, got " + treatment);
            return false;
        }

        // Invariant 2: exactly one exposure fired, for the holdout itself - never one for the
        // covered experiment, which must be suppressed rather than exposed.
        if (exposures.size() != 1) {
            System.out.println("[holdouts probe] behavioral self-test FAILED: expected exactly 1 "
                + "exposure (the holdout's), got " + exposures.size() + ": " + exposures);
            return false;
        }
        Map<String, Object> onlyExposure = exposures.get(0);
        boolean isHoldoutExposure = Integer.valueOf(HOLDOUT_ID).equals(onlyExposure.get("id"))
            && HOLDOUT_NAME.equals(onlyExposure.get("name"));
        if (!isHoldoutExposure) {
            System.out.println("[holdouts probe] behavioral self-test FAILED: sole exposure was not "
                + "the holdout's own - covered experiment was not suppressed: " + onlyExposure);
            return false;
        }

        System.out.println("[holdouts probe] behavioral self-test PASSED: held-out unit suppressed "
            + "to control with exactly one ordinary holdout exposure");
        return true;
    }

    /**
     * Builds the scenario-203-shaped fixture: one covered experiment referencing one holdout that
     * suppresses it. The covered Experiment's holdoutIds field and the ContextData's holdouts
     * field are both looked up reflectively, since neither exists on a pre-holdout core-api - a
     * missing field there means the wire model itself carries no holdout support, so the fixture
     * cannot be built and the probe fails closed (returns null, treated as unsupported).
     */
    private static ContextData buildFixture() throws Exception {
        Field holdoutIdsField = findField(Experiment.class, "holdoutIds");
        Field holdoutsField = findField(ContextData.class, "holdouts");
        if (holdoutIdsField == null || holdoutsField == null) {
            return null;
        }

        Class<?> holdoutElementType = holdoutsField.getType().getComponentType();
        Object holdout = buildHoldoutEntry(holdoutElementType);
        if (holdout == null) {
            return null;
        }

        Experiment experiment = new Experiment();
        experiment.id = EXPERIMENT_ID;
        experiment.name = EXPERIMENT_NAME;
        experiment.iteration = 1;
        experiment.unitType = UNIT_TYPE;
        experiment.seedHi = 100;
        experiment.seedLo = 200;
        experiment.split = new double[] {0.5, 0.5};
        experiment.trafficSeedHi = 1;
        experiment.trafficSeedLo = 2;
        experiment.trafficSplit = new double[] {0, 1};
        experiment.fullOnVariant = 0;
        experiment.applications = new ExperimentApplication[] {new ExperimentApplication("website")};
        experiment.variants = new ExperimentVariant[] {
            new ExperimentVariant("A", null),
            new ExperimentVariant("B", null)
        };
        holdoutIdsField.set(experiment, new int[] {HOLDOUT_ID});

        ContextData data = new ContextData();
        data.experiments = new Experiment[] {experiment};
        Object holdoutsArray = Array.newInstance(holdoutElementType, 1);
        Array.set(holdoutsArray, 0, holdout);
        holdoutsField.set(data, holdoutsArray);
        return data;
    }

    /**
     * Constructs one holdout entry of whatever type ContextData.holdouts actually holds on the
     * linked core-api. Every holdout wire shape this SDK has ever shipped carries at minimum
     * id/seedHi/seedLo/split (the fields the variant assigner needs); anything else (name,
     * iteration, unitType, applications, variants, a holdoutType discriminator, ...) is set
     * opportunistically when present so the fixture matches the live wire shape as closely as
     * possible, but their absence does not fail fixture construction.
     */
    private static Object buildHoldoutEntry(Class<?> holdoutType) throws Exception {
        Object holdout = holdoutType.getDeclaredConstructor().newInstance();

        Field idField = findField(holdoutType, "id");
        Field seedHiField = findField(holdoutType, "seedHi");
        Field seedLoField = findField(holdoutType, "seedLo");
        Field splitField = findField(holdoutType, "split");
        if (idField == null || seedHiField == null || seedLoField == null || splitField == null) {
            return null;
        }
        idField.set(holdout, HOLDOUT_ID);
        seedHiField.set(holdout, 13);
        seedLoField.set(holdout, 111);
        splitField.set(holdout, new double[] {0.1, 0.9});

        setIfPresent(holdout, "name", HOLDOUT_NAME);
        setIfPresent(holdout, "iteration", 1);
        setIfPresent(holdout, "unitType", UNIT_TYPE);
        setIfPresent(holdout, "trafficSeedHi", 0);
        setIfPresent(holdout, "trafficSeedLo", 0);
        setIfPresent(holdout, "trafficSplit", new double[] {0, 1});
        setIfPresent(holdout, "fullOnVariant", 0);
        setIfPresent(holdout, "applications", new ExperimentApplication[0]);
        setIfPresent(holdout, "variants", new ExperimentVariant[] {
            new ExperimentVariant("A", null),
            new ExperimentVariant("B", null)
        });
        setIfPresent(holdout, "holdoutType", "full");

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
