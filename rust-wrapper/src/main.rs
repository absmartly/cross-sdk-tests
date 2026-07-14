use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::IntoResponse,
    routing::{delete, get, post, put},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::{Arc, Mutex, RwLock};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tower_http::cors::CorsLayer;
use uuid::Uuid;

use absmartly_sdk::{utils, ABsmartly, Context, ContextData};

fn now_millis() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

fn translate_endpoint(endpoint: &str) -> String {
    let mut translated = endpoint.to_string();
    for prefix in ["localhost:", "127.0.0.1:"] {
        if let Some(start) = translated.find(prefix) {
            let port_start = start + prefix.len();
            let port_end = translated[port_start..]
                .find('/')
                .map(|i| port_start + i)
                .unwrap_or(translated.len());
            if translated[port_start..port_end]
                .chars()
                .all(|c| c.is_ascii_digit())
            {
                translated.replace_range(start..port_end, "127.0.0.1:3000");
                break;
            }
        }
    }
    translated
}

#[derive(Clone, Serialize, Deserialize)]
struct Event {
    #[serde(rename = "type")]
    event_type: String,
    data: Option<Value>,
    timestamp: i64,
}

struct EventCollector {
    events: Mutex<Vec<Event>>,
}

impl EventCollector {
    fn new() -> Self {
        Self {
            events: Mutex::new(Vec::new()),
        }
    }

    fn push(&self, event_type: &str, data: Option<Value>) {
        let mut events = self.events.lock().unwrap();
        events.push(Event {
            event_type: event_type.to_string(),
            data,
            timestamp: now_millis(),
        });
    }

    fn get_events_since(&self, index: usize) -> Vec<Event> {
        let events = self.events.lock().unwrap();
        events[index..].to_vec()
    }

    fn len(&self) -> usize {
        self.events.lock().unwrap().len()
    }
}

struct ContextData_ {
    context: Mutex<Context>,
    event_collector: Arc<EventCollector>,
    publish_fail: std::sync::atomic::AtomicBool,
    sdk: Arc<ABsmartly>,
    is_e2e: bool,
}

struct AppState {
    contexts: RwLock<HashMap<String, Arc<ContextData_>>>,
    payloads: RwLock<HashMap<String, ContextData>>,
}

impl AppState {
    fn new() -> Self {
        Self {
            contexts: RwLock::new(HashMap::new()),
            payloads: RwLock::new(HashMap::new()),
        }
    }
}

