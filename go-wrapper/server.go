package main

import (
	"crypto/md5"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
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
	case sdk.Close:
		eventTypeLower = "finalize"
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
	dest = normalizeEmptyStrings(dest)
	return dest
}

func normalizeEmptyStrings(v interface{}) interface{} {
	switch val := v.(type) {
	case string:
		if val == "" {
			return nil
		}
		return val
	case map[string]interface{}:
		result := make(map[string]interface{})
		for k, v := range val {
			result[k] = normalizeEmptyStrings(v)
		}
		return result
	case []interface{}:
		result := make([]interface{}, len(val))
		for i, v := range val {
			result[i] = normalizeEmptyStrings(v)
		}
		return result
	default:
		return val
	}
}

type deferredContextDataProvider struct {
	dataFuture *future.Future
}

func (d *deferredContextDataProvider) GetContextData() *future.Future {
	return d.dataFuture
}

type CustomPublisher struct {
	eventCollector *EventCollector
}

func (cp *CustomPublisher) Publish(context sdk.Context, event jsonmodels.PublishEvent) *future.Future {
	result, done := future.New()
	done(nil, nil)
	return result
}

type CustomVariableParser struct{}

func (cvp *CustomVariableParser) Parse(context sdk.Context, experimentName string, variantName string, config string) map[string]interface{} {
	var data map[string]interface{}
	if err := json.Unmarshal([]byte(config), &data); err != nil {
		// If not a JSON object, parse as any JSON type and wrap it
		var anyData interface{}
		if err2 := json.Unmarshal([]byte(config), &anyData); err2 == nil {
			return map[string]interface{}{"__raw_value": anyData}
		}
		return nil
	}
	return data
}

