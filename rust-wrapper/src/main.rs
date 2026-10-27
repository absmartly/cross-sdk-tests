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

use absmartly_sdk::{
    utils, ABsmartly, Context, ContextData, ContextOptions, ContextPublisher, PublishParams,
};
use std::sync::atomic::{AtomicBool, Ordering};

/// Fails the next publish once when armed by /publishFail. Otherwise, in e2e
/// mode it sends through the SDK's own `ABsmartly::publish` (same request and
/// headers as the SDK's default publisher, blocking on the multi-thread
/// runtime the same way); in mock mode there is no collector, so it delivers
/// nothing.
struct ArmablePublisher {
    fail_next: Arc<AtomicBool>,
    sdk: Option<Arc<ABsmartly>>,
}

impl ContextPublisher for ArmablePublisher {
    fn publish(&self, params: &PublishParams) -> Result<(), String> {
        if self.fail_next.swap(false, Ordering::SeqCst) {
            return Err("Publish failed".to_string());
        }
        match &self.sdk {
            Some(sdk) => tokio::task::block_in_place(|| {
                tokio::runtime::Handle::current().block_on(sdk.publish(params))
            })
            .map_err(|e| format!("Publish failed: {}", e)),
            None => Ok(()),
        }
    }
}

fn logging_options(ec: &Arc<EventCollector>) -> ContextOptions {
    let ec = ec.clone();
    ContextOptions {
        publish_delay: -1,
        refresh_period: 0,
        event_logger: Some(Arc::new(move |_ctx: &Context, name: &str, data: Option<Value>| ec.push(name, data))),
    }
}

/// SDK pointed at a mock collector endpoint (e.g. `/context_payload/{id}`);
/// the SDK appends `/context` and fetches it itself.
fn mock_endpoint_sdk(endpoint: &str) -> Result<ABsmartly, absmartly_sdk::SDKError> {
    ABsmartly::builder()
        .endpoint(endpoint)
        .api_key("test-key")
        .application("test-app")
        .environment("test-env")
        .retries(1)
        .build()
}

/// Refresh fetcher for a context resolved outside `create_context` (the
/// throttled path). It fetches through the SDK's own client, as the fetcher
/// the SDK installs in `create_context` does.
fn sdk_data_fetcher(sdk: Arc<ABsmartly>) -> absmartly_sdk::context::DataFetcher {
    Box::new(move || {
        tokio::task::block_in_place(|| {
            tokio::runtime::Handle::current().block_on(async {
                sdk.create_context(HashMap::<String, Value>::new(), None)
                    .await
                    .map_err(|e| format!("Refresh fetch failed: {}", e))
                    .and_then(|c| c.data().map(|d| d.clone()))
            })
        })
    })
}

type HandlerError = (StatusCode, Json<Value>);

