package main

import (
	gocontext "context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/absmartly/go-sdk/sdk"
	"github.com/absmartly/go-sdk/sdk/future"
	"github.com/absmartly/go-sdk/sdk/jsonmodels"
	"github.com/gorilla/mux"
)

func translateEndpoint(endpoint string) string {
	parsed, err := url.Parse(endpoint)
	if err != nil {
		return endpoint
	}

	host := strings.ToLower(parsed.Hostname())
	// In matrix runs orchestrator can send localhost:<published-port>; inside
	// the SDK container we must target the local service port instead.
	if host == "localhost" || host == "127.0.0.1" || host == "go-sdk" {
		parsed.Host = "localhost:3000"
		return parsed.String()
	}

	return endpoint
}

type EventCollector struct {
	events []Event
	mu     sync.Mutex
}

type Event struct {
	Type      string      `json:"type"`
	Data      interface{} `json:"data"`
	Timestamp int64       `json:"timestamp"`
}

func (ec *EventCollector) Len() int {
	ec.mu.Lock()
	defer ec.mu.Unlock()
	return len(ec.events)
}

func (ec *EventCollector) SliceFrom(start int) []Event {
	ec.mu.Lock()
	defer ec.mu.Unlock()
	result := make([]Event, len(ec.events)-start)
	copy(result, ec.events[start:])
	return result
}

func (ec *EventCollector) HandleEvent(context sdk.Context, eventType sdk.EventType, data interface{}) {
	ec.mu.Lock()
	defer ec.mu.Unlock()

	eventTypeLower := string(eventType)
	switch eventType {
	case sdk.Ready:
		eventTypeLower = "ready"
	case sdk.Refresh:
		eventTypeLower = "refresh"
	case sdk.Publish:
		eventTypeLower = "publish"
	case sdk.Exposure:
		eventTypeLower = "exposure"
	case sdk.Goal:
		eventTypeLower = "goal"
	case sdk.Error:
		eventTypeLower = "error"
	}

	event := Event{
		Type:      eventTypeLower,
		Data:      deepCopy(data),
		Timestamp: time.Now().UnixMilli(),
	}
	ec.events = append(ec.events, event)
}

func deepCopy(src interface{}) interface{} {
	if src == nil {
		return nil
	}
	data, err := json.Marshal(src)
	if err != nil {
		return src
	}
	var dest interface{}
	json.Unmarshal(data, &dest)
	return dest
}

type deferredContextDataProvider struct {
	dataFuture *future.Future
}

func (d *deferredContextDataProvider) GetContextData() *future.Future {
	return d.dataFuture
}

// CustomPublisher is the per-context ContextPublisher handed to the real SDK.
// When armed via /publishFail it fails the next SDK-invoked Publish exactly
// once; what happens to pending events after that failure is decided entirely
// by the SDK's own Flush() logic. Otherwise it forwards to inner (the SDK's
// DefaultContextEventHandler in e2e mode) or, in mock mode where there is no
// collector, delivers nothing.
type CustomPublisher struct {
	eventCollector *EventCollector
	failNext       atomic.Bool
	inner          sdk.ContextEventHandler
}

func (cp *CustomPublisher) Publish(context sdk.Context, event jsonmodels.PublishEvent) *future.Future {
	if cp.failNext.CompareAndSwap(true, false) {
		result, done := future.New()
		done(nil, errors.New("Publish failed"))
		return result
	}
	if cp.inner != nil {
		return cp.inner.Publish(context, event)
	}
	result, done := future.New()
	done(nil, nil)
	return result
}

type ContextData struct {
	context        *sdk.Context
	eventCollector *EventCollector
	publisher      *CustomPublisher
}

var (
	contexts     = make(map[string]*ContextData)
	contextsMu   sync.RWMutex
	payloadStore = make(map[string]jsonmodels.ContextData)
	payloadMu    sync.RWMutex
)

type Response struct {
	Result interface{} `json:"result"`
	Events []Event     `json:"events"`
	Error  string      `json:"error,omitempty"`
}

type CreateContextRequest struct {
	Data       jsonmodels.ContextData `json:"data"`
	Endpoint   string                 `json:"endpoint"`
	Units      map[string]interface{} `json:"units"`
	Options    map[string]interface{} `json:"options"`
	FailLoad   bool                   `json:"failLoad"`
	Mode       string                 `json:"mode"`
	Attributes map[string]interface{} `json:"attributes"`
}

type StorePayloadRequest struct {
	Data jsonmodels.ContextData `json:"data"`
}