#[derive(Serialize)]
struct ApiResponse {
    result: Value,
    events: Vec<Event>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

#[derive(Deserialize)]
struct CreateContextRequest {
    data: Option<ContextData>,
    endpoint: Option<String>,
    units: HashMap<String, Value>,
    #[serde(default)]
    options: Option<HashMap<String, Value>>,
    #[serde(default, rename = "failLoad")]
    fail_load: bool,
    #[serde(default)]
    mode: Option<String>,
    #[serde(default)]
    attributes: Option<HashMap<String, Value>>,
}

#[derive(Serialize)]
struct CreateContextResponse {
    #[serde(rename = "contextId")]
    context_id: String,
    ready: bool,
    failed: bool,
    finalized: bool,
}

#[derive(Deserialize)]
struct SetUnitRequest {
    #[serde(rename = "unitType")]
    unit_type: String,
    uid: Value,
}

#[derive(Deserialize)]
struct GetUnitRequest {
    #[serde(rename = "unitType")]
    unit_type: String,
}

#[derive(Deserialize)]
struct SetAttributeRequest {
    name: String,
    value: Value,
}

#[derive(Deserialize)]
struct GetAttributeRequest {
    name: String,
}

#[derive(Deserialize)]
struct TreatmentRequest {
    #[serde(rename = "experimentName")]
    experiment_name: String,
}

#[derive(Deserialize)]
struct VariableValueRequest {
    key: String,
    #[serde(rename = "defaultValue")]
    default_value: Value,
}

#[derive(Deserialize)]
struct TrackRequest {
    #[serde(rename = "goalName")]
    goal_name: String,
    properties: Option<Value>,
}

#[derive(Deserialize)]
struct OverrideRequest {
    #[serde(rename = "experimentName")]
    experiment_name: String,
    variant: i32,
}

#[derive(Deserialize)]
struct CustomFieldValueRequest {
    #[serde(rename = "experimentName")]
    experiment_name: String,
    #[serde(rename = "fieldName")]
    field_name: String,
}

#[derive(Deserialize)]
struct RefreshRequest {
    #[serde(rename = "newData")]
    new_data: ContextData,
}

#[derive(Deserialize)]
struct StorePayloadRequest {
    data: ContextData,
}

#[derive(Deserialize)]
struct DiagnosticRequest {
    operation: String,
    value: Option<Value>,
}

async fn health_handler() -> impl IntoResponse {
    Json(json!({
        "status": "healthy",
        "sdk": "rust",
        "version": "0.1.0"
    }))
}

async fn capabilities_handler() -> impl IntoResponse {
    Json(json!({"diagnostics": true,
        "attrsSeq": false,
        "publishFail": true,
        "variableKeysMap": true,
        "globalCustomFieldKeys": true,
        "getUnits": true,
        "getAttributes": true,
        "readyError": true
    }))
}

async fn diagnostic_handler(
    Json(req): Json<DiagnosticRequest>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let value = req.value.unwrap_or(Value::Null);
    let result = match req.operation.as_str() {
        "hashUnit" => {
            let input = value.as_str().map(|s| s.to_string()).unwrap_or_else(|| value.to_string());
            Value::String(utils::hash_unit(&input))
        }
        "base64UrlNoPadding" => {
            let input = value.as_str().map(|s| s.to_string()).unwrap_or_else(|| value.to_string());
            Value::String(utils::base64_url_no_padding(input.as_bytes()))
        }
        "utf8Bytes" => {
            let input = value.as_str().map(|s| s.to_string()).unwrap_or_else(|| value.to_string());
            Value::Array(
                input
                    .as_bytes()
                    .iter()
                    .map(|b| Value::Number(serde_json::Number::from(*b)))
                    .collect(),
            )
        }
        "isObject" => Value::Bool(matches!(value, Value::Object(_))),
        "isNumeric" => Value::Bool(matches!(value, Value::Number(_))),
        "isPromise" => Value::Bool(false),
        _ => {
            return Err((
                StatusCode::BAD_REQUEST,
                Json(json!({ "error": format!("Unsupported diagnostic operation: {}", req.operation) })),
            ))
        }
    };

    Ok(Json(ApiResponse {
        result,
        events: vec![],
        error: None,
    }))
}

async fn store_payload_handler(
    State(state): State<Arc<AppState>>,
    Path(payload_id): Path<String>,
    Json(req): Json<StorePayloadRequest>,
) -> impl IntoResponse {
    let mut payloads = state.payloads.write().unwrap();
    payloads.insert(payload_id, req.data);

    Json(json!({ "success": true }))
}

// Mock ABsmartly API - SDK calls GET /context_payload/{payloadId}/context?application=...&environment=...
async fn mock_api_context_handler(
    State(state): State<Arc<AppState>>,
    Path(payload_id): Path<String>,
) -> impl IntoResponse {
    let payloads = state.payloads.read().unwrap();
    let data = payloads.get(&payload_id).cloned().unwrap_or_default();
    Json(data)
}

fn value_to_string(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i.to_string()
            } else if let Some(f) = n.as_f64() {
                if f == f.trunc() {
                    (f as i64).to_string()
                } else {
                    f.to_string()
                }
            } else {
                n.to_string()
            }
        }
        _ => v.to_string(),
    }
}

