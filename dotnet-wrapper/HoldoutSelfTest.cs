using System.Reflection;
using ABSmartly;
using ABSmartly.Models;

// Behavioral self-test for the `holdouts` / `holdout_arms` wrapper capabilities. Mirrors
// javascript-wrapper/holdoutSelfTest.js (and java-wrapper's HoldoutSelfTest/HoldoutArmsSelfTest):
// build a real ContextData from fixture values ported from the scenario battery
// (test_scenarios_complete.json), create a real context through the SDK's public
// CreateContextWith, call GetTreatment and require BOTH the returned variant and the exact ordered
// id/name/variant sequence of exposure events that call logs.
//
// The holdout model fields (ContextData.Holdouts, Experiment.HoldoutIds/HoldoutType) are set by
// reflection, so the wrapper still compiles against a dotnet-sdk build that predates holdouts; such
// a build lacks the properties and the self-test fails closed (the capability reports false).
// Run once per process; the result is cached. Any exception reports false.
public static class HoldoutSelfTest
{
    private static readonly Lazy<bool> Holdouts = new(() => RunSafely("holdouts", RunHoldoutsBattery));

    private static readonly Lazy<bool> HoldoutArms = new(() =>
    {
        // Three-arm support cannot exist without basic holdout support.
        if (!Holdouts.Value)
        {
            Console.WriteLine("[holdout_arms probe] skipped: base holdouts battery did not pass");
            return false;
        }

        return RunSafely("holdout_arms", RunHoldoutArmsBattery);
    });

    public static bool RunHoldouts() => Holdouts.Value;

    public static bool RunHoldoutArms() => HoldoutArms.Value;

    private static bool RunSafely(string label, Func<bool> battery)
    {
        try
        {
            return battery();
        }
        catch (Exception e)
        {
            Console.WriteLine($"[{label} probe] behavioral self-test FAILED with error: {e}");
            return false;
        }
    }

    private sealed class ExposureLog : IContextEventLogger
    {
        public readonly List<(int Id, string Name, int Variant)> Exposures = new();

        public void HandleEvent(IContext context, EventType eventType, object data)
        {
            if (eventType == EventType.Exposure && data is Exposure exposure)
                lock (Exposures) Exposures.Add((exposure.Id, exposure.Name, exposure.Variant));
        }
    }

    private static void SetRequired(object target, string property, object value)
    {
        var info = target.GetType().GetProperty(property, BindingFlags.Instance | BindingFlags.Public)
                   ?? throw new InvalidOperationException(
                       $"{target.GetType().Name}.{property} is missing: this dotnet-sdk build has no holdouts");
        info.SetValue(target, value);
    }

    private static Experiment BuildExperiment(int id, string name, string unitType, int seedHi, int seedLo,
        double[] split, int fullOnVariant, string[] variantNames, int[] holdoutIds)
    {
        var experiment = new Experiment
        {
            Id = id,
            Name = name,
            Iteration = 1,
            UnitType = unitType,
            SeedHi = seedHi,
            SeedLo = seedLo,
            Split = split,
            TrafficSeedHi = 1,
            TrafficSeedLo = 2,
            TrafficSplit = new[] { 0.0, 1.0 },
            FullOnVariant = fullOnVariant,
            Applications = new[] { new ExperimentApplication { Name = "website" } },
            Variants = variantNames.Select(n => new ExperimentVariant { Name = n }).ToArray()
        };
        SetRequired(experiment, "HoldoutIds", holdoutIds);
        return experiment;
    }

    private static Experiment BuildHoldout(int id, string name, string unitType, int seedHi, int seedLo,
        double[] split, string[] variantNames, string holdoutType)
    {
        var holdout = new Experiment
        {
            Id = id,
            Name = name,
            Iteration = 1,
            UnitType = unitType,
            SeedHi = seedHi,
            SeedLo = seedLo,
            Split = split,
            TrafficSplit = new[] { 0.0, 1.0 },
            Applications = Array.Empty<ExperimentApplication>(),
            Variants = variantNames.Select(n => new ExperimentVariant { Name = n }).ToArray()
        };
        SetRequired(holdout, "HoldoutType", holdoutType);
        return holdout;
    }

    private static (IContext Context, ExposureLog Log) CreateContext(Experiment[] experiments, Experiment[] holdouts,
        Dictionary<string, string> units)
    {
        var data = new ABSmartly.Models.ContextData(experiments);
        SetRequired(data, "Holdouts", holdouts);

        var log = new ExposureLog();
        var sdk = new ABsmartly.Sdk(new DummyHttpClientFactory(),
            new ABSmartlyServiceConfiguration
            {
                Endpoint = "http://127.0.0.1:9", ApiKey = "dummy", Application = "test", Environment = "test"
            },
            new ABsmartlyConfig { ContextEventLogger = log, ContextPublisher = new CustomPublisher(null, null) });
        var config = new ContextConfig
        {
            PublishDelay = TimeSpan.FromMilliseconds(-1),
            RefreshInterval = TimeSpan.FromMilliseconds(0)
        };
        foreach (var unit in units) config.Units[unit.Key] = unit.Value;

        var context = sdk.CreateContextWith(config, data);
        if (!context.IsReady() || context.IsFailed())
            throw new InvalidOperationException($"context not ready (failed={context.IsFailed()})");
        return (context, log);
    }

