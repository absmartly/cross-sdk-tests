using System.Globalization;
using System.Reflection;
using System.Text;
using System.Text.Json;
using ABSmartly;
using ABSmartly.Models;
using ABSmartly.Services;

// The switchback pure functions (dotnet-sdk's internal ABSmartly.Internal.SwitchbackAssigner),
// resolved by reflection so the wrapper still compiles against a dotnet-sdk build without
// switchbacks; there IsAvailable is false and the endpoints answer 501.
public static class SwitchbackAssignerAccess
{
    private static readonly MethodInfo? AssignMethod;
    private static readonly MethodInfo? BaseHashMethod;
    private static readonly MethodInfo? PermutationMethod;

    static SwitchbackAssignerAccess()
    {
        var type = typeof(Experiment).Assembly.GetType("ABSmartly.Internal.SwitchbackAssigner");
        const BindingFlags flags = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
        AssignMethod = type?.GetMethod("Assign", flags, null, new[] { typeof(string), typeof(long), typeof(long), typeof(int) }, null);
        BaseHashMethod = type?.GetMethod("BaseHash", flags, null, new[] { typeof(string), typeof(long), typeof(long) }, null);
        PermutationMethod = type?.GetMethod("FisherYatesPermutation", flags, null, new[] { typeof(int), typeof(int) }, null);
    }

    public static bool IsAvailable => AssignMethod != null && BaseHashMethod != null && PermutationMethod != null;

    public static int Assign(string clusterUnit, long periodIndex, long seed, int armCount) =>
        (int)Invoke(AssignMethod, clusterUnit, periodIndex, seed, armCount);

    public static int[] Permutation(string clusterUnit, long cycleIndex, long seed, int armCount) =>
        (int[])Invoke(PermutationMethod, armCount, (int)Invoke(BaseHashMethod, clusterUnit, cycleIndex, seed));

    private static object Invoke(MethodInfo? method, params object[] args)
    {
        if (method == null) throw new NotSupportedException("linked dotnet-sdk has no switchback assigner");
        try
        {
            return method.Invoke(null, args)!;
        }
        catch (TargetInvocationException e) when (e.InnerException != null)
        {
            throw e.InnerException;
        }
    }
}

// Behavioral self-test behind the `switchback` capability (R33, cluster-defined switchbacks),
// ported from java-wrapper SwitchbackSelfTest: a sample of the platform golden vectors through the
// SDK's assigner, then switchback serving through a real context on an R33 payload deserialized by
// the SDK's own deserializer (so the cluster map's key order is exercised too). The model fields are
// read by reflection, so the wrapper still compiles against a dotnet-sdk build without them; the
// verdict always comes from observed treatments, exposures and publish bytes. Run once per
// process; any exception reports false.
public static class SwitchbackSelfTest
{
    private const string Prefix = "[switchback probe]";

    // Grid constants shared with generate_switchback_scenarios.py: with a 400,000 h period "now"
    // stays in period 0 until 2069.
    private const long PeriodMs = 400_000L * 3_600_000L;
    private const long AnchorPeriod0 = 1_704_067_200_000L;

    // Md5.Hash("serving-9"), as published in PublishEvent.units.
    private const string ServingUnitHash = "r5uxZRoi-JuM5yYJa0kmSQ";

    // {clusterUnit, periodIndex, seed, armCount, expectedVariant}, from the platform golden vectors,
    // including long.MinValue/MaxValue periods and seeds.
    private static readonly string[][] AssignCases =
    {
        new[] { "market_1", "0", "0", "2", "0" },
        new[] { "market_1", "-9223372036854775808", "1", "2", "1" },
        new[] { "market_1", "9223372036854775807", "1", "2", "1" },
        new[] { "123456789", "0", "0", "2", "1" },
        new[] { "market_1", "10", "0", "3", "2" },
        new[] { "123456789", "-2", "-1", "3", "0" },
        new[] { "market_1", "5", "1", "10", "0" },
        new[] { "market_2", "-11", "-9223372036854775808", "10", "3" },
        new[] { "market_2", "9223372036854775807", "-9223372036854775808", "10", "2" },
        new[] { "market_1", "1", "9223372036854775807", "2", "1" },
    };