/// An SDK error as an error response carrying the events the SDK emitted
/// during the call.
fn err_with_events(status: StatusCode, e: impl ToString, ec: &EventCollector, since: usize) -> HandlerError {
    (status, Json(json!({"error": e.to_string(), "events": ec.get_events_since(since)})))
}

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
    publish_fail: Arc<AtomicBool>,
    /// The context's data comes from an endpoint the SDK fetches (mock
    /// collector or e2e), so /refresh re-fetches through the SDK.
    fetches_from_endpoint: bool,
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
    #[serde(rename = "newData", default)]
    new_data: Option<ContextData>,
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
        "diagnosticsUtf8": true,
        "diagnosticsBase64": true,
        "diagnosticsTypeChecks": true,
        "diagnosticsIsPromise": false,
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
        "hashUnit" => match utils::hash_unit_value(&value) {
            Some(hash) => Value::String(hash),
            None => {
                return Err((
                    StatusCode::BAD_REQUEST,
                    Json(json!({ "error": "hashUnit takes a string or a number", "events": [] })),
                ))
            }
        },
        "base64UrlNoPadding" => {
            let input = value.as_str().map(|s| s.to_string()).unwrap_or_else(|| value.to_string());
            Value::String(utils::base64_url_no_padding(&utils::utf8_bytes(&input)))
        }
        "utf8Bytes" => {
            let input = value.as_str().map(|s| s.to_string()).unwrap_or_else(|| value.to_string());
            json!(utils::utf8_bytes(&input))
        }
        "isObject" => Value::Bool(utils::is_object(&value)),
        "isNumeric" => Value::Bool(utils::is_numeric(&value)),
        _ => {
            return Err((
                StatusCode::BAD_REQUEST,
                Json(json!({ "error": format!("Unsupported diagnostic operation: {}", req.operation), "events": [] })),
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

// Read a stored payload; `?throttle=<ms>` delays the response.
async fn get_payload_handler(
    State(state): State<Arc<AppState>>,
    Path(payload_id): Path<String>,
    axum::extract::Query(query): axum::extract::Query<HashMap<String, String>>,
) -> impl IntoResponse {
    let throttle_ms = query.get("throttle").and_then(|t| t.parse::<u64>().ok()).unwrap_or(0);
    if throttle_ms > 0 {
        tokio::time::sleep(Duration::from_millis(throttle_ms)).await;
    }
    let data = state.payloads.read().unwrap().get(&payload_id).cloned();
    match data {
        Some(data) => Json(data).into_response(),
        None => (StatusCode::NOT_FOUND, Json(json!({"error": "payload not found"}))).into_response(),
    }
}

// Mock ABsmartly API - SDK calls GET /context_payload/{payloadId}/context?application=...&environment=...
async fn mock_api_context_handler(
    State(state): State<Arc<AppState>>,
    Path(payload_id): Path<String>,
) -> impl IntoResponse {
    let payloads = state.payloads.read().unwrap();
    match payloads.get(&payload_id).cloned() {
        Some(data) => Json(data).into_response(),
        None => (StatusCode::NOT_FOUND, Json(json!({"error": "payload not found"}))).into_response(),
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

        let units: HashMap<String, Value> = req.units.clone();

        let sdk = match ABsmartly::new(&e2e_endpoint, &e2e_api_key, &e2e_app, &e2e_env) {
            Ok(s) => Arc::new(s),
            Err(e) => {
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"error": format!("Failed to create SDK: {}", e), "events": []})),
                ).into_response();
            }
        };

        // create_context returns Err on load failure; surface the real SDK
        // error rather than fabricating a failed context.
        let mut context = match sdk.create_context(units.clone(), Some(logging_options(&event_collector))).await {
            Ok(ctx) => ctx,
            Err(e) => {
                return (
                    StatusCode::BAD_GATEWAY,
                    Json(json!({"error": format!("{}", e), "events": event_collector.get_events_since(0)})),
                ).into_response();
            }
        };
        let ready = context.is_ready();
        let failed = context.is_failed();

        if let Some(attributes) = &req.attributes {
            for (name, value) in attributes {
                let _ = context.set_attribute(name, value.clone());
            }
        }

        let publish_fail = Arc::new(AtomicBool::new(false));
        context.set_publisher(Box::new(ArmablePublisher {
            fail_next: publish_fail.clone(),
            sdk: Some(sdk.clone()),
        }));

        let finalized = context.is_finalized();

        let context_data = Arc::new(ContextData_ {
            context: Mutex::new(context),
            event_collector: event_collector.clone(),
            publish_fail,
            fetches_from_endpoint: true,
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

    let units: HashMap<String, Value> = req.units.clone();

    let payload_throttle_ms = req.options.as_ref()
        .and_then(|opts| opts.get("payloadThrottle"))
        .and_then(|v| v.as_u64())
        .unwrap_or(0);

    let fetches_from_endpoint = req.data.is_none() && req.endpoint.is_some();

    let (mut context, ready, failed) = if let Some(data) = req.data {
        let sdk = ABsmartly::new("http://dummy", "test-key", "test-app", "test-env").expect("Failed to create SDK");
        let ctx = sdk.create_context_with(units, data, Some(logging_options(&event_collector)));
        let (ready, failed) = (ctx.is_ready(), ctx.is_failed());
        (ctx, ready, failed)
    } else if let Some(endpoint) = req.endpoint {
        let translated_endpoint = translate_endpoint(&endpoint);
        // The SDK fetches `{endpoint}/context` itself in both the throttled and
        // the immediate path. The mock collector route answers 404 for an
        // unknown payload, so a missing payload fails the context (with
        // ready_error and an `error` event) instead of loading as empty data.
        let sdk = match mock_endpoint_sdk(&translated_endpoint) {
            Ok(sdk) => Arc::new(sdk),
            Err(e) => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({"error": format!("Failed to create SDK: {}", e), "events": []})),
                ).into_response();
            }
        };

        if payload_throttle_ms > 0 {
            // The context exists (not ready) before its data arrives, so calls
            // made meanwhile (e.g. track before ready) land on this context.
            let mut ctx = Context::new_loading_with_options(logging_options(&event_collector));
            for (unit_type, uid) in &units {
                if let Err(e) = ctx.set_unit(unit_type, uid.clone()) {
                    return err_with_events(StatusCode::BAD_REQUEST, e, &event_collector, 0).into_response();
                }
            }
            let publish_fail = Arc::new(AtomicBool::new(false));
            ctx.set_publisher(Box::new(ArmablePublisher { fail_next: publish_fail.clone(), sdk: None }));
            ctx.set_data_fetcher(sdk_data_fetcher(sdk.clone()));

            let (ready, failed, finalized) = (ctx.is_ready(), ctx.is_failed(), ctx.is_finalized());
            let context_data = Arc::new(ContextData_ {
                context: Mutex::new(ctx),
                event_collector: event_collector.clone(),
                publish_fail,
                fetches_from_endpoint: true,
            });

            {
                let mut contexts = state.contexts.write().unwrap();
                contexts.insert(context_id.clone(), context_data.clone());
            }

            let context_data_clone = context_data.clone();
            let throttle_delay = payload_throttle_ms;

            tokio::spawn(async move {
                tokio::time::sleep(Duration::from_millis(throttle_delay)).await;

                // Fetch through the SDK's own client. The temporary context is
                // only a carrier for the fetched data: the stored context
                // already holds the units and the event logger, and resolving
                // it below emits the single `ready`/`error` event.
                let fetched = sdk
                    .create_context(HashMap::<String, String>::new(), None)
                    .await
                    .map_err(|e| e.to_string())
                    .and_then(|c| c.data().map(|d| d.clone()));
                let mut context = context_data_clone.context.lock().unwrap();
                match fetched {
                    Ok(data) => context.become_ready(data),
                    Err(e) => {
                        tracing::warn!("deferred context fetch failed: {}", e);
                        context.become_failed_with_error(e.to_string());
                    }
                }
            });

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
        } else {
            let ctx = sdk.create_context_or_failed(units, Some(logging_options(&event_collector))).await;
            let (ready, failed) = (ctx.is_ready(), ctx.is_failed());
            (ctx, ready, failed)
        }
    } else if req.fail_load {
        let sdk = ABsmartly::builder()
            .endpoint("http://127.0.0.1:1")
            .api_key("test-key").application("test-app").environment("test-env")
            .retries(1).build().expect("Failed to create SDK");
        let ctx = sdk.create_context_or_failed(units, Some(logging_options(&event_collector))).await;
        let (ready, failed) = (ctx.is_ready(), ctx.is_failed());
        (ctx, ready, failed)
    } else {
        let sdk = ABsmartly::new("http://dummy", "test-key", "test-app", "test-env").expect("Failed to create SDK");
        let ctx = sdk.create_context_with(units, ContextData::default(), Some(logging_options(&event_collector)));
        let (ready, failed) = (ctx.is_ready(), ctx.is_failed());
        (ctx, ready, failed)
    };

    let publish_fail = Arc::new(AtomicBool::new(false));
    context.set_publisher(Box::new(ArmablePublisher { fail_next: publish_fail.clone(), sdk: None }));

    let finalized = context.is_finalized();

    let context_data = Arc::new(ContextData_ {
        context: Mutex::new(context),
        event_collector: event_collector.clone(),
        publish_fail,
        fetches_from_endpoint,
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
            Json(json!({"error": "context not found", "events": []})),
        )
    })
}