type StorePayloadResponse struct {
	PayloadURL string `json:"payloadUrl"`
	PayloadID  string `json:"payloadId"`
}

type CreateContextResponse struct {
	ContextID string `json:"contextId"`
	Ready     bool   `json:"ready"`
	Failed    bool   `json:"failed"`
	Finalized bool   `json:"finalized"`
}

func writeSDKError(w http.ResponseWriter, status int, err error, events []Event) {
	if events == nil {
		events = []Event{}
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(Response{Result: nil, Events: events, Error: err.Error()})
}

func writeResult(w http.ResponseWriter, result interface{}, events []Event) {
	if events == nil {
		events = []Event{}
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(Response{Result: result, Events: events})
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"status":  "healthy",
		"sdk":     "go",
		"version": "1.0.0",
	})
}

func capabilitiesHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]bool{"diagnostics": true,
		"diagnosticsUtf8":       true,
		"diagnosticsBase64":     true,
		"diagnosticsTypeChecks": false,
		"diagnosticsIsPromise":  false,
		"attrsSeq":              false,
		"publishFail":           true,
		"variableKeysMap":       true,
		"globalCustomFieldKeys": true,
		"getUnits":              true,
		"getAttributes":         true,
		// The Go SDK has no ReadyError API (SetDataFailed discards the error),
		// so the wrapper cannot report it without fabricating it.
		"readyError": false,
	})
}

