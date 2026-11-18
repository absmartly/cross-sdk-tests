//! Behavioural self-test behind the `switchback` capability (R33,
//! cluster-defined switchbacks), the Rust counterpart of the java-wrapper's
//! `SwitchbackSelfTest`. It runs a sample of the platform golden vectors
//! through rust-sdk's assigner and permutation, then serves an R33 payload
//! through a real context built the way scenario contexts are
//! (`Client::create_context_with` on data parsed from JSON text, so the
//! cluster map's key order is exercised), on the clock-free grid the
//! scenarios use. The verdict comes from observed treatments, exposures and
//! publish payloads only.

use std::sync::{Arc, Mutex};

use absmartly::switchback::{assign_switchback, base_hash, fisher_yates_permutation};
use absmartly::{Client, ContextData, ContextOptions, ContextPublisher, Exposure, PublishParams};
use serde_json::{json, Value};

const PREFIX: &str = "[switchback probe]";

// Shared with generate_switchback_scenarios.py: with a 400,000 h period "now"
// stays in period 0 until 2069.
const PERIOD_MS: i64 = 400_000 * 3_600_000;
const ANCHOR_PERIOD_0: i64 = 1_704_067_200_000;

// hash_unit("serving-9"), as published in PublishParams.units.
const SERVING_UNIT_HASH: &str = "r5uxZRoi-JuM5yYJa0kmSQ";

// (clusterUnit, periodIndex, seed, armCount, expectedVariant) from the
// platform golden vectors, including i64::MIN/MAX periods and seeds.
const ASSIGN_CASES: &[(&str, i64, i64, usize, usize)] = &[
    ("market_1", 0, 0, 2, 0),
    ("market_1", i64::MIN, 1, 2, 1),
    ("market_1", i64::MAX, 1, 2, 1),
    ("123456789", 0, 0, 2, 1),
    ("market_1", 10, 0, 3, 2),
    ("123456789", -2, -1, 3, 0),
    ("market_1", 5, 1, 10, 0),
    ("market_2", -11, i64::MIN, 10, 3),
    ("market_2", i64::MAX, i64::MIN, 10, 2),
    ("market_1", 1, i64::MAX, 2, 1),
];

// (clusterUnit, cycleIndex, seed, expectedPermutation), from the same fixture.
const PERMUTATION_CASES: &[(&str, i64, i64, &[usize])] = &[
    ("market_1", 0, 0, &[0, 3, 7, 5, 8, 2, 6, 4, 1, 9]),
    ("market_1", i64::MIN, 0, &[1, 0]),
    ("market_1", i64::MAX, 1, &[0, 1]),
    ("123456789", 0, 0, &[2, 0, 1]),
];

/// Runs once (see `SWITCHBACK_SELF_TEST` in main.rs) and fails closed.
pub fn run() -> bool {
    run_with(ASSIGN_CASES)
}

fn run_with(assign_cases: &[(&str, i64, i64, usize, usize)]) -> bool {
    match std::panic::catch_unwind(|| check_golden_sample(assign_cases) && check_context_serving()) {
        Ok(true) => {
            println!("{} behavioral self-test PASSED", PREFIX);
            true
        }
        Ok(false) => false,
        Err(_) => {
            println!("{} behavioral self-test FAILED with a panic", PREFIX);
            false
        }
    }
}

fn check_golden_sample(assign_cases: &[(&str, i64, i64, usize, usize)]) -> bool {
    for &(unit, period, seed, arms, expected) in assign_cases {
        let actual = assign_switchback(unit, period, seed, arms);
        if actual != Some(expected) {
            println!(
                "{} golden assign FAILED for {}/{}/{}/{}: got {:?}",
                PREFIX, unit, period, seed, arms, actual
            );
            return false;
        }
    }
    for &(unit, cycle, seed, expected) in PERMUTATION_CASES {
        let actual = fisher_yates_permutation(expected.len(), base_hash(unit, cycle, seed));
        if actual.as_deref() != Some(expected) {
            println!(
                "{} golden permutation FAILED for {}/{}/{}: expected {:?}, got {:?}",
                PREFIX, unit, cycle, seed, expected, actual
            );
            return false;
        }
    }
    true
}