type HandlerResult = Result<Json<ApiResponse>, HandlerError>;

/// Run one SDK call on the context and return its result together with the
/// events the SDK emitted during the call, on success and on error.
fn with_context(
    state: &Arc<AppState>,
    context_id: &str,
    error_status: StatusCode,
    call: impl FnOnce(&mut Context) -> Result<Value, String>,
) -> HandlerResult {
    let ctx_data = get_context(state, context_id)?;
    let events_before = ctx_data.event_collector.len();
    // SDK calls are synchronous and may block (refresh/publish do HTTP, which
    // in mock mode hits this same server). block_in_place hands this worker's
    // queued tasks to other workers first; otherwise a task parked here (e.g.
    // the one serving the SDK's own fetch) stalls until the SDK times out.
    let outcome = tokio::task::block_in_place(|| {
        let mut context = ctx_data.context.lock().unwrap();
        call(&mut context)
    });
    match outcome {
        Ok(result) => Ok(Json(ApiResponse {
            result,
            events: ctx_data.event_collector.get_events_since(events_before),
            error: None,
        })),
        Err(e) => Err(err_with_events(error_status, e, &ctx_data.event_collector, events_before)),
    }
}

fn to_json<T: Serialize>(value: T) -> Value {
    serde_json::to_value(value).unwrap_or(Value::Null)
}