    // {clusterUnit, cycleIndex, seed, expectedPermutation}, from the same fixture.
    private static readonly (string Unit, string Cycle, string Seed, int[] Expected)[] PermutationCases =
    {
        ("market_1", "0", "0", new[] { 0, 3, 7, 5, 8, 2, 6, 4, 1, 9 }),
        ("market_1", "-9223372036854775808", "0", new[] { 1, 0 }),
        ("market_1", "9223372036854775807", "1", new[] { 0, 1 }),
        ("123456789", "0", "0", new[] { 2, 0, 1 }),
    };

    private static readonly Lazy<bool> Result = new(RunSafely);

    public static bool Run() => Result.Value;

    private static bool RunSafely()
    {
        try
        {
            if (!CheckGoldenSample() || !CheckContextServing()) return false;
            Console.WriteLine($"{Prefix} behavioral self-test PASSED");
            return true;
        }
        catch (Exception e)
        {
            Console.WriteLine($"{Prefix} behavioral self-test FAILED with error: {e}");
            return false;
        }
    }

    private static long L(string s) => long.Parse(s, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture);

    private static bool CheckGoldenSample()
    {
        if (!SwitchbackAssignerAccess.IsAvailable)
        {
            Console.WriteLine($"{Prefix} FAILED: linked dotnet-sdk has no SwitchbackAssigner");
            return false;
        }

        foreach (var c in AssignCases)
        {
            var actual = SwitchbackAssignerAccess.Assign(c[0], L(c[1]), L(c[2]), int.Parse(c[3], CultureInfo.InvariantCulture));
            if (actual != int.Parse(c[4], CultureInfo.InvariantCulture))
            {
                Console.WriteLine($"{Prefix} golden assign FAILED for [{string.Join(", ", c)}]: got {actual}");
                return false;
            }
        }

        foreach (var c in PermutationCases)
        {
            var actual = SwitchbackAssignerAccess.Permutation(c.Unit, L(c.Cycle), L(c.Seed), c.Expected.Length);
            if (!actual.SequenceEqual(c.Expected))
            {
                Console.WriteLine($"{Prefix} golden permutation FAILED for {c.Unit}/{c.Cycle}/{c.Seed}: expected " +
                                  $"[{string.Join(",", c.Expected)}], got [{string.Join(",", actual)}]");
                return false;
            }
        }

        return true;
    }

    // The R33 wire model: an ordered cluster map and an allow-list, and none of the abandoned Phase
    // 1E bucket fields.
    private static bool HasR33WireModel()
    {
        var t = typeof(Experiment);
        return t.GetProperty("RandomizationType") != null && t.GetProperty("SwitchbackClusters") != null &&
               t.GetProperty("SwitchbackClusterValues") != null && t.GetProperty("SwitchbackStartAt") != null &&
               t.GetProperty("SwitchbackPeriodMs") != null && t.GetProperty("SwitchbackClusterCount") == null;
    }