func diagnosticHandler(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Operation string      `json:"operation"`
		Value     interface{} `json:"value"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	var result interface{}
	switch req.Operation {
	case "hashUnit":
		hashed, err := sdk.HashUnitValue(req.Value)
		if err != nil {
			writeSDKError(w, http.StatusBadRequest, err, nil)
			return
		}
		hashedBytes := make([]byte, len(hashed))
		for i, b := range hashed {
			hashedBytes[i] = byte(b)
		}
		result = string(hashedBytes)
	case "base64UrlNoPadding", "utf8Bytes":
		value, ok := req.Value.(string)
		if !ok {
			writeSDKError(w, http.StatusBadRequest, fmt.Errorf("%s expects a string value", req.Operation), nil)
			return
		}
		encoded := sdk.UTF8Bytes(value)
		if req.Operation == "base64UrlNoPadding" {
			result = sdk.Base64UrlNoPadding(encoded)
		} else {
			out := make([]int, len(encoded))
			for i, b := range encoded {
				out[i] = int(b)
			}
			result = out
		}
	default:
		// isObject, isNumeric and isPromise are JavaScript type checks the Go
		// SDK has no counterpart for.
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotImplemented)
		json.NewEncoder(w).Encode(map[string]string{"error": fmt.Sprintf("Unsupported diagnostic operation: %s", req.Operation)})
		return
	}

	writeResult(w, result, nil)
}

func storePayloadHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	payloadID := vars["payloadId"]

	var req StorePayloadRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	payloadMu.Lock()
	payloadStore[payloadID] = req.Data
	payloadMu.Unlock()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]bool{"success": true})
}

func getPayloadHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	payloadID := vars["payloadId"]

	throttleStr := r.URL.Query().Get("throttle")
	throttle := 0
	if throttleStr != "" {
		fmt.Sscanf(throttleStr, "%d", &throttle)
	}

	payloadMu.RLock()
	data, exists := payloadStore[payloadID]
	payloadMu.RUnlock()

	if !exists {
		data = jsonmodels.ContextData{Experiments: []jsonmodels.Experiment{}}
	}

	if throttle > 0 {
		time.Sleep(time.Duration(throttle) * time.Millisecond)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(data)
}

func mockApiContextHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	payloadID := vars["payloadId"]

	payloadMu.RLock()
	data, exists := payloadStore[payloadID]
	payloadMu.RUnlock()

	if !exists {
		data = jsonmodels.ContextData{Experiments: []jsonmodels.Experiment{}}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(data)
}

func createContextHandler(w http.ResponseWriter, r *http.Request) {
	var req CreateContextRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	eventCollector := &EventCollector{events: []Event{}}
	customPublisher := &CustomPublisher{eventCollector: eventCollector}

	contextConfig := sdk.ContextConfig{
		Attributes_:      make(map[string]interface{}),
		Overrides_:       make(map[string]int),
		Cassigmnents_:    make(map[string]int),
		EventLogger_:     eventCollector,
		PublishDelay_:    int64(-1),
		RefreshInterval_: int64(0),
	}

	var absmartly *sdk.ABSmartly
	var data *jsonmodels.ContextData
	throttled := false
	switch {
	case req.Mode == "e2e":
		e2eEndpoint := os.Getenv("ABSMARTLY_E2E_ENDPOINT")
		e2eApiKey := os.Getenv("ABSMARTLY_E2E_API_KEY")
		e2eApplication := os.Getenv("ABSMARTLY_E2E_APPLICATION")
		e2eEnvironment := os.Getenv("ABSMARTLY_E2E_ENVIRONMENT")
		if e2eEndpoint == "" || e2eApiKey == "" || e2eApplication == "" || e2eEnvironment == "" {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(501)
			json.NewEncoder(w).Encode(map[string]string{"error": "e2e mode not configured"})
			return
		}
		client := sdk.CreateDefaultClient(sdk.ClientConfig{
			Endpoint_:    e2eEndpoint,
			ApiKey_:      e2eApiKey,
			Application_: e2eApplication,
			Environment_: e2eEnvironment,
		})
		customPublisher.inner = sdk.CreateDefaultContextEventHandler(client)
		a := sdk.Create(sdk.ABSmartlyConfig{
			Client_:             client,
			ContextPublisher_:   customPublisher,
			ContextEventLogger_: eventCollector,
		})
		absmartly = &a
	case req.Endpoint != "":
		endpoint := translateEndpoint(req.Endpoint)
		payloadThrottle := 0
		if req.Options != nil {
			if pt, ok := req.Options["payloadThrottle"].(float64); ok {
				payloadThrottle = int(pt)
			}
		}
		if payloadThrottle > 0 {
			throttled = true
			dataFuture, done := future.New()
			go func() {
				time.Sleep(time.Duration(payloadThrottle) * time.Millisecond)
				resp, err := http.Get(endpoint)
				if err != nil {
					done(nil, err)
					return
				}
				defer resp.Body.Close()
				var data jsonmodels.ContextData
				if err := json.NewDecoder(resp.Body).Decode(&data); err != nil {
					done(nil, err)
					return
				}
				done(data, nil)
			}()
			a := sdk.Create(sdk.ABSmartlyConfig{
				ContextDataProvider_: &deferredContextDataProvider{dataFuture: dataFuture},
				ContextPublisher_:    customPublisher,
				ContextEventLogger_:  eventCollector,
			})
			absmartly = &a
		} else {
			client := sdk.CreateDefaultClient(sdk.ClientConfig{
				Endpoint_:    endpoint,
				ApiKey_:      "test-api-key",
				Application_: "test-app",
				Environment_: "test-env",
			})
			a := sdk.Create(sdk.ABSmartlyConfig{
				Client_:             client,
				ContextPublisher_:   customPublisher,
				ContextEventLogger_: eventCollector,
			})
			absmartly = &a
		}
	case req.FailLoad:
		dataFuture, done := future.New()
		done(nil, fmt.Errorf("Context load failed"))
		a := sdk.Create(sdk.ABSmartlyConfig{
			ContextDataProvider_: &deferredContextDataProvider{dataFuture: dataFuture},
			ContextPublisher_:    customPublisher,
			ContextEventLogger_:  eventCollector,
		})
		absmartly = &a
	default:
		a := sdk.Create(sdk.ABSmartlyConfig{
			ContextPublisher_:   customPublisher,
			ContextEventLogger_: eventCollector,
		})
		absmartly = &a
		data = &req.Data
	}

	var context *sdk.Context
	if data != nil {
		context = absmartly.CreateContextWith(contextConfig, *data)
	} else {
		context = absmartly.CreateContext(contextConfig)
	}

	for unitType, uid := range req.Units {
		if err := context.SetUnitValue(unitType, uid); err != nil {
			writeSDKError(w, http.StatusBadRequest, err, eventCollector.SliceFrom(0))
			return
		}
	}
	for name, value := range req.Attributes {
		if err := context.SetAttribute(name, value); err != nil {
			writeSDKError(w, http.StatusBadRequest, err, eventCollector.SliceFrom(0))
			return
		}
	}

	if !throttled {
		context.WaitUntilReady()
	}

	contextID := fmt.Sprintf("ctx-%d-%f", time.Now().UnixNano(), float64(time.Now().UnixNano()%1000000))
	contextsMu.Lock()
	contexts[contextID] = &ContextData{
		context:        context,
		eventCollector: eventCollector,
		publisher:      customPublisher,
	}
	contextsMu.Unlock()

	writeResult(w, CreateContextResponse{
		ContextID: contextID,
		Ready:     context.IsReady(),
		Failed:    context.IsFailed(),
		Finalized: context.IsClosed(),
	}, eventCollector.SliceFrom(0))
}

func getContext(contextID string) (*ContextData, error) {
	contextsMu.RLock()
	defer contextsMu.RUnlock()

	ctx, exists := contexts[contextID]
	if !exists {
		return nil, fmt.Errorf("context not found")
	}
	return ctx, nil
}

type routeFunc func(cd *ContextData, decode func(interface{}) error) (interface{}, error)

func contextRoute(call routeFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ctxData, err := getContext(mux.Vars(r)["contextId"])
		if err != nil {
			http.Error(w, err.Error(), http.StatusNotFound)
			return
		}
		decode := func(v interface{}) error {
			if err := json.NewDecoder(r.Body).Decode(v); err != nil && err != io.EOF {
				return err
			}
			return nil
		}
		eventsBefore := ctxData.eventCollector.Len()
		result, err := call(ctxData, decode)
		events := ctxData.eventCollector.SliceFrom(eventsBefore)
		if err != nil {
			writeSDKError(w, http.StatusBadRequest, err, events)
			return
		}
		writeResult(w, result, events)
	}
}

type experimentRequest struct {
	ExperimentName string `json:"experimentName"`
}

type variantRequest struct {
	ExperimentName string  `json:"experimentName"`
	Variant        float64 `json:"variant"`
}

type fieldRequest struct {
	ExperimentName string `json:"experimentName"`
	FieldName      string `json:"fieldName"`
}

type variableRequest struct {
	Key          string      `json:"key"`
	DefaultValue interface{} `json:"defaultValue"`
}

func setUnit(cd *ContextData, decode func(interface{}) error) (interface{}, error) {
	var req struct {
		UnitType string      `json:"unitType"`
		UID      interface{} `json:"uid"`
	}
	if err := decode(&req); err != nil {
		return nil, err
	}
	return nil, cd.context.SetUnitValue(req.UnitType, req.UID)
}

func getUnit(cd *ContextData, decode func(interface{}) error) (interface{}, error) {
	var req struct {
		UnitType string `json:"unitType"`
	}
	if err := decode(&req); err != nil {
		return nil, err
	}
	return cd.context.GetUnit(req.UnitType), nil
}

func setAttribute(cd *ContextData, decode func(interface{}) error) (interface{}, error) {
	var req struct {
		Name  string      `json:"name"`
		Value interface{} `json:"value"`
	}
	if err := decode(&req); err != nil {
		return nil, err
	}
	return nil, cd.context.SetAttribute(req.Name, req.Value)
}

func getAttribute(cd *ContextData, decode func(interface{}) error) (interface{}, error) {
	var req struct {
		Name string `json:"name"`
	}
	if err := decode(&req); err != nil {
		return nil, err
	}
	return cd.context.GetAttribute(req.Name), nil
}

func treatment(cd *ContextData, decode func(interface{}) error) (interface{}, error) {
	var req experimentRequest
	if err := decode(&req); err != nil {
		return nil, err
	}
	return resultOrNil(cd.context.GetTreatment(req.ExperimentName))
}

func peek(cd *ContextData, decode func(interface{}) error) (interface{}, error) {
	var req experimentRequest
	if err := decode(&req); err != nil {
		return nil, err
	}
	return resultOrNil(cd.context.PeekTreatment(req.ExperimentName))
}

func resultOrNil(v int, err error) (interface{}, error) {
	if err != nil {
		return nil, err
	}
	return v, nil
}

func variableValue(cd *ContextData, decode func(interface{}) error) (interface{}, error) {
	var req variableRequest
	if err := decode(&req); err != nil {
		return nil, err
	}
	return cd.context.GetVariableValue(req.Key, req.DefaultValue)
}

func peekVariableValue(cd *ContextData, decode func(interface{}) error) (interface{}, error) {
	var req variableRequest
	if err := decode(&req); err != nil {
		return nil, err
	}
	return cd.context.PeekVariableValue(req.Key, req.DefaultValue)
}

func track(cd *ContextData, decode func(interface{}) error) (interface{}, error) {
	var req struct {
		GoalName   string          `json:"goalName"`
		Properties json.RawMessage `json:"properties"`
	}
	if err := decode(&req); err != nil {
		return nil, err
	}
	// Track takes a map, so a non-object cannot reach the SDK: reject it at
	// the JSON-to-Go boundary with the SDK family's message.
	var properties map[string]interface{}
	if len(req.Properties) > 0 && string(req.Properties) != "null" {
		if err := json.Unmarshal(req.Properties, &properties); err != nil {
			return nil, fmt.Errorf("Goal '%s' properties must be of type object.", req.GoalName)
		}
	}
	return nil, cd.context.Track(req.GoalName, properties)
}

func override(cd *ContextData, decode func(interface{}) error) (interface{}, error) {
	var req variantRequest
	if err := decode(&req); err != nil {
		return nil, err
	}
	return nil, cd.context.SetOverride(req.ExperimentName, int(req.Variant))
}

func customAssignment(cd *ContextData, decode func(interface{}) error) (interface{}, error) {
	var req variantRequest
	if err := decode(&req); err != nil {
		return nil, err
	}
	return nil, cd.context.SetCustomAssignment(req.ExperimentName, int(req.Variant))
}

func customFieldValue(cd *ContextData, decode func(interface{}) error) (interface{}, error) {
	var req fieldRequest
	if err := decode(&req); err != nil {
		return nil, err
	}
	return cd.context.GetCustomFieldValueE(req.ExperimentName, req.FieldName)
}

func customFieldValueType(cd *ContextData, decode func(interface{}) error) (interface{}, error) {
	var req fieldRequest
	if err := decode(&req); err != nil {
		return nil, err
	}
	return cd.context.GetCustomFieldValueType(req.ExperimentName, req.FieldName), nil
}

func variableKeys(cd *ContextData, _ func(interface{}) error) (interface{}, error) {
	keys, err := cd.context.GetVariableKeys()
	if err != nil {
		return nil, err
	}
	result := make([]string, 0, len(keys))
	for key := range keys {
		result = append(result, key)
	}
	return result, nil
}

func variableKeysMap(cd *ContextData, _ func(interface{}) error) (interface{}, error) {
	keys, err := cd.context.GetVariableKeys()
	if err != nil {
		return nil, err
	}
	return keys, nil
}

func customFieldKeys(cd *ContextData, _ func(interface{}) error) (interface{}, error) {
	keys, err := cd.context.GetCustomFieldValueKeys()
	if err != nil {
		return nil, err
	}
	return keys, nil
}

func experiments(cd *ContextData, _ func(interface{}) error) (interface{}, error) {
	names, err := cd.context.GetExperiments()
	if err != nil {
		return nil, err
	}
	return names, nil
}

func pending(cd *ContextData, _ func(interface{}) error) (interface{}, error) {
	return cd.context.GetPendingCount(), nil
}

func isFinalized(cd *ContextData, _ func(interface{}) error) (interface{}, error) {
	return cd.context.IsClosed(), nil
}

func isReady(cd *ContextData, _ func(interface{}) error) (interface{}, error) {
	return cd.context.IsReady(), nil
}

func isFailed(cd *ContextData, _ func(interface{}) error) (interface{}, error) {
	return cd.context.IsFailed(), nil
}

func getUnits(cd *ContextData, _ func(interface{}) error) (interface{}, error) {
	return cd.context.GetUnits(), nil
}

func getAttributes(cd *ContextData, _ func(interface{}) error) (interface{}, error) {
	return cd.context.GetAttributes(), nil
}

func publishFail(cd *ContextData, _ func(interface{}) error) (interface{}, error) {
	cd.publisher.failNext.Store(true)
	return nil, nil
}

func publish(cd *ContextData, _ func(interface{}) error) (interface{}, error) {
	return nil, cd.context.Publish()
}

func refresh(cd *ContextData, _ func(interface{}) error) (interface{}, error) {
	_, err := cd.context.RefreshAsync().Get(gocontext.Background())
	return nil, err
}

func finalize(cd *ContextData, _ func(interface{}) error) (interface{}, error) {
	f, err := cd.context.FinalizeAsync()
	if err != nil {
		return nil, err
	}
	_, err = f.Get(gocontext.Background())
	return nil, err
}

func readyErrorHandler(w http.ResponseWriter, r *http.Request) {
	// The Go SDK exposes IsFailed() but no ReadyError()/error accessor, so the
	// wrapper has nothing real to return. Capability "readyError" is false.
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusNotImplemented)
	json.NewEncoder(w).Encode(map[string]string{"error": "readyError not supported by Go SDK"})
}

func deleteContextHandler(w http.ResponseWriter, r *http.Request) {
	contextID := mux.Vars(r)["contextId"]
	contextsMu.Lock()
	delete(contexts, contextID)
	contextsMu.Unlock()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"result": "deleted"})
}

func main() {
	router := mux.NewRouter()

	router.HandleFunc("/health", healthHandler).Methods("GET")
	router.HandleFunc("/capabilities", capabilitiesHandler).Methods("GET")
	router.HandleFunc("/diagnostic", diagnosticHandler).Methods("POST")
	router.HandleFunc("/context_payload/{payloadId}", storePayloadHandler).Methods("PUT")
	router.HandleFunc("/context_payload/{payloadId}", getPayloadHandler).Methods("GET")
	router.HandleFunc("/context_payload/{payloadId}/context", mockApiContextHandler).Methods("GET")
	router.HandleFunc("/context", createContextHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/setUnit", contextRoute(setUnit)).Methods("POST")
	router.HandleFunc("/context/{contextId}/getUnit", contextRoute(getUnit)).Methods("POST")
	router.HandleFunc("/context/{contextId}/attribute", contextRoute(setAttribute)).Methods("POST")
	router.HandleFunc("/context/{contextId}/getAttribute", contextRoute(getAttribute)).Methods("POST")
	router.HandleFunc("/context/{contextId}/treatment", contextRoute(treatment)).Methods("POST")
	router.HandleFunc("/context/{contextId}/peek", contextRoute(peek)).Methods("POST")
	router.HandleFunc("/context/{contextId}/variableValue", contextRoute(variableValue)).Methods("POST")
	router.HandleFunc("/context/{contextId}/peekVariableValue", contextRoute(peekVariableValue)).Methods("POST")
	router.HandleFunc("/context/{contextId}/track", contextRoute(track)).Methods("POST")
	router.HandleFunc("/context/{contextId}/override", contextRoute(override)).Methods("POST")
	router.HandleFunc("/context/{contextId}/customAssignment", contextRoute(customAssignment)).Methods("POST")
	router.HandleFunc("/context/{contextId}/customFieldValue", contextRoute(customFieldValue)).Methods("POST")
	router.HandleFunc("/context/{contextId}/variableKeys", contextRoute(variableKeys)).Methods("POST")
	router.HandleFunc("/context/{contextId}/customFieldKeys", contextRoute(customFieldKeys)).Methods("POST")
	router.HandleFunc("/context/{contextId}/customFieldValueType", contextRoute(customFieldValueType)).Methods("POST")
	router.HandleFunc("/context/{contextId}/setOverride", contextRoute(override)).Methods("POST")
	router.HandleFunc("/context/{contextId}/setCustomAssignment", contextRoute(customAssignment)).Methods("POST")
	router.HandleFunc("/context/{contextId}/pending", contextRoute(pending)).Methods("GET")
	router.HandleFunc("/context/{contextId}/isFinalized", contextRoute(isFinalized)).Methods("GET")
	router.HandleFunc("/context/{contextId}/isReady", contextRoute(isReady)).Methods("GET")
	router.HandleFunc("/context/{contextId}/isFailed", contextRoute(isFailed)).Methods("GET")
	router.HandleFunc("/context/{contextId}/experiments", contextRoute(experiments)).Methods("GET")
	router.HandleFunc("/context/{contextId}/getUnits", contextRoute(getUnits)).Methods("POST")
	router.HandleFunc("/context/{contextId}/getAttributes", contextRoute(getAttributes)).Methods("POST")
	router.HandleFunc("/context/{contextId}/readyError", readyErrorHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/variableKeysMap", contextRoute(variableKeysMap)).Methods("POST")
	router.HandleFunc("/context/{contextId}/globalCustomFieldKeys", contextRoute(customFieldKeys)).Methods("POST")
	router.HandleFunc("/context/{contextId}/publishFail", contextRoute(publishFail)).Methods("POST")
	router.HandleFunc("/context/{contextId}/publish", contextRoute(publish)).Methods("POST")
	router.HandleFunc("/context/{contextId}/refresh", contextRoute(refresh)).Methods("POST")
	router.HandleFunc("/context/{contextId}/finalize", contextRoute(finalize)).Methods("POST")
	router.HandleFunc("/context/{contextId}", deleteContextHandler).Methods("DELETE")

	port := os.Getenv("PORT")
	if port == "" {
		port = "3000"
	}

	log.Printf("Go SDK wrapper listening on port %s", port)
	log.Fatal(http.ListenAndServe(fmt.Sprintf("0.0.0.0:%s", port), router))
}