    private static bool Verify(string prefix, string check, IContext context, ExposureLog log, string experimentName,
        int expectedTreatment, params (int Id, string Name, int Variant)[] expected)
    {
        var before = log.Exposures.Count;
        var treatment = context.GetTreatment(experimentName);
        var actual = log.Exposures.Skip(before).ToList();

        if (treatment == expectedTreatment && actual.SequenceEqual(expected)) return true;

        Console.WriteLine($"{prefix} check {check} FAILED: expected treatment={expectedTreatment} with exposures " +
                          $"[{string.Join(", ", expected)}], got treatment={treatment} exposures [{string.Join(", ", actual)}]");
        return false;
    }

    // Two-arm battery, ported from scenarios 203-208 and 221 (checks A-G). Synthetic ids (301-308
    // experiments, 401-408 holdouts) let all checks share one context; every value that drives
    // assignment (unit type, seeds, split, full-on variant, unit id) is the scenario's own.
    private static bool RunHoldoutsBattery()
    {
        const string heldOutUnitType = "session_id";
        // seed 13/111 split [0.1,0.9]: variant 0 (held out).
        const string heldOutUid = "e791e240fcd3df7d238cfc285f475e8152fcc0ec";
        const string notHeldOutUnitType = "session_id_b";
        // Same seed and split: variant 1 (not held out).
        const string notHeldOutUid = "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3";
        // Installed only after the first resolution (check G, scenario 221).
        const string lateUnitType = "user_id";

        string[] ab = { "A", "B" };
        string[] abc = { "A", "B", "C" };
        double[] half = { 0.5, 0.5 };
        double[] tenNinety = { 0.1, 0.9 };

        var experiments = new[]
        {
            BuildExperiment(301, "probe_a_covered", heldOutUnitType, 100, 200, half, 0, ab, new[] { 401 }),
            BuildExperiment(302, "probe_b_covered", notHeldOutUnitType, 100, 200, half, 0, ab, new[] { 402 }),
            BuildExperiment(304, "probe_c_union", heldOutUnitType, 100, 200, half, 0, ab, new[] { 404, 405 }),
            BuildExperiment(305, "probe_d_covered", heldOutUnitType, 100, 200, half, 0, ab, new[] { 406 }),
            BuildExperiment(306, "probe_d_uncovered_sibling", heldOutUnitType, 100, 200, half, 0, ab, null),
            BuildExperiment(307, "probe_e_dangling_plus_valid", heldOutUnitType, 100, 200, half, 0, ab,
                new[] { 999, 407 }),
            BuildExperiment(303, "probe_f_fullon", heldOutUnitType, 100, 200, half, 2, abc, new[] { 403 }),
            BuildExperiment(308, "probe_g_late_unit", lateUnitType, 100, 200, new[] { 0.5, 0.5, 0.0 }, 2, abc,
                new[] { 408 })
        };
        var holdouts = new[]
        {
            BuildHoldout(401, "probe_a_holdout", heldOutUnitType, 13, 111, tenNinety, ab, "full"),
            BuildHoldout(402, "probe_b_holdout", notHeldOutUnitType, 13, 111, tenNinety, ab, "full"),
            BuildHoldout(404, "probe_c_holdout_low", heldOutUnitType, 1, 222, tenNinety, ab, "full"),
            BuildHoldout(405, "probe_c_holdout_high", heldOutUnitType, 13, 111, tenNinety, ab, "full"),
            BuildHoldout(406, "probe_d_holdout", heldOutUnitType, 13, 111, tenNinety, ab, "full"),
            BuildHoldout(407, "probe_e_holdout", heldOutUnitType, 13, 111, tenNinety, ab, "full"),
            BuildHoldout(403, "probe_f_holdout", heldOutUnitType, 13, 111, tenNinety, ab, "full"),
            BuildHoldout(408, "probe_g_holdout", lateUnitType, 1, 222, tenNinety, ab, "full")
        };

        var (context, log) = CreateContext(experiments, holdouts, new Dictionary<string, string>
        {
            [heldOutUnitType] = heldOutUid,
            [notHeldOutUnitType] = notHeldOutUid
        });
        const string p = "[holdouts probe]";

        if (!Verify(p, "A (mirrors 203)", context, log, "probe_a_covered", 0, (401, "probe_a_holdout", 0)))
            return false;
        if (!Verify(p, "B (mirrors 204)", context, log, "probe_b_covered", 0,
                (302, "probe_b_covered", 0), (402, "probe_b_holdout", 1)))
            return false;
        if (!Verify(p, "C (mirrors 205)", context, log, "probe_c_union", 0,
                (404, "probe_c_holdout_low", 1), (405, "probe_c_holdout_high", 0)))
            return false;
        if (!Verify(p, "D (mirrors 206, covered)", context, log, "probe_d_covered", 0, (406, "probe_d_holdout", 0)))
            return false;
        if (!Verify(p, "D (mirrors 206, uncovered sibling)", context, log, "probe_d_uncovered_sibling", 1,
                (306, "probe_d_uncovered_sibling", 1)))
            return false;
        if (!Verify(p, "E (mirrors 207)", context, log, "probe_e_dangling_plus_valid", 0, (407, "probe_e_holdout", 0)))
            return false;
        if (!Verify(p, "F (mirrors 208)", context, log, "probe_f_fullon", 0, (403, "probe_f_holdout", 0)))
            return false;

        // G (mirrors 221): a peek before the unit exists exposes nothing; once the unit is set, the
        // treatment publishes the experiment's exposure and the holdout's, each once.
        var before = log.Exposures.Count;
        var peeked = context.PeekTreatment("probe_g_late_unit");
        if (peeked != 2 || log.Exposures.Count != before)
        {
            Console.WriteLine($"{p} check G (mirrors 221, pre-unit peek) FAILED: treatment={peeked}, " +
                              $"exposures={log.Exposures.Count - before}");
            return false;
        }

        context.SetUnit(lateUnitType, heldOutUid);
        if (!Verify(p, "G (mirrors 221, post-unit treatment)", context, log, "probe_g_late_unit", 2,
                (308, "probe_g_late_unit", 2), (408, "probe_g_holdout", 1)))
            return false;

        Console.WriteLine($"{p} behavioral self-test PASSED: all 7 checks mirroring scenarios 203-208 and 221 passed");
        return true;
    }