fn base(id: i64, name: &str, seed_hi: i64, seed_lo: i64, arms: usize) -> Value {
    let variants: Vec<Value> = (0..arms)
        .map(|i| json!({"name": ((b'A' + i as u8) as char).to_string(), "config": null}))
        .collect();
    json!({
        "id": id, "name": name, "iteration": 1, "unitType": "unit_serving",
        "seedHi": seed_hi, "seedLo": seed_lo, "split": vec![1.0 / arms as f64; arms],
        "trafficSeedHi": 1, "trafficSeedLo": 2, "trafficSplit": [0, 1], "fullOnVariant": 0,
        "applications": [{"name": "website"}], "variants": variants, "audienceStrict": false
    })
}

/// A switchback experiment as JSON text, the cluster map written in its
/// declared order (`json!` would sort its keys).
fn switchback(
    id: i64,
    name: &str,
    seed_lo: i64,
    arms: usize,
    clusters: Option<&[(&str, &str)]>,
    extra: Value,
) -> String {
    let mut experiment = base(id, name, 0, seed_lo, arms);
    experiment["randomizationType"] = json!("switchback");
    experiment["switchbackStartAt"] = json!(ANCHOR_PERIOD_0);
    experiment["switchbackPeriodMs"] = json!(PERIOD_MS);
    experiment["switchbackDesignType"] = json!("balanced_block");
    experiment["switchbackEstimand"] = json!("global_average_treatment_effect");
    if let Some(clusters) = clusters {
        experiment["unitType"] = json!(clusters[0].0);
    }
    if let Value::Object(extra) = extra {
        for (k, v) in extra {
            experiment[k] = v;
        }
    }
    let text = experiment.to_string();
    match clusters {
        None => text,
        Some(clusters) => {
            let map = clusters
                .iter()
                .map(|(t, p)| format!("{}:{}", json!(t), json!(p)))
                .collect::<Vec<_>>()
                .join(",");
            format!("{},\"switchbackClusters\":{{{}}}}}", &text[..text.len() - 1], map)
        }
    }
}