type ContextData struct {
	context        *sdk.Context
	eventCollector *EventCollector
	publishFail    bool
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
	json.NewEncoder(w).Encode(map[string]bool{"diagnostics":  true,
		"attrsSeq":     false,
		"publishFail":  true,
		"variableKeysMap": true,
		"globalCustomFieldKeys": true,
		"getUnits":     true,
		"getAttributes": true,
		"readyError":   true,
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
		sum := md5.Sum([]byte(fmt.Sprint(req.Value)))
		result = base64.RawURLEncoding.EncodeToString(sum[:])
	case "base64UrlNoPadding":
		result = base64.RawURLEncoding.EncodeToString([]byte(fmt.Sprint(req.Value)))
	case "utf8Bytes":
		data := []byte(fmt.Sprint(req.Value))
		out := make([]int, len(data))
		for i, b := range data {
			out[i] = int(b)
		}
		result = out
	case "isObject":
		_, ok := req.Value.(map[string]interface{})
		result = ok
	case "isNumeric":
		switch req.Value.(type) {
		case float64, float32, int, int64, int32, int16, int8, uint, uint64, uint32, uint16, uint8:
			result = true
		default:
			result = false
		}
	case "isPromise":
		result = false
	default:
		http.Error(w, fmt.Sprintf("Unsupported diagnostic operation: %s", req.Operation), http.StatusBadRequest)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"result": result,
		"events": []interface{}{},
	})
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

	if req.Mode == "e2e" {
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

		e2eEventCollector := &EventCollector{events: []Event{}}
		e2eVariableParser := &CustomVariableParser{}

		e2eClientConfig := sdk.ClientConfig{
			Endpoint_:    e2eEndpoint,
			ApiKey_:      e2eApiKey,
			Application_: e2eApplication,
			Environment_: e2eEnvironment,
		}
		e2eClient := sdk.CreateDefaultClient(e2eClientConfig)
		e2eAbsmartly := sdk.Create(sdk.ABSmartlyConfig{
			Client_:              e2eClient,
			ContextEventLogger_: e2eEventCollector,
			VariableParser_:     e2eVariableParser,
		})

		e2eUnits := make(map[string]string)
		for k, v := range req.Units {
			switch val := v.(type) {
			case string:
				e2eUnits[k] = val
			case float64:
				if val == float64(int64(val)) {
					e2eUnits[k] = fmt.Sprintf("%.0f", val)
				} else {
					e2eUnits[k] = fmt.Sprintf("%g", val)
				}
			default:
				e2eUnits[k] = fmt.Sprintf("%v", val)
			}
		}

		e2eContextConfig := sdk.ContextConfig{
			Units_:          e2eUnits,
			Attributes_:     make(map[string]interface{}),
			Overrides_:      make(map[string]int),
			Cassigmnents_:   make(map[string]int),
			EventLogger_:    e2eEventCollector,
			PublishDelay_:   int64(-1),
			RefreshInterval_: int64(-1),
		}

		e2eContext := e2eAbsmartly.CreateContext(e2eContextConfig)

		for k, v := range req.Attributes {
			_ = e2eContext.SetAttribute(k, v)
		}

		e2eContext.WaitUntilReady()

		e2eContextID := fmt.Sprintf("ctx-%d-%f", time.Now().UnixNano(), float64(time.Now().UnixNano()%1000000))
		e2eCtxData := &ContextData{
			context:        e2eContext,
			eventCollector: e2eEventCollector,
		}

		contextsMu.Lock()
		contexts[e2eContextID] = e2eCtxData
		contextsMu.Unlock()

		e2eResponse := Response{
			Result: CreateContextResponse{
				ContextID: e2eContextID,
				Ready:     e2eContext.IsReady(),
				Failed:    e2eContext.IsFailed(),
				Finalized: e2eContext.IsClosed(),
			},
			Events: e2eEventCollector.SliceFrom(0),
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(e2eResponse)
		return
	}

	contextID := fmt.Sprintf("ctx-%d-%f", time.Now().UnixNano(), float64(time.Now().UnixNano()%1000000))

	eventCollector := &EventCollector{events: []Event{}}
	customPublisher := &CustomPublisher{eventCollector: eventCollector}
	customVariableParser := &CustomVariableParser{}

	config := sdk.ABSmartlyConfig{
		Client_:               nil,
		ContextPublisher_:  customPublisher,
		ContextEventLogger_:  eventCollector,
		ContextDataProvider_:  nil,
		VariableParser_:      customVariableParser,
		AudienceDeserializer_:nil,
	}

	absmartly := sdk.Create(config)

	units := make(map[string]string)
	for k, v := range req.Units {
		switch val := v.(type) {
		case string:
			units[k] = val
		case float64:
			// Check if it's a whole number
			if val == float64(int64(val)) {
				units[k] = fmt.Sprintf("%.0f", val)
			} else {
				units[k] = fmt.Sprintf("%g", val)
			}
		case int:
			units[k] = fmt.Sprintf("%d", val)
		default:
			units[k] = fmt.Sprintf("%v", val)
		}
	}

	publishDelay := int64(-1)
	refreshInterval := int64(-1)

	if req.Options != nil {
		if pd, ok := req.Options["publishDelay"].(float64); ok {
			publishDelay = int64(pd)
		}
		if ri, ok := req.Options["refreshPeriod"].(float64); ok {
			if ri > 0 {
				refreshInterval = int64(ri)
			} else {
				refreshInterval = -1
			}
		}
	}

	contextConfig := sdk.ContextConfig{
		Units_:            units,
		Attributes_:       make(map[string]interface{}),
		Overrides_:        make(map[string]int),
		Cassigmnents_:    make(map[string]int),
		EventLogger_:      eventCollector,
		PublishDelay_:      publishDelay,
		RefreshInterval_:   refreshInterval,
	}

	var context *sdk.Context
	if req.Endpoint != "" {
		endpoint := translateEndpoint(req.Endpoint)

		payloadThrottle := 0
		if req.Options != nil {
			if pt, ok := req.Options["payloadThrottle"].(float64); ok {
				payloadThrottle = int(pt)
			}
		}

		if payloadThrottle > 0 {
			dataFuture, done := future.New()
			go func() {
				time.Sleep(time.Duration(payloadThrottle) * time.Millisecond)
				resp, err := http.Get(endpoint)
				if err != nil {
					done(jsonmodels.ContextData{Experiments: []jsonmodels.Experiment{}}, nil)
					return
				}
				defer resp.Body.Close()
				var data jsonmodels.ContextData
				if err := json.NewDecoder(resp.Body).Decode(&data); err != nil {
					done(jsonmodels.ContextData{Experiments: []jsonmodels.Experiment{}}, nil)
					return
				}
				done(data, nil)
			}()

			deferredProvider := &deferredContextDataProvider{dataFuture: dataFuture}
			absmartlyDeferred := sdk.Create(sdk.ABSmartlyConfig{
				ContextDataProvider_:  deferredProvider,
				ContextPublisher_:  customPublisher,
				ContextEventLogger_:  eventCollector,
				VariableParser_:      customVariableParser,
				AudienceDeserializer_:nil,
			})
			context = absmartlyDeferred.CreateContext(contextConfig)
		} else {
			clientConfig := sdk.ClientConfig{
				Endpoint_:    endpoint,
				ApiKey_:      "test-api-key",
				Application_: "test-app",
				Environment_: "test-env",
			}
			client := sdk.CreateDefaultClient(clientConfig)
			absmartlyWithClient := sdk.Create(sdk.ABSmartlyConfig{
				Client_:               client,
				ContextPublisher_:  customPublisher,
				ContextEventLogger_:  eventCollector,
				VariableParser_:      customVariableParser,
				AudienceDeserializer_:nil,
			})
			context = absmartlyWithClient.CreateContext(contextConfig)
			context.WaitUntilReady()
			for i := 0; i < 50 && eventCollector.Len() == 0; i++ {
				time.Sleep(10 * time.Millisecond)
			}
		}
	} else if req.FailLoad {
		dataFuture, done := future.New()
		done(jsonmodels.ContextData{}, fmt.Errorf("Context load failed"))
		failingProvider := &deferredContextDataProvider{dataFuture: dataFuture}
		absmartlyFailing := sdk.Create(sdk.ABSmartlyConfig{
			ContextDataProvider_:  failingProvider,
			ContextPublisher_:  customPublisher,
			ContextEventLogger_:  eventCollector,
			VariableParser_:      customVariableParser,
			AudienceDeserializer_:nil,
		})
		context = absmartlyFailing.CreateContext(contextConfig)
		for i := 0; i < 50 && eventCollector.Len() == 0; i++ {
			time.Sleep(10 * time.Millisecond)
		}
	} else {
		contextConfig.RefreshInterval_ = -1
		context = absmartly.CreateContextWith(contextConfig, req.Data)
	}

	contextData := &ContextData{
		context:        context,
		eventCollector: eventCollector,
	}

	contextsMu.Lock()
	contexts[contextID] = contextData
	contextsMu.Unlock()

	response := Response{
		Result: CreateContextResponse{
			ContextID: contextID,
			Ready:     context.IsReady(),
			Failed:    context.IsFailed(),
			Finalized: context.IsClosed(),
		},
		Events: eventCollector.SliceFrom(0),
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
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

func setUnitHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	var req struct {
		UnitType string      `json:"unitType"`
		UID      interface{} `json:"uid"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	uidStr := ""
	switch v := req.UID.(type) {
	case string:
		uidStr = v
	case float64:
		// Check if it's a whole number
		if v == float64(int64(v)) {
			uidStr = fmt.Sprintf("%.0f", v)
		} else {
			uidStr = fmt.Sprintf("%g", v)
		}
	case int:
		uidStr = fmt.Sprintf("%d", v)
	default:
		uidStr = fmt.Sprintf("%v", v)
	}

	err = ctxData.context.SetUnit(req.UnitType, uidStr)
	if err != nil {
		errMsg := err.Error()
		if strings.Contains(errMsg, "closed") || strings.Contains(errMsg, "finalized") {
			errMsg = "Context finalized"
		}
		http.Error(w, errMsg, http.StatusBadRequest)
		return
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	response := Response{
		Result: nil,
		Events: newEvents,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func getUnitHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	var req struct {
		UnitType string `json:"unitType"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	unit := ctxData.context.Units_[req.UnitType]

	if unit == "" {
		newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)
		response := Response{
			Result: nil,
			Events: newEvents,
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(response)
		return
	}

	var result interface{} = unit
	if num, err := strconv.ParseFloat(unit, 64); err == nil {
		if num == float64(int64(num)) {
			result = int64(num)
		} else {
			result = num
		}
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	response := Response{
		Result: result,
		Events: newEvents,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func setAttributeHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	var req struct {
		Name  string      `json:"name"`
		Value interface{} `json:"value"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	err = ctxData.context.SetAttribute(req.Name, req.Value)
	if err != nil {
		errMsg := err.Error()
		if strings.Contains(errMsg, "closed") || strings.Contains(errMsg, "finalized") {
			errMsg = "Context finalized"
		}
		http.Error(w, errMsg, http.StatusBadRequest)
		return
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	response := Response{
		Result: nil,
		Events: newEvents,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func getAttributeHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	var req struct {
		Name string `json:"name"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	var result interface{}
	for _, attr := range ctxData.context.Attributes_ {
		if a, ok := attr.(jsonmodels.Attribute); ok && a.Name == req.Name {
			result = a.Value
		}
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	response := Response{
		Result: result,
		Events: newEvents,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func treatmentHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	var req struct {
		ExperimentName string `json:"experimentName"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	variant, err := ctxData.context.GetTreatment(req.ExperimentName)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	response := Response{
		Result: variant,
		Events: newEvents,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func peekHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	var req struct {
		ExperimentName string `json:"experimentName"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	variant, err := ctxData.context.PeekTreatment(req.ExperimentName)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	response := Response{
		Result: variant,
		Events: newEvents,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func variableValueHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	var req struct {
		Key          string      `json:"key"`
		DefaultValue interface{} `json:"defaultValue"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	value := req.DefaultValue
	var varErr error

	func() {
		defer func() {
			if r := recover(); r != nil {
				log.Printf("Recovered from panic in variableValue: %v", r)
				value = req.DefaultValue
			}
		}()
		value, varErr = ctxData.context.GetVariableValue(req.Key, req.DefaultValue)
	}()

	if varErr != nil {
		log.Printf("Error in GetVariableValue: %v", varErr)
		value = req.DefaultValue
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	response := Response{
		Result: value,
		Events: newEvents,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func peekVariableValueHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	var req struct {
		Key          string      `json:"key"`
		DefaultValue interface{} `json:"defaultValue"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	value := req.DefaultValue
	var peekErr error

	func() {
		defer func() {
			if r := recover(); r != nil {
				log.Printf("Recovered from panic in peekVariableValue: %v", r)
				value = req.DefaultValue
			}
		}()
		value, peekErr = ctxData.context.PeekVariableValue(req.Key, req.DefaultValue)
	}()

	if peekErr != nil {
		log.Printf("Error in PeekVariableValue: %v", peekErr)
		value = req.DefaultValue
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	response := Response{
		Result: value,
		Events: newEvents,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func trackHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	// First decode into generic map to validate properties type
	var rawReq map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&rawReq); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	goalName, _ := rawReq["goalName"].(string)
	var properties map[string]interface{}

	if propsVal, exists := rawReq["properties"]; exists && propsVal != nil {
		if propsMap, ok := propsVal.(map[string]interface{}); ok {
			properties = propsMap
		} else {
			http.Error(w, fmt.Sprintf("Goal '%s' properties must be of type object.", goalName), http.StatusBadRequest)
			return
		}
	}

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	err = ctxData.context.Track(goalName, properties)
	if err != nil {
		errMsg := err.Error()
		if strings.Contains(errMsg, "closed") || strings.Contains(errMsg, "finalized") {
			errMsg = "Context finalized"
		}
		http.Error(w, errMsg, http.StatusBadRequest)
		return
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	response := Response{
		Result: nil,
		Events: newEvents,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func overrideHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	var req struct {
		ExperimentName string  `json:"experimentName"`
		Variant        float64 `json:"variant"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	err = ctxData.context.SetOverride(req.ExperimentName, int(req.Variant))
	if err != nil {
		errMsg := strings.ToLower(err.Error())
		if strings.Contains(errMsg, "closed") || strings.Contains(errMsg, "closing") || strings.Contains(errMsg, "finalized") {
			response := Response{
				Result: nil,
				Events: []Event{},
			}
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(response)
			return
		}
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	response := Response{
		Result: nil,
		Events: []Event{},
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func customAssignmentHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	var req struct {
		ExperimentName string  `json:"experimentName"`
		Variant        float64 `json:"variant"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	err = ctxData.context.SetCustomAssignment(req.ExperimentName, int(req.Variant))
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	response := Response{
		Result: nil,
		Events: []Event{},
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func customFieldValueHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	var req struct {
		ExperimentName string `json:"experimentName"`
		FieldName      string `json:"fieldName"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	value := ctxData.context.GetCustomFieldValue(req.ExperimentName, req.FieldName)
	log.Printf("GetCustomFieldValue(%s, %s) returned: %v (type: %T)", req.ExperimentName, req.FieldName, value, value)

	if mapValue, ok := value.(map[string]interface{}); ok {
		if rawValue, exists := mapValue["__raw_value"]; exists {
			value = rawValue
			log.Printf("Unwrapped __raw_value: %v (type: %T)", value, value)
		}
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	response := Response{
		Result: value,
		Events: newEvents,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func variableKeysHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	keys, err := ctxData.context.GetVariableKeys()
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	result := make([]string, 0, len(keys))
	for key := range keys {
		result = append(result, key)
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	response := Response{
		Result: result,
		Events: newEvents,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func customFieldKeysHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	var req struct {
		ExperimentName string `json:"experimentName"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	keys, err := ctxData.context.GetCustomFieldValueKeys()
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	response := Response{
		Result: keys,
		Events: newEvents,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func customFieldValueTypeHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	var req struct {
		ExperimentName string `json:"experimentName"`
		FieldName      string `json:"fieldName"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	valueType := ctxData.context.GetCustomFieldValueType(req.ExperimentName, req.FieldName)

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	response := Response{
		Result: valueType,
		Events: newEvents,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func setOverrideHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	var req struct {
		ExperimentName string  `json:"experimentName"`
		Variant        float64 `json:"variant"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	err = ctxData.context.SetOverride(req.ExperimentName, int(req.Variant))
	if err != nil {
		errMsg := strings.ToLower(err.Error())
		if strings.Contains(errMsg, "closed") || strings.Contains(errMsg, "closing") || strings.Contains(errMsg, "finalized") {
			response := Response{
				Result: nil,
				Events: []Event{},
			}
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(response)
			return
		}
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	response := Response{
		Result: nil,
		Events: newEvents,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func setCustomAssignmentHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	var req struct {
		ExperimentName string  `json:"experimentName"`
		Variant        float64 `json:"variant"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	err = ctxData.context.SetCustomAssignment(req.ExperimentName, int(req.Variant))
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	response := Response{
		Result: nil,
		Events: newEvents,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func pendingHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	response := Response{
		Result: ctxData.context.GetPendingCount(),
		Events: []Event{},
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func isFinalizedHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	response := Response{
		Result: ctxData.context.IsClosed(),
		Events: []Event{},
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func isReadyHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	response := Response{
		Result: ctxData.context.IsReady(),
		Events: []Event{},
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func isFailedHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	response := Response{
		Result: ctxData.context.IsFailed(),
		Events: []Event{},
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func experimentsHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	experiments, err := ctxData.context.GetExperiments()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	response := Response{
		Result: experiments,
		Events: []Event{},
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func getUnitsHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	units := ctxData.context.Units_
	result := make(map[string]interface{})
	for k, v := range units {
		if num, err := strconv.ParseFloat(v, 64); err == nil {
			if num == float64(int64(num)) {
				result[k] = int64(num)
			} else {
				result[k] = num
			}
		} else {
			result[k] = v
		}
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(Response{Result: result, Events: newEvents})
}

func getAttributesHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	attrsMap := make(map[string]interface{})
	for _, attr := range ctxData.context.Attributes_ {
		if a, ok := attr.(jsonmodels.Attribute); ok {
			attrsMap[a.Name] = a.Value
		}
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(Response{Result: attrsMap, Events: newEvents})
}

func readyErrorHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	var result interface{}
	if ctxData.context.IsFailed() {
		result = "Context load failed"
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(Response{Result: result, Events: []Event{}})
}

func variableKeysMapHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	keys, err := ctxData.context.GetVariableKeys()
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(Response{Result: keys, Events: newEvents})
}

func globalCustomFieldKeysHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	keys, err := ctxData.context.GetCustomFieldValueKeys()
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(Response{Result: keys, Events: newEvents})
}

func publishFailHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	ctxData.publishFail = true

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(Response{Result: nil, Events: []Event{}})
}

func publishHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	if ctxData.publishFail {
		ctxData.publishFail = false
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": "publish failed", "code": "PUBLISH_ERROR"})
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	err = ctxData.context.Publish()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	response := Response{
		Result: nil,
		Events: newEvents,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func refreshHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	ctxData.context.Refresh()

	for i := 0; i < 50 && ctxData.eventCollector.Len() == eventsBefore; i++ {
		time.Sleep(10 * time.Millisecond)
	}

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	response := Response{
		Result: nil,
		Events: newEvents,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func finalizeHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := ctxData.eventCollector.Len()

	ctxData.context.Close()

	newEvents := ctxData.eventCollector.SliceFrom(eventsBefore)

	response := Response{
		Result: nil,
		Events: newEvents,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func deleteContextHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	contextsMu.Lock()
	delete(contexts, contextID)
	contextsMu.Unlock()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"result": "deleted",
	})
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
	router.HandleFunc("/context/{contextId}/setUnit", setUnitHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/getUnit", getUnitHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/attribute", setAttributeHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/getAttribute", getAttributeHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/treatment", treatmentHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/peek", peekHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/variableValue", variableValueHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/peekVariableValue", peekVariableValueHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/track", trackHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/override", overrideHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/customAssignment", customAssignmentHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/customFieldValue", customFieldValueHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/variableKeys", variableKeysHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/customFieldKeys", customFieldKeysHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/customFieldValueType", customFieldValueTypeHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/setOverride", setOverrideHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/setCustomAssignment", setCustomAssignmentHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/pending", pendingHandler).Methods("GET")
	router.HandleFunc("/context/{contextId}/isFinalized", isFinalizedHandler).Methods("GET")
	router.HandleFunc("/context/{contextId}/isReady", isReadyHandler).Methods("GET")
	router.HandleFunc("/context/{contextId}/isFailed", isFailedHandler).Methods("GET")
	router.HandleFunc("/context/{contextId}/experiments", experimentsHandler).Methods("GET")
	router.HandleFunc("/context/{contextId}/getUnits", getUnitsHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/getAttributes", getAttributesHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/readyError", readyErrorHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/variableKeysMap", variableKeysMapHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/globalCustomFieldKeys", globalCustomFieldKeysHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/publishFail", publishFailHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/publish", publishHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/refresh", refreshHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}/finalize", finalizeHandler).Methods("POST")
	router.HandleFunc("/context/{contextId}", deleteContextHandler).Methods("DELETE")

	port := os.Getenv("PORT")
	if port == "" {
		port = "3000"
	}

	log.Printf("Go SDK wrapper listening on port %s", port)
	log.Fatal(http.ListenAndServe(fmt.Sprintf("0.0.0.0:%s", port), router))
}