    // The probe payload, as the Collector serves it (R33); the same experiments and expected variants
    // as java-wrapper SwitchbackSelfTest.probePayload, every one a platform golden vector chosen to
    // differ from every wrong model (hashed key, uid as key, hashed uid, a Phase 1E bucket, the
    // sticky unit assignment).
    internal static string ProbePayload()
    {
        var experiments = new List<Dictionary<string, object?>>
        {
            Switchback(521, "sb_agree", 0, 42, 10, new[] { ("unit_serving", "city"), ("unit_user", "profile/city") }),
            Switchback(522, "sb_declared_order", 0, 42, 10, new[] { ("unit_user", "profile/city"), ("unit_serving", "city") }),
            Switchback(523, "sb_disagree", 0, 42, 10, new[] { ("unit_serving", "city"), ("unit_user", "profile/zone") }),
            Switchback(524, "sb_whole_number", 0, 42, 10, new[] { ("unit_serving", "store_number") }),
        };
        // switchbackClusterValues [] admits every value, like null.
        var admitAll = Switchback(528, "sb_allow_empty", 0, 42, 10, new[] { ("unit_serving", "city") });
        admitAll["switchbackClusterValues"] = Array.Empty<string>();
        experiments.Add(admitAll);
        var filtered = Switchback(525, "sb_value_filtered", 0, 1, 10, new[] { ("unit_serving", "city") });
        filtered["switchbackClusterValues"] = new[] { "market_2" };
        experiments.Add(filtered);
        // Only the abandoned Phase 1E fields: no cluster map, so the legacy ineligible control.
        var legacy = Switchback(526, "sb_phase1e_only", 0, 1, 10, null);
        legacy["switchbackClusterUnitType"] = "unit_serving";
        legacy["switchbackClusterCount"] = 10;
        legacy["switchbackClusterAttribute"] = "city";
        experiments.Add(legacy);
        experiments.Add(Switchback(513, "sb_payload", 0, 1, 3, new[] { ("unit_serving", "city") }));
        experiments.Add(Unit(514, "sb_unit_baseline"));
        var withFields = Unit(527, "sb_unit_with_fields");
        withFields["randomizationType"] = "unit";
        withFields["switchbackClusters"] = new Dictionary<string, string> { ["unit_serving"] = "city" };
        withFields["switchbackClusterValues"] = new[] { "market_1" };
        withFields["switchbackStartAt"] = AnchorPeriod0;
        withFields["switchbackPeriodMs"] = PeriodMs;
        withFields["switchbackDesignType"] = "balanced_block";
        experiments.Add(withFields);
        return JsonSerializer.Serialize(new Dictionary<string, object> { ["experiments"] = experiments });
    }

    private sealed class Log : IContextEventLogger
    {
        public readonly List<Exposure> Exposures = new();

        public void HandleEvent(IContext context, EventType eventType, object data)
        {
            if (eventType == EventType.Exposure && data is Exposure exposure)
                lock (Exposures) Exposures.Add(exposure);
        }
    }

    private sealed class CapturingPublisher : IContextPublisher
    {
        public readonly List<PublishEvent> Published = new();

        public Task PublishAsync(IContext context, PublishEvent publishEvent)
        {
            lock (Published) Published.Add(publishEvent);
            return Task.CompletedTask;
        }
    }