async fn create_context_handler(
    State(state): State<Arc<AppState>>,
    Json(req): Json<CreateContextRequest>,
) -> impl IntoResponse {
    if req.mode.as_deref() == Some("e2e") {
        let e2e_endpoint = std::env::var("ABSMARTLY_E2E_ENDPOINT").unwrap_or_default();
        let e2e_api_key = std::env::var("ABSMARTLY_E2E_API_KEY").unwrap_or_default();
        let e2e_app = std::env::var("ABSMARTLY_E2E_APPLICATION").unwrap_or_default();
        let e2e_env = std::env::var("ABSMARTLY_E2E_ENVIRONMENT").unwrap_or_default();

        if e2e_endpoint.is_empty() || e2e_api_key.is_empty() || e2e_app.is_empty() || e2e_env.is_empty() {
            return (
                StatusCode::NOT_IMPLEMENTED,
                Json(json!({"error": "e2e mode not configured"})),
            ).into_response();
        }

        let context_id = format!("ctx-{}", Uuid::new_v4());
        let event_collector = Arc::new(EventCollector::new());

        let units: HashMap<String, String> = req
            .units
            .iter()
            .map(|(k, v)| (k.clone(), value_to_string(v)))
            .collect();

        let sdk = match ABsmartly::new(&e2e_endpoint, &e2e_api_key, &e2e_app, &e2e_env) {
            Ok(s) => Arc::new(s),
            Err(e) => {
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"error": format!("Failed to create SDK: {}", e)})),
                ).into_response();
            }
        };

        let result = sdk.create_context(units.clone(), None).await;
        let (mut context, ready, failed) = match result {
            Ok(ctx) => {
                let data = ctx.data().clone();
                event_collector.push("ready", Some(serde_json::to_value(&data).unwrap_or_default()));
                (ctx, true, false)
            }
            Err(e) => {
                let mut ctx = Context::new_loading();
                for (unit_type, uid) in &units {
                    let _ = ctx.set_unit(unit_type, uid);
                }
                ctx.become_failed();
                event_collector.push("error", Some(json!({"message": format!("{}", e)})));
                (ctx, false, true)
            }
        };

        let ec = event_collector.clone();
        context.set_event_logger(Arc::new(move |_ctx: &Context, event_name: &str, data: Option<Value>| {
            ec.push(event_name, data);
        }));

        if let Some(attributes) = &req.attributes {
            for (name, value) in attributes {
                let _ = context.set_attribute(name, value.clone());
            }
        }

        let finalized = context.is_finalized();

        let context_data = Arc::new(ContextData_ {
            context: Mutex::new(context),
            event_collector: event_collector.clone(),
            publish_fail: std::sync::atomic::AtomicBool::new(false),
            sdk: sdk.clone(),
            is_e2e: true,
        });

        let mut contexts = state.contexts.write().unwrap();
        contexts.insert(context_id.clone(), context_data);

        let events = event_collector.get_events_since(0);

        return Json(ApiResponse {
            result: serde_json::to_value(CreateContextResponse {
                context_id,
                ready,
                failed,
                finalized,
            })
            .unwrap(),
            events,
            error: None,
        })
        .into_response();
    }

    let context_id = format!("ctx-{}", Uuid::new_v4());
    let event_collector = Arc::new(EventCollector::new());

    let units: HashMap<String, String> = req
        .units
        .iter()
        .map(|(k, v)| (k.clone(), value_to_string(v)))
        .collect();

    let payload_throttle_ms = req.options.as_ref()
        .and_then(|opts| opts.get("payloadThrottle"))
        .and_then(|v| v.as_u64())
        .unwrap_or(0);

    let (mut context, ready, failed) = if let Some(data) = req.data {
        let sdk = ABsmartly::new("http://dummy", "test-key", "test-app", "test-env").expect("Failed to create SDK");
        let ctx = sdk.create_context_with(units, data.clone(), None);
        event_collector.push("ready", Some(serde_json::to_value(&data).unwrap_or_default()));
        (ctx, true, false)
    } else if let Some(endpoint) = req.endpoint {
        if payload_throttle_ms > 0 {
            let mut ctx = Context::new_loading();
            for (unit_type, uid) in &units {
                let _ = ctx.set_unit(unit_type, uid);
            }

            let ec = event_collector.clone();
            ctx.set_event_logger(Arc::new(move |_ctx, event_name, data| {
                ec.push(event_name, data);
            }));

            let context_data = Arc::new(ContextData_ {
                context: Mutex::new(ctx),
                event_collector: event_collector.clone(),
                publish_fail: std::sync::atomic::AtomicBool::new(false),
                sdk: Arc::new(
                    ABsmartly::new("http://dummy", "test-key", "test-app", "test-env")
                        .expect("Failed to create SDK"),
                ),
                is_e2e: false,
            });

            {
                let mut contexts = state.contexts.write().unwrap();
                contexts.insert(context_id.clone(), context_data.clone());
            }

            let endpoint_clone = translate_endpoint(&endpoint);
            let context_data_clone = context_data.clone();
            let event_collector_clone = event_collector.clone();
            let state_clone = state.clone();
            let throttle_delay = payload_throttle_ms;

            tokio::spawn(async move {
                tokio::time::sleep(Duration::from_millis(throttle_delay)).await;

                if let Some(payload_id) = endpoint_clone
                    .split("/context_payload/")
                    .nth(1)
                    .map(|s| s.trim_matches('/').to_string())
                {
                    let payload_data = {
                        let payloads = state_clone.payloads.read().unwrap();
                        payloads.get(&payload_id).cloned()
                    };

                    if let Some(data) = payload_data {
                        {
                            let mut context = context_data_clone.context.lock().unwrap();
                            context.become_ready(data.clone());
                        }
                        event_collector_clone.push("ready", Some(serde_json::to_value(&data).unwrap_or_default()));
                        return;
                    }
                }

                let sdk = ABsmartly::new(&endpoint_clone, "test-key", "test-app", "test-env")
                    .expect("Failed to create SDK");
                let result: Result<Context, _> =
                    sdk.create_context(HashMap::<String, String>::new(), None).await;
                match result {
                    Ok(ready_ctx) => {
                        let data: ContextData = ready_ctx.data().clone();
                        {
                            let mut context = context_data_clone.context.lock().unwrap();
                            context.become_ready(data.clone());
                        }
                        event_collector_clone
                            .push("ready", Some(serde_json::to_value(&data).unwrap_or_default()));
                    }
                    Err(e) => {
                        {
                            let mut context = context_data_clone.context.lock().unwrap();
                            context.become_failed();
                        }
                        event_collector_clone.push(
                            "error",
                            Some(json!({"message": format!("Failed to fetch context: {}", e)})),
                        );
                    }
                }
            });

            let events = event_collector.get_events_since(0);

            return Json(ApiResponse {
                result: serde_json::to_value(CreateContextResponse {
                    context_id,
                    ready: false,
                    failed: false,
                    finalized: false,
                })
                .unwrap(),
                events,
                error: None,
            })
            .into_response();
        } else {
            let translated_endpoint = translate_endpoint(&endpoint);
            let payload_data = if let Some(payload_id) = translated_endpoint
                .split("/context_payload/")
                .nth(1)
                .map(|s| s.to_string())
            {
                let payloads = state.payloads.read().unwrap();
                payloads.get(&payload_id).cloned()
            } else {
                None
            };

            if let Some(data) = payload_data {
                let sdk = ABsmartly::new("http://dummy", "test-key", "test-app", "test-env").expect("Failed to create SDK");
                let ctx = sdk.create_context_with(units, data.clone(), None);
                event_collector.push("ready", Some(serde_json::to_value(&data).unwrap_or_default()));
                (ctx, true, false)
            } else {
                let sdk = ABsmartly::new("http://dummy", "test-key", "test-app", "test-env").expect("Failed to create SDK");
                let ctx = sdk.create_context_with(HashMap::<String, String>::new(), ContextData::default(), None);
                event_collector.push(
                    "error",
                    Some(json!({"message": format!("Payload not found for endpoint: {}", translated_endpoint)})),
                );
                (ctx, true, true)
            }
        }
    } else if req.fail_load {
        let mut ctx = Context::new_loading();
        for (unit_type, uid) in &units {
            let _ = ctx.set_unit(unit_type, uid);
        }
        ctx.become_failed_with_error("Context load failed".to_string());
        event_collector.push("error", Some(json!({"message": "Context load failed"})));
        (ctx, false, true)
    } else {
        let sdk = ABsmartly::new("http://dummy", "test-key", "test-app", "test-env").expect("Failed to create SDK");
        let ctx = sdk.create_context_with(units, ContextData::default(), None);
        event_collector.push("ready", Some(serde_json::to_value(&ContextData::default()).unwrap_or_default()));
        (ctx, true, false)
    };

    let ec = event_collector.clone();
    context.set_event_logger(Arc::new(move |_ctx: &Context, event_name: &str, data: Option<Value>| {
        ec.push(event_name, data);
    }));

    let finalized = context.is_finalized();

    let context_data = Arc::new(ContextData_ {
        context: Mutex::new(context),
        event_collector: event_collector.clone(),
        publish_fail: std::sync::atomic::AtomicBool::new(false),
        sdk: Arc::new(
            ABsmartly::new("http://dummy", "test-key", "test-app", "test-env")
                .expect("Failed to create SDK"),
        ),
        is_e2e: false,
    });

    let mut contexts = state.contexts.write().unwrap();
    contexts.insert(context_id.clone(), context_data);

    let events = event_collector.get_events_since(0);

    Json(ApiResponse {
        result: serde_json::to_value(CreateContextResponse {
            context_id,
            ready,
            failed,
            finalized,
        })
        .unwrap(),
        events,
        error: None,
    })
    .into_response()
}