const BAD: StatusCode = StatusCode::BAD_REQUEST;

async fn set_unit_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<SetUnitRequest>,
) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| c.set_unit(&req.unit_type, req.uid).map(|_| Value::Null))
}

async fn get_unit_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<GetUnitRequest>,
) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| Ok(c.get_unit(&req.unit_type).cloned().unwrap_or(Value::Null)))
}

async fn set_attribute_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<SetAttributeRequest>,
) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| c.set_attribute(&req.name, req.value).map(|_| Value::Null))
}

async fn get_attribute_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<GetAttributeRequest>,
) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| Ok(c.get_attribute(&req.name).cloned().unwrap_or(Value::Null)))
}

async fn treatment_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<TreatmentRequest>,
) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| c.treatment(&req.experiment_name).map(|v| json!(v)))
}

async fn peek_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<TreatmentRequest>,
) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| c.peek(&req.experiment_name).map(|v| json!(v)))
}

async fn variable_value_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<VariableValueRequest>,
) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| c.variable_value(&req.key, req.default_value))
}

async fn peek_variable_value_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<VariableValueRequest>,
) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| c.peek_variable_value(&req.key, req.default_value))
}

async fn track_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<TrackRequest>,
) -> HandlerResult {
    let properties = req.properties.unwrap_or(Value::Null);
    with_context(&state, &context_id, BAD, |c| c.track(&req.goal_name, properties).map(|_| Value::Null))
}

async fn override_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<OverrideRequest>,
) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| c.set_override(&req.experiment_name, req.variant).map(|_| Value::Null))
}

async fn custom_assignment_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<OverrideRequest>,
) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| {
        c.set_custom_assignment(&req.experiment_name, req.variant).map(|_| Value::Null)
    })
}

async fn custom_field_value_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    Json(req): Json<CustomFieldValueRequest>,
) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| {
        c.custom_field_value(&req.experiment_name, &req.field_name).map(|v| v.unwrap_or(Value::Null))
    })
}

async fn variable_keys_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| {
        c.variable_keys().map(|keys| to_json(keys.keys().cloned().collect::<Vec<String>>()))
    })
}

