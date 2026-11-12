package com.absmartly.wrapper;

import com.absmartly.sdk.*;
import com.absmartly.sdk.json.*;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CompletableFuture;

/** Fail closed unless both audience removal and audience-only edits invalidate mismatches. */
final class OmittedTargetingSelfTest {
    private static Boolean cached;

    static synchronized boolean run() {
        if (cached == null) {
            try {
                cached = battery();
            } catch (Throwable error) {
                cached = false;
            }
        }
        return cached;
    }

    private static String audience(String country) {
        return "{\"filter\":[{\"eq\":[{\"var\":\"country\"},{\"value\":\"" + country + "\"}]}]}";
    }

    private static ContextData data(boolean strict, String audience) {
        Experiment exp = new Experiment();
        exp.id = 1;
        exp.name = "probe_targeting";
        exp.iteration = 1;
        exp.unitType = "session_id";
        exp.split = new double[] {0, 1};
        exp.trafficSplit = new double[] {0, 1};
        exp.applications = new ExperimentApplication[] {new ExperimentApplication("website")};
        exp.variants = new ExperimentVariant[] {new ExperimentVariant("A", null), new ExperimentVariant("B", null)};
        exp.audience = audience;
        exp.audienceStrict = strict;
        ContextData data = new ContextData();
        data.experiments = new Experiment[] {exp};
        return data;
    }

    private static boolean battery() {
        boolean passed = true;
        for (boolean strict : new boolean[] {false, true}) {
            for (boolean rulesSignal : new boolean[] {false, true}) {
                List<Exposure> exposures = new CopyOnWriteArrayList<>();
                DummyContextDataProvider provider = new DummyContextDataProvider();
                ABSmartly sdk = ABSmartly.create(ABSmartlyConfig.create()
                    .setContextDataProvider(provider)
                    .setContextEventHandler((ctx, event) -> CompletableFuture.completedFuture(null))
                    .setContextEventLogger((ctx, type, event) -> {
                        if (type == ContextEventLogger.EventType.Exposure) exposures.add((Exposure) event);
                    }));
                Context context = sdk.createContextWith(ContextConfig.create().setUnit("session_id", "test123")
                    .setPublishDelay(-1).setRefreshInterval(0), data(strict, audience("US")));
                try {
                    context.waitUntilReady();
                    context.setAttribute("country", "CA");
                    int before = context.getTreatment("probe_targeting");
                    provider.setNextData(data(strict, rulesSignal ? audience("CA") : null));
                    context.refresh();
                    if (!rulesSignal) context.setAttribute("unrelated", "changed");
                    int after = context.getTreatment("probe_targeting");
                    passed &= before == (strict ? 0 : 1) && after == 1 && exposures.size() == 2
                        && exposures.get(0).audienceMismatch && exposures.get(1).assigned
                        && !exposures.get(1).audienceMismatch;
                } finally {
                    context.close();
                }
            }
        }
        return passed;
    }
}