fn get_context(
    state: &Arc<AppState>,
    context_id: &str,
) -> Result<Arc<ContextData_>, (StatusCode, Json<Value>)> {
    let contexts = state.contexts.read().unwrap();
    contexts.get(context_id).cloned().ok_or_else(|| {
        (
            StatusCode::NOT_FOUND,
            Json(json!({"error": "context not found"})),
        )
    })
}

async fn set_unit_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<SetUnitRequest>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();

    let uid_str = value_to_string(&req.uid);

    {
        let mut context = ctx_data.context.lock().unwrap();
        if let Err(e) = context.set_unit(&req.unit_type, &uid_str) {
            return Err((StatusCode::BAD_REQUEST, Json(json!({"error": e}))));
        }
    }

    let new_events = ctx_data.event_collector.get_events_since(events_before);

    Ok(Json(ApiResponse {
        result: Value::Null,
        events: new_events,
        error: None,
    }))
}

async fn get_unit_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<GetUnitRequest>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();

    let result = {
        let context = ctx_data.context.lock().unwrap();
        match context.get_unit(&req.unit_type) {
            Some(uid) => {
                if let Ok(n) = uid.parse::<i64>() {
                    Value::Number(n.into())
                } else if let Ok(f) = uid.parse::<f64>() {
                    serde_json::Number::from_f64(f)
                        .map(Value::Number)
                        .unwrap_or(Value::String(uid.clone()))
                } else {
                    Value::String(uid.clone())
                }
            }
            None => Value::Null,
        }
    };

    let new_events = ctx_data.event_collector.get_events_since(events_before);

    Ok(Json(ApiResponse {
        result,
        events: new_events,
        error: None,
    }))
}

