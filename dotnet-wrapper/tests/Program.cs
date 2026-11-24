using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

// No test framework dependencies: dotnet run --project tests/ProbeTests.csproj
var sameShape = typeof(SwitchbackSelfTest).GetMethod("SameShape",
    BindingFlags.Static | BindingFlags.NonPublic, null, new[] { typeof(byte[]), typeof(byte[]) }, null)!;
bool Matches(JsonNode a, JsonNode b) => (bool)sameShape.Invoke(null,
    new object[] { Encoding.UTF8.GetBytes(a.ToJsonString()), Encoding.UTF8.GetBytes(b.ToJsonString()) })!;
void Require(bool condition, string label)
{
    if (!condition) throw new Exception(label);
}
var baseline = JsonNode.Parse("""
    {"publishedAt":123,"exposures":[{"id":514,"name":"baseline","exposedAt":123}]}
    """)!;
var changed = baseline.DeepClone();
changed["publishedAt"] = 456;
changed["exposures"]![0]!["exposedAt"] = 789;
Require(Matches(baseline, changed), "timestamp values may differ");
changed["exposures"]![0]!.AsObject().Remove("exposedAt");
Require(!Matches(baseline, changed), "missing serialized exposedAt must fail before normalization");
changed = baseline.DeepClone();
changed.AsObject().Remove("publishedAt");
Require(!Matches(baseline, changed), "missing serialized publishedAt must fail");
changed = baseline.DeepClone();
changed["publishedAt"] = "123";
Require(!Matches(baseline, changed), "timestamp scalar type must match");
changed = baseline.DeepClone();
changed["exposures"]![0]!["switchbackOnly"] = true;
Require(!Matches(baseline, changed), "extra nested field must fail");
Require(SwitchbackSelfTest.Run(), "linked SDK self-test");
// 3.0 keys no cluster while 3 serves variant 5; a reader that keys 3.0 like 3, or one that keys no
// number at all, fails.
JsonElement Token(string json) => JsonDocument.Parse(json).RootElement.Clone();
Require(SwitchbackSelfTest.CheckIntegralFloat(Token("3.0"), Token("3")), "integral float check");
Require(!SwitchbackSelfTest.CheckIntegralFloat(Token("3"), Token("3")), "float keyed like the integer");
Require(!SwitchbackSelfTest.CheckIntegralFloat(Token("3.0"), Token("3.0")), "integer as a float");
Require(SwitchbackSelfTest.RunIntegralFloat(), "linked SDK integral-float self-test");
// The probe itself passes on this SDK (above), so only the gate can make it false: a failing
// switchback battery must withhold integralFloatAttributes.
var batteryRuns = 0;
Require(!SwitchbackSelfTest.RunIntegralFloatAfter(() => { batteryRuns++; return false; }),
    "integral float advertised despite a failing switchback battery");
Require(batteryRuns == 1, "switchback battery not consulted");
Console.WriteLine("All probe regressions passed");