    private static bool CheckContextServing()
    {
        if (!HasR33WireModel())
        {
            Console.WriteLine($"{Prefix} FAILED: linked dotnet-sdk has no R33 switchback wire model " +
                              "(SwitchbackClusters + SwitchbackClusterValues, no SwitchbackClusterCount)");
            return false;
        }

        var data = new DefaultContextDataDeserializer().Deserialize(new MemoryStream(Encoding.UTF8.GetBytes(ProbePayload())));
        var order = ClusterKeys(data, "sb_declared_order");
        if (!order.SequenceEqual(new[] { "unit_user", "unit_serving" }))
        {
            Console.WriteLine($"{Prefix} FAILED: switchbackClusters lost its declared order: [{string.Join(", ", order)}]");
            return false;
        }

        var log = new Log();
        var publisher = new CapturingPublisher();
        var sdk = new ABsmartly.Sdk(new DummyHttpClientFactory(),
            new ABSmartlyServiceConfiguration
            {
                Endpoint = "http://127.0.0.1:9", ApiKey = "dummy", Application = "test", Environment = "test"
            },
            new ABsmartlyConfig { ContextEventLogger = log, ContextPublisher = publisher });
        // unit_user is set first, so declared order, not context order, must pick the serving unit.
        var config = new ContextConfig
        {
            PublishDelay = TimeSpan.FromMilliseconds(-1),
            RefreshInterval = TimeSpan.FromMilliseconds(0)
        };
        config.Units["unit_user"] = "probe-user";
        config.Units["unit_serving"] = "serving-9";

        var context = sdk.CreateContextWith(config, data);
        try
        {
            if (!context.IsReady() || context.IsFailed())
            {
                Console.WriteLine($"{Prefix} FAILED: context not ready (failed={context.IsFailed()})");
                return false;
            }

            context.SetAttribute("city", "market_1");
            context.SetAttribute("profile", new Dictionary<string, object> { ["city"] = "market_1", ["zone"] = "market_2" });
            context.SetAttribute("store_number", 123456789);

            var served = Expect(context, log, "agreement", "sb_agree", 3, (521, "sb_agree", "unit_serving", 3))
                         && Expect(context, log, "agreement repeat", "sb_agree", 3)
                         && Expect(context, log, "declared order", "sb_declared_order", 3,
                             (522, "sb_declared_order", "unit_user", 3))
                         && Expect(context, log, "disagreement", "sb_disagree", 0)
                         && Expect(context, log, "whole number", "sb_whole_number", 6,
                             (524, "sb_whole_number", "unit_serving", 6))
                         && Expect(context, log, "value filtered out", "sb_value_filtered", 0)
                         && Expect(context, log, "empty allow-list admits all", "sb_allow_empty", 3,
                             (528, "sb_allow_empty", "unit_serving", 3))
                         && Expect(context, log, "Phase 1E fields only", "sb_phase1e_only", 0);
            if (!served) return false;

            context.Publish();
            var baseline = PublishOne(context, log, publisher, "sb_unit_baseline", 1, (514, "sb_unit_baseline", "unit_serving", 1));
            var switchback = PublishOne(context, log, publisher, "sb_payload", 1, (513, "sb_payload", "unit_serving", 1));
            var inert = PublishOne(context, log, publisher, "sb_unit_with_fields", 1,
                (527, "sb_unit_with_fields", "unit_serving", 1));
            if (baseline == null || switchback == null || inert == null) return false;

            // No new publish or exposure fields: the switchback publish bytes have the unit
            // baseline's shape, and a unit experiment carrying every R33 field publishes the
            // baseline's bytes once id, name and timestamps are aligned.
            var serializer = new DefaultContextEventSerializer();
            var baselineJson = Normalized(serializer.Serialize(baseline));
            if (Normalized(serializer.Serialize(switchback)) != baselineJson)
            {
                Console.WriteLine($"{Prefix} check publish payload FAILED: shape of " +
                                  Encoding.UTF8.GetString(serializer.Serialize(switchback)) + " differs from " +
                                  Encoding.UTF8.GetString(serializer.Serialize(baseline)));
                return false;
            }

            if (Normalized(serializer.Serialize(inert)) != baselineJson)
            {
                Console.WriteLine($"{Prefix} check unit inertness FAILED: " +
                                  Encoding.UTF8.GetString(serializer.Serialize(inert)) + " differs from " +
                                  Encoding.UTF8.GetString(serializer.Serialize(baseline)));
                return false;
            }

            if (!baseline.Units.Any(u => u.Type == "unit_serving" && u.Uid == ServingUnitHash))
            {
                Console.WriteLine($"{Prefix} check publish units FAILED: no hashed serving unit");
                return false;
            }

            return true;
        }
        finally
        {
            (context as IDisposable)?.Dispose();
        }
    }

    private static List<string> ClusterKeys(ABSmartly.Models.ContextData data, string experimentName)
    {
        var experiment = data.Experiments.First(e => e.Name == experimentName);
        var clusters = typeof(Experiment).GetProperty("SwitchbackClusters")!.GetValue(experiment) as KeyValuePair<string, string>[];
        return clusters?.Select(c => c.Key).ToList() ?? new List<string>();
    }

    private static string Normalized(byte[] json)
    {
        var node = System.Text.Json.Nodes.JsonNode.Parse(json)!.AsObject();
        node.Remove("publishedAt");
        foreach (var exposure in node["exposures"]!.AsArray())
        {
            var e = exposure!.AsObject();
            e.Remove("exposedAt");
            e["id"] = 0;
            e["name"] = "";
        }

        return node.ToJsonString();
    }