async fn set_attribute_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<SetAttributeRequest>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();

    {
        let mut context = ctx_data.context.lock().unwrap();
        if let Err(e) = context.set_attribute(&req.name, req.value) {
            return Err((StatusCode::BAD_REQUEST, Json(json!({"error": e}))));
        }
    }

    let new_events = ctx_data.event_collector.get_events_since(events_before);

    Ok(Json(ApiResponse {
        result: Value::Null,
        events: new_events,
        error: None,
    }))
}

async fn get_attribute_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<GetAttributeRequest>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();

    let result = {
        let context = ctx_data.context.lock().unwrap();
        context.get_attribute(&req.name).cloned().unwrap_or(Value::Null)
    };

    let new_events = ctx_data.event_collector.get_events_since(events_before);

    Ok(Json(ApiResponse {
        result,
        events: new_events,
        error: None,
    }))
}

async fn treatment_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<TreatmentRequest>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();

    // The rust SDK's treatment() returns 0 after finalize rather than erroring, so
    // guard the finalized state explicitly (scenario 189).
    if ctx_data.context.lock().unwrap().is_finalized() {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "Context finalized"})),
        ));
    }

    let (variant, is_ready) = {
        let mut context = ctx_data.context.lock().unwrap();
        let ready = context.is_ready();
        let v = if ready { context.treatment(&req.experiment_name) } else { 0 };
        (v, ready)
    };

    let new_events = if is_ready {
        ctx_data.event_collector.get_events_since(events_before)
    } else {
        vec![]
    };

    Ok(Json(ApiResponse {
        result: Value::Number(variant.into()),
        events: new_events,
        error: None,
    }))
}

async fn peek_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<TreatmentRequest>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();

    let variant = {
        let mut context = ctx_data.context.lock().unwrap();
        context.peek(&req.experiment_name)
    };

    let new_events = ctx_data.event_collector.get_events_since(events_before);

    Ok(Json(ApiResponse {
        result: Value::Number(variant.into()),
        events: new_events,
        error: None,
    }))
}

async fn variable_value_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<VariableValueRequest>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();

    let (value, is_ready) = {
        let mut context = ctx_data.context.lock().unwrap();
        let ready = context.is_ready();
        let v = if ready {
            context.variable_value(&req.key, req.default_value.clone())
        } else {
            req.default_value
        };
        (v, ready)
    };

    let new_events = if is_ready {
        ctx_data.event_collector.get_events_since(events_before)
    } else {
        vec![]
    };

    Ok(Json(ApiResponse {
        result: value,
        events: new_events,
        error: None,
    }))
}

async fn peek_variable_value_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<VariableValueRequest>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();

    let value = {
        let mut context = ctx_data.context.lock().unwrap();
        context.peek_variable_value(&req.key, req.default_value)
    };

    let new_events = ctx_data.event_collector.get_events_since(events_before);

    Ok(Json(ApiResponse {
        result: value,
        events: new_events,
        error: None,
    }))
}

async fn track_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<TrackRequest>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();

    let properties = req.properties.unwrap_or(Value::Null);

    if !properties.is_null() && !properties.is_object() {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(json!({"error": format!("Goal '{}' properties must be of type object.", req.goal_name)})),
        ));
    }

    {
        let mut context = ctx_data.context.lock().unwrap();
        if let Err(e) = context.track(&req.goal_name, properties) {
            return Err((StatusCode::BAD_REQUEST, Json(json!({"error": e}))));
        }
    }

    let new_events = ctx_data.event_collector.get_events_since(events_before);

    Ok(Json(ApiResponse {
        result: Value::Null,
        events: new_events,
        error: None,
    }))
}