/// The probe payload, as the Collector serves it. The 10-arm variants are
/// the ones java-wrapper's probe pins, each different from a hashed key, the
/// uid as key, the hashed uid as key, a Phase 1E bucket and the sticky unit
/// assignment for both uids.
fn probe_payload() -> String {
    let unit_baseline = base(514, "sb_unit_baseline", -559038737, 1234567890, 3);
    let mut with_fields = base(527, "sb_unit_with_fields", -559038737, 1234567890, 3);
    with_fields["randomizationType"] = json!("unit");
    with_fields["switchbackClusters"] = json!({"unit_serving": "city"});
    with_fields["switchbackClusterValues"] = json!(["market_1"]);
    with_fields["switchbackStartAt"] = json!(ANCHOR_PERIOD_0);
    with_fields["switchbackPeriodMs"] = json!(PERIOD_MS);
    with_fields["switchbackDesignType"] = json!("balanced_block");
    let experiments = [
        switchback(
            521,
            "sb_agree",
            42,
            10,
            Some(&[("unit_serving", "city"), ("unit_user", "profile/city")]),
            json!({}),
        ),
        switchback(
            522,
            "sb_declared_order",
            42,
            10,
            Some(&[("unit_user", "profile/city"), ("unit_serving", "city")]),
            json!({}),
        ),
        switchback(
            523,
            "sb_disagree",
            42,
            10,
            Some(&[("unit_serving", "city"), ("unit_user", "profile/zone")]),
            json!({}),
        ),
        switchback(
            524,
            "sb_whole_number",
            42,
            10,
            Some(&[("unit_serving", "store_number")]),
            json!({}),
        ),
        switchback(
            530,
            "sb_float_number",
            42,
            10,
            Some(&[("unit_serving", "store_float")]),
            json!({}),
        ),
        switchback(
            528,
            "sb_allow_empty",
            42,
            10,
            Some(&[("unit_serving", "city")]),
            json!({"switchbackClusterValues": []}),
        ),
        switchback(
            525,
            "sb_value_filtered",
            1,
            10,
            Some(&[("unit_serving", "city")]),
            json!({"switchbackClusterValues": ["market_2"]}),
        ),
        switchback(
            526,
            "sb_phase1e_only",
            1,
            10,
            None,
            json!({"switchbackClusterUnitType": "unit_serving", "switchbackClusterCount": 10, "switchbackClusterAttribute": "city"}),
        ),
        switchback(
            529,
            "sb_holdout_covered",
            42,
            10,
            Some(&[("unit_serving", "city")]),
            json!({"holdoutIds": [91]}),
        ),
        // An assign rule, full-on and a strict audience mismatch win over the
        // draw (3) with the flags a unit experiment publishes for them.
        switchback(
            531,
            "sb_rule",
            42,
            10,
            Some(&[("unit_serving", "city")]),
            json!({"assignmentRules": r#"{"rules":[{"type":"assign","environments":[],"variant":7}]}"#}),
        ),
        switchback(
            532,
            "sb_full_on",
            42,
            10,
            Some(&[("unit_serving", "city")]),
            json!({"fullOnVariant": 8}),
        ),
        switchback(
            533,
            "sb_strict_mismatch",
            42,
            10,
            Some(&[("unit_serving", "city")]),
            json!({"audienceStrict": true, "audience": r#"{"filter":[{"eq":[{"var":"tier"},{"value":"gold"}]}]}"#}),
        ),
        switchback(513, "sb_payload", 1, 3, Some(&[("unit_serving", "city")]), json!({})),
        unit_baseline.to_string(),
        with_fields.to_string(),
    ];
    // A holdout whose split holds every unit out (arm 0): it must neither
    // suppress the covered switchback nor record its own exposure through it.
    let holdout = json!({"id": 91, "name": "sb_probe_holdout", "unitType": "unit_serving", "iteration": 1,
        "seedHi": 1, "seedLo": 2, "split": [1.0, 0.0], "trafficSplit": [0, 1]});
    format!(r#"{{"experiments":[{}],"holdouts":[{}]}}"#, experiments.join(","), holdout)
}

struct Capture(Arc<Mutex<Vec<PublishParams>>>);

impl ContextPublisher for Capture {
    fn publish(&self, params: &PublishParams) -> Result<(), String> {
        self.0.lock().unwrap().push(params.clone());
        Ok(())
    }
}

/// (id, name, unit, variant, [assigned, eligible, overridden, fullOn, custom, audienceMismatch, ruleOverride])
type Served = (i64, &'static str, &'static str, i32, [bool; 7]);

const ELIGIBLE: [bool; 7] = [true, true, false, false, false, false, false];
const RULE: [bool; 7] = [false, true, false, false, false, false, true];
const FULL_ON: [bool; 7] = [true, true, false, true, false, false, false];
const STRICT_MISMATCH: [bool; 7] = [false, true, false, false, false, true, false];

fn served_as(actual: &Exposure, expected: &Served) -> bool {
    let (id, name, unit, variant, flags) = expected;
    actual.id == *id
        && actual.name == *name
        && actual.unit.as_deref() == Some(*unit)
        && actual.variant == *variant
        && [
            actual.assigned,
            actual.eligible,
            actual.overridden,
            actual.full_on,
            actual.custom,
            actual.audience_mismatch,
            actual.rule_override,
        ] == *flags
        && actual.exposed_at > 0
}

fn check_context_serving() -> bool {
    let data: ContextData = match serde_json::from_str(&probe_payload()) {
        Ok(data) => data,
        Err(e) => {
            println!("{} FAILED: probe payload did not parse: {}", PREFIX, e);
            return false;
        }
    };
    let order: Vec<&str> = data
        .experiments
        .iter()
        .find(|e| e.name == "sb_declared_order")
        .and_then(|e| e.switchback_clusters.as_deref())
        .map(|c| c.iter().map(|(t, _)| t.as_str()).collect())
        .unwrap_or_default();
    if order != ["unit_user", "unit_serving"] {
        println!("{} FAILED: switchbackClusters lost its declared order: {:?}", PREFIX, order);
        return false;
    }

    let exposures: Arc<Mutex<Vec<Exposure>>> = Arc::new(Mutex::new(Vec::new()));
    let sink = exposures.clone();
    let options = ContextOptions {
        publish_delay: -1,
        refresh_period: 0,
        event_logger: Some(Arc::new(move |_ctx, event, data| {
            if event == "exposure" {
                if let Some(exposure) = data.and_then(|d| serde_json::from_value::<Exposure>(d).ok()) {
                    sink.lock().unwrap().push(exposure);
                }
            }
        })),
    };
    let client = match Client::new("http://dummy", "test-key", "website", "test-env") {
        Ok(client) => client,
        Err(e) => {
            println!("{} FAILED: client: {}", PREFIX, e);
            return false;
        }
    };
    // unit_user is set first, so the declared order, not the context's
    // order, must pick the serving unit.
    let units = vec![("unit_user", "probe-user"), ("unit_serving", "serving-9")];
    let mut context = client.create_context_with(units, data, Some(options));
    let published: Arc<Mutex<Vec<PublishParams>>> = Arc::new(Mutex::new(Vec::new()));
    context.set_publisher(Box::new(Capture(published.clone())));
    let attributes: Vec<(&str, Value)> = vec![
        ("city", json!("market_1")),
        ("profile", json!({"city": "market_1", "zone": "market_2"})),
        ("store_number", json!(123456789)),
        ("store_float", serde_json::from_str("123456789.0").unwrap()),
    ];
    if context.set_attributes(attributes).is_err() {
        println!("{} FAILED: set_attributes", PREFIX);
        return false;
    }

    let mut expect = |check: &str, name: &str, treatment: i32, expected: &[Served]| -> bool {
        let before = exposures.lock().unwrap().len();
        let actual = context.treatment(name);
        let emitted: Vec<Exposure> = exposures.lock().unwrap()[before..].to_vec();
        let matches = actual == Ok(treatment)
            && emitted.len() == expected.len()
            && emitted.iter().zip(expected).all(|(a, e)| served_as(a, e));
        if !matches {
            println!(
                "{} check {} FAILED: expected treatment={} exposures={:?}, got treatment={:?} exposures={:?}",
                PREFIX, check, treatment, expected, actual, emitted
            );
        }
        matches
    };
    let served = expect("agreement", "sb_agree", 3, &[(521, "sb_agree", "unit_serving", 3, ELIGIBLE)])
        && expect("agreement repeat", "sb_agree", 3, &[])
        && expect(
            "declared order",
            "sb_declared_order",
            3,
            &[(522, "sb_declared_order", "unit_user", 3, ELIGIBLE)],
        )
        && expect("disagreement", "sb_disagree", 0, &[])
        && expect(
            "whole number",
            "sb_whole_number",
            6,
            &[(524, "sb_whole_number", "unit_serving", 6, ELIGIBLE)],
        )
        && expect("float number gives no cluster", "sb_float_number", 0, &[])
        && expect("value filtered out", "sb_value_filtered", 0, &[])
        && expect(
            "empty allow-list admits all",
            "sb_allow_empty",
            3,
            &[(528, "sb_allow_empty", "unit_serving", 3, ELIGIBLE)],
        )
        && expect("Phase 1E fields only", "sb_phase1e_only", 0, &[])
        && expect(
            "holdouts never apply",
            "sb_holdout_covered",
            3,
            &[(529, "sb_holdout_covered", "unit_serving", 3, ELIGIBLE)],
        )
        && expect("assignment rule", "sb_rule", 7, &[(531, "sb_rule", "unit_serving", 7, RULE)])
        && expect("full-on", "sb_full_on", 8, &[(532, "sb_full_on", "unit_serving", 8, FULL_ON)])
        && expect(
            "strict audience mismatch",
            "sb_strict_mismatch",
            0,
            &[(533, "sb_strict_mismatch", "unit_serving", 0, STRICT_MISMATCH)],
        );
    if !served {
        return false;
    }
    check_payload_shape(&mut context, &exposures, &published)
}

fn publish_one(context: &mut absmartly::Context, published: &Arc<Mutex<Vec<PublishParams>>>) -> Option<Value> {
    let before = published.lock().unwrap().len();
    context.publish().ok()?;
    let published = published.lock().unwrap();
    (published.len() == before + 1)
        .then(|| serde_json::to_value(&published[before]).ok())
        .flatten()
}

/// With each exposure's id, name and exposedAt and the publishedAt cleared.
fn normalised(mut event: Value) -> Value {
    event["publishedAt"] = json!(0);
    if let Some(exposures) = event["exposures"].as_array_mut() {
        for exposure in exposures {
            exposure["id"] = json!(0);
            exposure["name"] = json!("");
            exposure["exposedAt"] = json!(0);
        }
    }
    event
}

fn shape(value: &Value) -> Value {
    match value {
        Value::Object(map) => Value::Object(map.iter().map(|(k, v)| (k.clone(), shape(v))).collect()),
        Value::Array(items) => Value::Array(items.iter().map(shape).collect()),
        _ => Value::Null,
    }
}

/// A unit baseline and a switchback on the same unit, each published on its
/// own, have the same fields at every depth; a unit experiment carrying every
/// R33 field publishes the same bytes as the plain baseline.
fn check_payload_shape(
    context: &mut absmartly::Context,
    exposures: &Arc<Mutex<Vec<Exposure>>>,
    published: &Arc<Mutex<Vec<PublishParams>>>,
) -> bool {
    if publish_one(context, published).is_none() {
        println!("{} check payload shape FAILED: first publish", PREFIX);
        return false;
    }
    let before = exposures.lock().unwrap().len();
    let baseline_variant = context.treatment("sb_unit_baseline");
    let baseline = publish_one(context, published);
    let switchback_variant = context.treatment("sb_payload");
    let switchback = publish_one(context, published);
    let with_fields_variant = context.treatment("sb_unit_with_fields");
    let with_fields = publish_one(context, published);
    let emitted: Vec<Exposure> = exposures.lock().unwrap()[before..].to_vec();
    let (Some(baseline), Some(switchback), Some(with_fields)) = (baseline, switchback, with_fields) else {
        println!("{} check payload shape FAILED: missing publish events", PREFIX);
        return false;
    };
    let expected: [Served; 3] = [
        (514, "sb_unit_baseline", "unit_serving", 1, ELIGIBLE),
        (513, "sb_payload", "unit_serving", 1, ELIGIBLE),
        (527, "sb_unit_with_fields", "unit_serving", 1, ELIGIBLE),
    ];
    if (baseline_variant, switchback_variant, with_fields_variant) != (Ok(1), Ok(1), Ok(1))
        || emitted.len() != 3
        || !emitted.iter().zip(&expected).all(|(a, e)| served_as(a, e))
    {
        println!("{} check published exposures FAILED: {:?}", PREFIX, emitted);
        return false;
    }
    for ((event, name), expected) in [(&baseline, "baseline"), (&switchback, "switchback"), (&with_fields, "unit with fields")]
        .into_iter()
        .zip(&expected)
    {
        // The exposure the publisher received, not just the logged one.
        let published_exposure = event["exposures"]
            .get(0)
            .and_then(|e| serde_json::from_value::<Exposure>(e.clone()).ok());
        if !published_exposure.is_some_and(|e| served_as(&e, expected)) {
            println!(
                "{} check published exposure FAILED for {}: expected {:?}, got {}",
                PREFIX, name, expected, event["exposures"]
            );
            return false;
        }
        let units = event["units"].as_array().cloned().unwrap_or_default();
        let hashed = units
            .iter()
            .any(|u| u["type"] == json!("unit_serving") && u["uid"] == json!(SERVING_UNIT_HASH));
        if event["hashed"] != json!(true) || !hashed || event["exposures"].as_array().map(Vec::len) != Some(1) {
            println!("{} check publish payload FAILED for {}: {}", PREFIX, name, event);
            return false;
        }
    }
    if shape(&baseline) != shape(&switchback) {
        println!(
            "{} check publish payload FAILED: switchback {} differs in shape from baseline {}",
            PREFIX, switchback, baseline
        );
        return false;
    }
    if normalised(baseline.clone()) != normalised(with_fields.clone()) {
        println!(
            "{} check unit inertness FAILED: publish {} differs from {}",
            PREFIX, with_fields, baseline
        );
        return false;
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn passes_on_the_linked_sdk() {
        assert!(run());
    }

    #[test]
    fn fails_closed_on_a_wrong_golden_vector() {
        let mut cases = ASSIGN_CASES.to_vec();
        cases[0].4 = 1 - cases[0].4;
        assert!(!run_with(&cases));
    }
}