async fn custom_field_keys_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| c.custom_field_keys().map(to_json))
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
) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| {
        c.custom_field_value_type(&req.experiment_name, &req.field_name).map(to_json)
    })
}

async fn pending_handler(State(state): State<Arc<AppState>>, Path(context_id): Path<String>) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| Ok(json!(c.pending())))
}

async fn is_finalized_handler(State(state): State<Arc<AppState>>, Path(context_id): Path<String>) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| Ok(json!(c.is_finalized())))
}

async fn is_ready_handler(State(state): State<Arc<AppState>>, Path(context_id): Path<String>) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| Ok(json!(c.is_ready())))
}

async fn is_failed_handler(State(state): State<Arc<AppState>>, Path(context_id): Path<String>) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| Ok(json!(c.is_failed())))
}

async fn experiments_handler(State(state): State<Arc<AppState>>, Path(context_id): Path<String>) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| c.experiments().map(to_json))
}

async fn get_units_handler(State(state): State<Arc<AppState>>, Path(context_id): Path<String>) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| Ok(to_json(c.get_units())))
}

async fn get_attributes_handler(State(state): State<Arc<AppState>>, Path(context_id): Path<String>) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| Ok(to_json(c.get_attributes())))
}

async fn ready_error_handler(State(state): State<Arc<AppState>>, Path(context_id): Path<String>) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| {
        Ok(c.ready_error()
            .map(|e| json!({"isError": true, "message": e.clone()}))
            .unwrap_or(Value::Null))
    })
}

async fn variable_keys_map_handler(State(state): State<Arc<AppState>>, Path(context_id): Path<String>) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| c.variable_keys().map(to_json))
}

async fn global_custom_field_keys_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> HandlerResult {
    with_context(&state, &context_id, BAD, |c| c.custom_field_keys().map(to_json))
}

async fn publish_fail_handler(State(state): State<Arc<AppState>>, Path(context_id): Path<String>) -> HandlerResult {
    let ctx_data = get_context(&state, &context_id)?;
    ctx_data.publish_fail.store(true, Ordering::SeqCst);
    Ok(Json(ApiResponse { result: Value::Null, events: vec![], error: None }))
}

// Context::publish hands the event to the context's ArmablePublisher (which
// delivers nothing in mock mode and sends via the SDK's ABsmartly::publish in
// e2e mode).
async fn publish_handler(State(state): State<Arc<AppState>>, Path(context_id): Path<String>) -> HandlerResult {
    with_context(&state, &context_id, StatusCode::INTERNAL_SERVER_ERROR, |c| c.publish().map(|_| Value::Null))
}

// Endpoint-backed contexts re-fetch through the SDK's own data fetcher (the
// orchestrator has already stored the new payload at the endpoint), as the JS
// wrapper does. Contexts created from inline data have no endpoint, so the
// supplied newData goes through the SDK's refresh_with.
async fn refresh_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
    body: axum::body::Bytes,
) -> HandlerResult {
    let ctx_data = get_context(&state, &context_id)?;
    let req: RefreshRequest = if body.iter().all(|b| b.is_ascii_whitespace()) {
        RefreshRequest { new_data: None }
    } else {
        serde_json::from_slice(&body).map_err(|e| {
            (BAD, Json(json!({"error": format!("Invalid refresh body: {}", e), "events": []})))
        })?
    };
    let fetches = ctx_data.fetches_from_endpoint;
    with_context(&state, &context_id, StatusCode::INTERNAL_SERVER_ERROR, |c| {
        match (fetches, req.new_data) {
            (false, Some(new_data)) => c.try_refresh_with(new_data),
            _ => c.refresh(),
        }
        .map(|_| Value::Null)
    })
}

async fn finalize_handler(State(state): State<Arc<AppState>>, Path(context_id): Path<String>) -> HandlerResult {
    with_context(&state, &context_id, StatusCode::INTERNAL_SERVER_ERROR, |c| c.finalize().map(|_| Value::Null))
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
        .route("/context_payload/{payloadId}", put(store_payload_handler).get(get_payload_handler))
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
