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
use std::time::{SystemTime, UNIX_EPOCH};
use tower_http::cors::CorsLayer;
use uuid::Uuid;

use absmartly_sdk::{ABsmartly, Context, ContextData};

fn now_millis() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
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

async fn health_handler() -> impl IntoResponse {
    Json(json!({
        "status": "healthy",
        "sdk": "rust",
        "version": "0.1.0"
    }))
}

async fn capabilities_handler() -> impl IntoResponse {
    Json(json!({
        "asyncContext": true,
        "attrsSeq": false
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
    let context_id = format!("ctx-{}", Uuid::new_v4());
    let event_collector = Arc::new(EventCollector::new());

    let units: HashMap<String, String> = req
        .units
        .iter()
        .map(|(k, v)| (k.clone(), value_to_string(v)))
        .collect();

    let has_payload_throttle = req.options.as_ref()
        .and_then(|opts| opts.get("payloadThrottle"))
        .map(|v| !v.is_null())
        .unwrap_or(false);

    let (mut context, ready, failed) = if let Some(data) = req.data {
        let sdk = ABsmartly::new("http://dummy", "test-key", "test-app", "test-env").expect("Failed to create SDK");
        let ctx = sdk.create_context_with(units, data.clone(), None);
        event_collector.push("ready", Some(serde_json::to_value(&data).unwrap_or_default()));
        (ctx, true, false)
    } else if let Some(endpoint) = req.endpoint {
        if has_payload_throttle {
            let mut ctx = Context::new_loading();
            for (unit_type, uid) in &units {
                let _ = ctx.set_unit(unit_type, uid);
            }

            let ec = event_collector.clone();
            ctx.set_event_logger(Box::new(move |_ctx, event_name, data| {
                ec.push(event_name, data);
            }));

            let context_data = Arc::new(ContextData_ {
                context: Mutex::new(ctx),
                event_collector: event_collector.clone(),
            });

            {
                let mut contexts = state.contexts.write().unwrap();
                contexts.insert(context_id.clone(), context_data.clone());
            }

            let endpoint_clone = endpoint.clone();
            let context_data_clone = context_data.clone();
            let event_collector_clone = event_collector.clone();

            tokio::spawn(async move {
                let sdk = ABsmartly::new(&endpoint_clone, "test-key", "test-app", "test-env").expect("Failed to create SDK");

                let result: Result<Context, _> = sdk.create_context(HashMap::<String, String>::new(), None).await;
                match result {
                    Ok(ready_ctx) => {
                        let data: ContextData = ready_ctx.data().clone();
                        {
                            let mut context = context_data_clone.context.lock().unwrap();
                            context.become_ready(data.clone());
                        }
                        event_collector_clone.push("ready", Some(serde_json::to_value(&data).unwrap_or_default()));
                    }
                    Err(e) => {
                        {
                            let mut context = context_data_clone.context.lock().unwrap();
                            context.become_failed();
                        }
                        event_collector_clone.push("error", Some(json!({"message": format!("Failed to fetch context: {}", e)})));
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
            });
        } else {
            let payload_data = if let Some(payload_id) = endpoint
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
                event_collector.push("error", Some(json!({"message": format!("Payload not found for endpoint: {}", endpoint)})));
                (ctx, true, true)
            }
        }
    } else {
        let sdk = ABsmartly::new("http://dummy", "test-key", "test-app", "test-env").expect("Failed to create SDK");
        let ctx = sdk.create_context_with(units, ContextData::default(), None);
        event_collector.push("ready", Some(serde_json::to_value(&ContextData::default()).unwrap_or_default()));
        (ctx, true, false)
    };

    let ec = event_collector.clone();
    context.set_event_logger(Box::new(move |_ctx: &Context, event_name: &str, data: Option<Value>| {
        ec.push(event_name, data);
    }));

    let finalized = context.is_finalized();

    let context_data = Arc::new(ContextData_ {
        context: Mutex::new(context),
        event_collector: event_collector.clone(),
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

    let variant = {
        let mut context = ctx_data.context.lock().unwrap();
        context.treatment(&req.experiment_name)
    };

    let new_events = ctx_data.event_collector.get_events_since(events_before);

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

    let value = {
        let mut context = ctx_data.context.lock().unwrap();
        context.variable_value(&req.key, req.default_value)
    };

    let new_events = ctx_data.event_collector.get_events_since(events_before);

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
        context.experiments()
    };

    Ok(Json(ApiResponse {
        result: serde_json::to_value(experiments).unwrap_or(Value::Array(vec![])),
        events: vec![],
        error: None,
    }))
}

async fn publish_handler(
    State(state): State<Arc<AppState>>,
    Path(context_id): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, Json<Value>)> {
    let ctx_data = get_context(&state, &context_id)?;
    let events_before = ctx_data.event_collector.len();

    {
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
        context.refresh(req.new_data);
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

    {
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