    private static PublishEvent? PublishOne(IContext context, Log log, CapturingPublisher publisher, string experiment,
        int expectedTreatment, (int Id, string Name, string Unit, int Variant) expected)
    {
        if (!Expect(context, log, "publish " + experiment, experiment, expectedTreatment, expected)) return null;
        var before = publisher.Published.Count;
        context.Publish();
        if (publisher.Published.Count != before + 1 || publisher.Published[before].Exposures?.Length != 1)
        {
            Console.WriteLine($"{Prefix} check publish {experiment} FAILED: expected one publish with one exposure");
            return null;
        }

        var published = publisher.Published[before];
        var exposure = published.Exposures[0];
        if (exposure.Id != expected.Id || exposure.Name != expected.Name || exposure.Unit != expected.Unit ||
            exposure.Variant != expected.Variant || !exposure.Assigned || !exposure.Eligible ||
            exposure.Overridden || exposure.FullOn || exposure.Custom || exposure.AudienceMismatch || exposure.ExposedAt <= 0)
            return null;
        return published;
    }

    private static bool Expect(IContext context, Log log, string check, string experimentName, int expectedTreatment,
        params (int Id, string Name, string Unit, int Variant)[] expected)
    {
        var before = log.Exposures.Count;
        var treatment = context.GetTreatment(experimentName);
        var emitted = log.Exposures.Skip(before).ToList();
        var matches = treatment == expectedTreatment && emitted.Count == expected.Length;
        for (var i = 0; matches && i < expected.Length; i++)
        {
            var e = emitted[i];
            matches = e.Id == expected[i].Id && e.Name == expected[i].Name && e.Unit == expected[i].Unit &&
                      e.Variant == expected[i].Variant && e.Assigned && e.Eligible && !e.Overridden && !e.FullOn &&
                      !e.Custom && !e.AudienceMismatch && e.ExposedAt > 0;
        }

        if (!matches)
            Console.WriteLine($"{Prefix} check {check} FAILED: expected treatment={expectedTreatment} exposures=" +
                              $"[{string.Join(", ", expected)}], got treatment={treatment} exposures=" +
                              $"[{string.Join(", ", emitted.Select(e => (e.Id, e.Name, e.Unit, e.Variant, e.Assigned, e.Eligible)))}]");
        return matches;
    }

    private static Dictionary<string, object?> Base(int id, string name, int seedHi, int seedLo, int arms) => new()
    {
        ["id"] = id,
        ["name"] = name,
        ["iteration"] = 1,
        ["unitType"] = "unit_serving",
        ["seedHi"] = seedHi,
        ["seedLo"] = seedLo,
        ["split"] = Enumerable.Repeat(1.0 / arms, arms).ToArray(),
        ["trafficSeedHi"] = 1,
        ["trafficSeedLo"] = 2,
        ["trafficSplit"] = new[] { 0, 1 },
        ["fullOnVariant"] = 0,
        ["applications"] = new[] { new Dictionary<string, string> { ["name"] = "website" } },
        ["variants"] = Enumerable.Range(0, arms)
            .Select(i => new Dictionary<string, string?> { ["name"] = ((char)('A' + i)).ToString(), ["config"] = null })
            .ToArray(),
        ["audienceStrict"] = false
    };

    private static Dictionary<string, object?> Switchback(int id, string name, int seedHi, int seedLo, int arms,
        (string Type, string Path)[]? clusters)
    {
        var experiment = Base(id, name, seedHi, seedLo, arms);
        experiment["randomizationType"] = "switchback";
        if (clusters != null)
        {
            // A JsonObject writes its keys in insertion order: the declared cluster order.
            var map = new System.Text.Json.Nodes.JsonObject();
            foreach (var c in clusters) map[c.Type] = c.Path;
            experiment["switchbackClusters"] = map;
            experiment["unitType"] = clusters[0].Type;
        }

        experiment["switchbackStartAt"] = AnchorPeriod0;
        experiment["switchbackPeriodMs"] = PeriodMs;
        experiment["switchbackDesignType"] = "balanced_block";
        experiment["switchbackEstimand"] = "global_average_treatment_effect";
        return experiment;
    }

    // The unit baseline: seeds whose 3-arm unit draw for serving-9 is 1.
    private static Dictionary<string, object?> Unit(int id, string name) => Base(id, name, -559038737, 1234567890, 3);
}