    // Three-arm battery, ported from scenarios 214-216 (same ids as the scenarios).
    private static bool RunHoldoutArmsBattery()
    {
        const string unitType = "session_id";
        const string uid = "e791e240fcd3df7d238cfc285f475e8152fcc0ec";
        double[] covered = { 0.5, 0.5 };
        double[] threeArm = { 0.3, 0.3, 0.4 };
        string[] ab = { "A", "B" };
        string[] abc = { "A", "B", "C" };

        var experiments = new[]
        {
            BuildExperiment(601, "arms_0_non_fullon", unitType, 100, 200, covered, 0, ab, new[] { 621 }),
            BuildExperiment(602, "arms_0_fullon", unitType, 100, 200, covered, 2, abc, new[] { 621 }),
            BuildExperiment(603, "arms_1_non_fullon", unitType, 100, 200, covered, 0, ab, new[] { 622 }),
            BuildExperiment(604, "arms_1_fullon", unitType, 100, 200, covered, 2, abc, new[] { 622 }),
            BuildExperiment(605, "arms_2_non_fullon", unitType, 100, 200, covered, 0, ab, new[] { 623 }),
            BuildExperiment(606, "arms_2_fullon", unitType, 100, 200, covered, 2, abc, new[] { 623 })
        };
        var holdouts = new[]
        {
            // seedLo 1, 3 and 0 put the unit in arm 0, 1 and 2.
            BuildHoldout(621, "arms_holdout_0", unitType, 0, 1, threeArm, abc, "all_full_on"),
            BuildHoldout(622, "arms_holdout_1", unitType, 0, 3, threeArm, abc, "all_full_on"),
            BuildHoldout(623, "arms_holdout_2", unitType, 0, 0, threeArm, abc, "all_full_on")
        };

        var (context, log) = CreateContext(experiments, holdouts, new Dictionary<string, string> { [unitType] = uid });
        const string p = "[holdout_arms probe]";

        if (!Verify(p, "0 non-full-on (mirrors 214)", context, log, "arms_0_non_fullon", 0, (621, "arms_holdout_0", 0)))
            return false;
        if (!Verify(p, "0 full-on (mirrors 214)", context, log, "arms_0_fullon", 0)) return false;
        if (!Verify(p, "1 non-full-on (mirrors 215)", context, log, "arms_1_non_fullon", 0, (622, "arms_holdout_1", 1)))
            return false;
        if (!Verify(p, "1 full-on (mirrors 215)", context, log, "arms_1_fullon", 2, (604, "arms_1_fullon", 2)))
            return false;
        if (!Verify(p, "2 non-full-on (mirrors 216)", context, log, "arms_2_non_fullon", 1,
                (605, "arms_2_non_fullon", 1), (623, "arms_holdout_2", 2)))
            return false;
        if (!Verify(p, "2 full-on (mirrors 216)", context, log, "arms_2_fullon", 2, (606, "arms_2_fullon", 2)))
            return false;

        Console.WriteLine($"{p} behavioral self-test PASSED: all 3 arm checks mirroring scenarios 214-216 passed");
        return true;
    }
}