async fn override_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<OverrideRequest>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;

    {
        let mut context = ctx_data.context.lock().unwrap();
        if let Err(e) = context.set_override(&req.experiment_name, req.variant) {
            let msg = e.to_string().to_lowercase();
            if msg.contains("closed") || msg.contains("closing") || msg.contains("finalized") {
                return Ok(Json(ApiResponse {
                    result: Value::Null,
                    events: vec![],
                    error: None,
                }));
            }
            return Err((StatusCode::BAD_REQUEST, Json(json!({"error": e}))));
        }
    }

    Ok(Json(ApiResponse {
        result: Value::Null,
        events: vec![],
        error: None,
    }))
}

async fn custom_assignment_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<OverrideRequest>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;

    {
        let mut context = ctx_data.context.lock().unwrap();
        if let Err(e) = context.set_custom_assignment(&req.experiment_name, req.variant) {
            return Err((StatusCode::BAD_REQUEST, Json(json!({"error": e}))));
        }
    }

    Ok(Json(ApiResponse {
        result: Value::Null,
        events: vec![],
        error: None,
    }))
}

async fn custom_field_value_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<CustomFieldValueRequest>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();

    let value = {
        let context = ctx_data.context.lock().unwrap();
        context.custom_field_value(&req.experiment_name, &req.field_name)
    };

    let new_events = ctx_data.event_collector.get_events_since(events_before);

    Ok(Json(ApiResponse {
        result: value.unwrap_or(Value::Null),
        events: new_events,
        error: None,
    }))
}

async fn variable_keys_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();

    let keys = {
        let context = ctx_data.context.lock().unwrap();
        if !context.is_ready() {
            return Ok(Json(ApiResponse {
                result: Value::Array(vec![]),
                events: vec![],
                error: None,
            }));
        }
        context.variable_keys()
    };

    let keys_list: Vec<String> = keys.keys().cloned().collect();

    let new_events = ctx_data.event_collector.get_events_since(events_before);

    Ok(Json(ApiResponse {
        result: serde_json::to_value(keys_list).unwrap_or(Value::Array(vec![])),
        events: new_events,
        error: None,
    }))
}

async fn custom_field_keys_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();

    let keys = {
        let context = ctx_data.context.lock().unwrap();
        context.custom_field_keys()
    };

    let new_events = ctx_data.event_collector.get_events_since(events_before);

    Ok(Json(ApiResponse {
        result: serde_json::to_value(keys).unwrap_or(Value::Array(vec![])),
        events: new_events,
        error: None,
    }))
}

#[derive(Deserialize)]
struct CustomFieldValueTypeRequest {
    #[serde(rename = "experimentName")]
    experiment_name: String,
    #[serde(rename = "fieldName")]
    field_name: String,
}

async fn custom_field_value_type_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<CustomFieldValueTypeRequest>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();

    let value_type = {
        let context = ctx_data.context.lock().unwrap();
        context.custom_field_value_type(&req.experiment_name, &req.field_name)
    };

    let new_events = ctx_data.event_collector.get_events_since(events_before);

    Ok(Json(ApiResponse {
        result: value_type.map(Value::String).unwrap_or(Value::Null),
        events: new_events,
        error: None,
    }))
}

async fn pending_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;

    let pending = {
        let context = ctx_data.context.lock().unwrap();
        context.pending()
    };

    Ok(Json(ApiResponse {
        result: Value::Number(pending.into()),
        events: vec![],
        error: None,
    }))
}

async fn is_finalized_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;

    let finalized = {
        let context = ctx_data.context.lock().unwrap();
        context.is_finalized()
    };

    Ok(Json(ApiResponse {
        result: Value::Bool(finalized),
        events: vec![],
        error: None,
    }))
}

async fn is_ready_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;

    let ready = {
        let context = ctx_data.context.lock().unwrap();
        context.is_ready()
    };

    Ok(Json(ApiResponse {
        result: Value::Bool(ready),
        events: vec![],
        error: None,
    }))
}

async fn is_failed_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;

    let failed = {
        let context = ctx_data.context.lock().unwrap();
        context.is_failed()
    };

    Ok(Json(ApiResponse {
        result: Value::Bool(failed),
        events: vec![],
        error: None,
    }))
}

async fn experiments_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;

    let experiments = {
        let context = ctx_data.context.lock().unwrap();
        if !context.is_ready() {
            return Ok(Json(ApiResponse {
                result: Value::Array(vec![]),
                events: vec![],
                error: None,
            }));
        }
        context.experiments()
    };

    Ok(Json(ApiResponse {
        result: serde_json::to_value(experiments).unwrap_or(Value::Array(vec![])),
        events: vec![],
        error: None,
    }))
}

async fn get_units_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();
    let result = {
        let context = ctx_data.context.lock().unwrap();
        let units = context.get_units();
        let mut map = serde_json::Map::new();
        for (k, v) in units {
            if let Ok(n) = v.parse::<i64>() {
                map.insert(k.clone(), Value::Number(n.into()));
            } else if let Ok(f) = v.parse::<f64>() {
                map.insert(k.clone(), serde_json::Number::from_f64(f).map(Value::Number).unwrap_or(Value::String(v.clone())));
            } else {
                map.insert(k.clone(), Value::String(v.clone()));
            }
        }
        Value::Object(map)
    };
    let new_events = ctx_data.event_collector.get_events_since(events_before);
    Ok(Json(ApiResponse { result, events: new_events, error: None }))
}

async fn get_attributes_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();
    let result = {
        let context = ctx_data.context.lock().unwrap();
        context.get_attributes()
    };
    let new_events = ctx_data.event_collector.get_events_since(events_before);
    Ok(Json(ApiResponse { result: serde_json::to_value(result).unwrap_or(Value::Object(serde_json::Map::new())), events: new_events, error: None }))
}

async fn ready_error_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let result = {
        let context = ctx_data.context.lock().unwrap();
        context
            .ready_error()
            .map(|e| json!({"isError": true, "message": e.clone()}))
            .unwrap_or(Value::Null)
    };
    Ok(Json(ApiResponse { result, events: vec![], error: None }))
}

async fn variable_keys_map_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();
    let keys = {
        let context = ctx_data.context.lock().unwrap();
        context.variable_keys()
    };
    let new_events = ctx_data.event_collector.get_events_since(events_before);
    Ok(Json(ApiResponse { result: serde_json::to_value(keys).unwrap_or(Value::Object(serde_json::Map::new())), events: new_events, error: None }))
}

async fn global_custom_field_keys_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();
    let keys = {
        let context = ctx_data.context.lock().unwrap();
        context.custom_field_keys()
    };
    let new_events = ctx_data.event_collector.get_events_since(events_before);
    Ok(Json(ApiResponse { result: serde_json::to_value(keys).unwrap_or(Value::Array(vec![])), events: new_events, error: None }))
}

async fn publish_fail_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    ctx_data.publish_fail.store(true, std::sync::atomic::Ordering::Relaxed);
    Ok(Json(ApiResponse { result: Value::Null, events: vec![], error: None }))
}

async fn publish_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;

    if ctx_data.publish_fail.load(std::sync::atomic::Ordering::Relaxed) {
        ctx_data.publish_fail.store(false, std::sync::atomic::Ordering::Relaxed);
        return Err((StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"error": "publish failed"}))));
    }

    let events_before = ctx_data.event_collector.len();

    if ctx_data.is_e2e {
        // E2E mode: drive the SDK's awaitable publish so the HTTP PUT actually
        // completes before we return (the sync Context::publish is fire-and-forget).
        let params = {
            let mut context = ctx_data.context.lock().unwrap();
            if context.pending() == 0 {
                None
            } else {
                Some(context.get_publish_params())
            }
        };

        if let Some(params) = params {
            // Mirror Context::publish's event logging so the response shape is unchanged.
            ctx_data.event_collector.push(
                "publish",
                Some(serde_json::to_value(&params).unwrap_or_default()),
            );

            if let Err(e) = ctx_data.sdk.publish(&params).await {
                return Err((
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"error": format!("publish failed: {}", e)})),
                ));
            }
        }
    } else {
        let mut context = ctx_data.context.lock().unwrap();
        context.publish();
    }

    let new_events = ctx_data.event_collector.get_events_since(events_before);

    Ok(Json(ApiResponse {
        result: Value::Null,
        events: new_events,
        error: None,
    }))
}

async fn refresh_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<RefreshRequest>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();

    {
        let mut context = ctx_data.context.lock().unwrap();
        context.refresh_with(req.new_data);
    }

    let new_events = ctx_data.event_collector.get_events_since(events_before);

    Ok(Json(ApiResponse {
        result: Value::Null,
        events: new_events,
        error: None,
    }))
}

async fn finalize_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();

    if ctx_data.is_e2e {
        // E2E mode: DefaultContextPublisher's send is fire-and-forget, so finalize()
        // would return before the HTTP PUT lands and the batch would race verification.
        // Mirror publish_handler: drive the SDK's awaitable publish for any pending
        // events, then emit the finalize event so the collector receives the batch
        // before we respond.
        let params = {
            let mut context = ctx_data.context.lock().unwrap();
            if context.is_finalized() || context.pending() == 0 {
                None
            } else {
                Some(context.get_publish_params())
            }
        };

        if let Some(params) = params {
            ctx_data.event_collector.push(
                "publish",
                Some(serde_json::to_value(&params).unwrap_or_default()),
            );

            if let Err(e) = ctx_data.sdk.publish(&params).await {
                return Err((
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"error": format!("finalize publish failed: {}", e)})),
                ));
            }
        }

        ctx_data.event_collector.push("finalize", None);
    } else {
        let mut context = ctx_data.context.lock().unwrap();
        context.finalize();
    }

    let new_events = ctx_data.event_collector.get_events_since(events_before);

    Ok(Json(ApiResponse {
        result: Value::Null,
        events: new_events,
        error: None,
    }))
}

async fn delete_context_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> impl IntoResponse {
    let mut contexts = state.contexts.write().unwrap();
    contexts.remove(&context_id);

    Json(json!({ "result": "deleted" }))
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt::init();

    let state = Arc::new(AppState::new());

    let app = Router::new()
        .route("/health", get(health_handler))
        .route("/capabilities", get(capabilities_handler))
        .route("/diagnostic", post(diagnostic_handler))
        .route("/context_payload/{payloadId}", put(store_payload_handler))
        .route("/context_payload/{payloadId}/context", get(mock_api_context_handler))
        .route("/context", post(create_context_handler))
        .route("/context/{contextId}/setUnit", post(set_unit_handler))
        .route("/context/{contextId}/getUnit", post(get_unit_handler))
        .route("/context/{contextId}/attribute", post(set_attribute_handler))
        .route("/context/{contextId}/getAttribute", post(get_attribute_handler))
        .route("/context/{contextId}/treatment", post(treatment_handler))
        .route("/context/{contextId}/peek", post(peek_handler))
        .route("/context/{contextId}/variableValue", post(variable_value_handler))
        .route("/context/{contextId}/peekVariableValue", post(peek_variable_value_handler))
        .route("/context/{contextId}/track", post(track_handler))
        .route("/context/{contextId}/override", post(override_handler))
        .route("/context/{contextId}/customAssignment", post(custom_assignment_handler))
        .route("/context/{contextId}/customFieldValue", post(custom_field_value_handler))
        .route("/context/{contextId}/variableKeys", post(variable_keys_handler))
        .route("/context/{contextId}/customFieldKeys", post(custom_field_keys_handler))
        .route("/context/{contextId}/customFieldValueType", post(custom_field_value_type_handler))
        .route("/context/{contextId}/setOverride", post(override_handler))
        .route("/context/{contextId}/setCustomAssignment", post(custom_assignment_handler))
        .route("/context/{contextId}/pending", get(pending_handler))
        .route("/context/{contextId}/isFinalized", get(is_finalized_handler))
        .route("/context/{contextId}/isReady", get(is_ready_handler))
        .route("/context/{contextId}/isFailed", get(is_failed_handler))
        .route("/context/{contextId}/experiments", get(experiments_handler))
        .route("/context/{contextId}/getUnits", post(get_units_handler))
        .route("/context/{contextId}/getAttributes", post(get_attributes_handler))
        .route("/context/{contextId}/readyError", post(ready_error_handler))
        .route("/context/{contextId}/variableKeysMap", post(variable_keys_map_handler))
        .route("/context/{contextId}/globalCustomFieldKeys", post(global_custom_field_keys_handler))
        .route("/context/{contextId}/publishFail", post(publish_fail_handler))
        .route("/context/{contextId}/publish", post(publish_handler))
        .route("/context/{contextId}/refresh", post(refresh_handler))
        .route("/context/{contextId}/finalize", post(finalize_handler))
        .route("/context/{contextId}", delete(delete_context_handler))
        .layer(CorsLayer::permissive())
        .with_state(state);

    let port = std::env::var("PORT").unwrap_or_else(|_| "3000".to_string());
    let listener = tokio::net::TcpListener::bind(format!("0.0.0.0:{}", port))
        .await
        .unwrap();

    tracing::info!("Rust SDK wrapper listening on port {}", port);

    axum::serve(listener, app).await.unwrap();
}
