package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"sync"
	"time"

	"github.com/absmartly/go-sdk/sdk"
	"github.com/absmartly/go-sdk/sdk/future"
	"github.com/absmartly/go-sdk/sdk/jsonmodels"
	"github.com/gorilla/mux"
)

type EventCollector struct {
	events []Event
	mu     sync.Mutex
}

type Event struct {
	Type      string      `json:"type"`
	Data      interface{} `json:"data"`
	Timestamp int64       `json:"timestamp"`
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
	Data     jsonmodels.ContextData `json:"data"`
	Endpoint string                 `json:"endpoint"`
	Units    map[string]interface{} `json:"units"`
	Options  map[string]interface{} `json:"options"`
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
	json.NewEncoder(w).Encode(map[string]bool{
		"asyncContext": false,
		"attrsSeq":     false,
	})
}

func storePayloadHandler(w http.ResponseWriter, r *http.Request) {
	var req StorePayloadRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	payloadID := fmt.Sprintf("payload-%d-%f", time.Now().UnixNano(), float64(time.Now().UnixNano()%1000000))

	payloadMu.Lock()
	payloadStore[payloadID] = req.Data
	payloadMu.Unlock()

	url := fmt.Sprintf("http://go-sdk:3000/context_payload/%s", payloadID)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(StorePayloadResponse{
		PayloadURL: url,
		PayloadID:  payloadID,
	})
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

func createContextHandler(w http.ResponseWriter, r *http.Request) {
	var req CreateContextRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	contextID := fmt.Sprintf("ctx-%d-%f", time.Now().UnixNano(), float64(time.Now().UnixNano()%1000000))

	eventCollector := &EventCollector{events: []Event{}}
	customPublisher := &CustomPublisher{eventCollector: eventCollector}
	customVariableParser := &CustomVariableParser{}

	config := sdk.ABSmartlyConfig{
		Client_:               nil,
		ContextEventHandler_:  customPublisher,
		ContextEventLogger_:   eventCollector,
		ContextDataProvider_:  nil,
		VariableParser_:       customVariableParser,
		AudienceDeserializer_: nil,
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
	refreshInterval := int64(0)

	if req.Options != nil {
		if pd, ok := req.Options["publishDelay"].(float64); ok {
			publishDelay = int64(pd)
		}
		if ri, ok := req.Options["refreshPeriod"].(float64); ok {
			refreshInterval = int64(ri)
		}
	}

	contextConfig := sdk.ContextConfig{
		Units_:           units,
		Attributes_:      make(map[string]interface{}),
		Overrides_:       make(map[string]int),
		Cassigmnents_:    make(map[string]int),
		EventLogger_:     eventCollector,
		PublishDelay_:    publishDelay,
		RefreshInterval_: refreshInterval,
	}

	// Go SDK only supports synchronous context creation
	context := absmartly.CreateContextWith(contextConfig, req.Data)

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
		Events: eventCollector.events,
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

	eventsBefore := len(ctxData.eventCollector.events)

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

	// Check if unit already set with different value
	ctxData.context.ContextLock_.RLock()
	existingUnit, exists := ctxData.context.Units_[req.UnitType]
	ctxData.context.ContextLock_.RUnlock()

	if exists && existingUnit != uidStr {
		http.Error(w, fmt.Sprintf("Unit '%s' UID already set.", req.UnitType), http.StatusBadRequest)
		return
	}

	err = ctxData.context.SetUnit(req.UnitType, uidStr)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	newEvents := ctxData.eventCollector.events[eventsBefore:]

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

	eventsBefore := len(ctxData.eventCollector.events)

	ctxData.context.ContextLock_.RLock()
	unit, exists := ctxData.context.Units_[req.UnitType]
	ctxData.context.ContextLock_.RUnlock()

	if !exists {
		http.Error(w, "unit not found", http.StatusBadRequest)
		return
	}

	var result interface{} = unit
	var num float64
	if _, err := fmt.Sscanf(unit, "%f", &num); err == nil {
		if num == float64(int64(num)) {
			result = int64(num)
		} else {
			result = num
		}
	}

	newEvents := ctxData.eventCollector.events[eventsBefore:]

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

	eventsBefore := len(ctxData.eventCollector.events)

	err = ctxData.context.SetAttribute(req.Name, req.Value)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	newEvents := ctxData.eventCollector.events[eventsBefore:]

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

	eventsBefore := len(ctxData.eventCollector.events)

	var result interface{} = nil
	var latestSetAt int64 = 0
	ctxData.context.ContextLock_.RLock()
	for _, attr := range ctxData.context.Attributes_ {
		if attrMap, ok := attr.(jsonmodels.Attribute); ok {
			if attrMap.Name == req.Name {
				if attrMap.SetAt >= latestSetAt {
					result = attrMap.Value
					latestSetAt = attrMap.SetAt
				}
			}
		}
	}
	ctxData.context.ContextLock_.RUnlock()

	newEvents := ctxData.eventCollector.events[eventsBefore:]

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

	eventsBefore := len(ctxData.eventCollector.events)

	variant := 0
	var treatmentErr error
	var assignment *sdk.Assignment

	func() {
		defer func() {
			if r := recover(); r != nil {
				log.Printf("Recovered from panic in treatment: %v", r)
				if assignment != nil && assignment.Variant < 0 {
					variant = assignment.Variant
				} else {
					variant = 0
				}
			}
		}()

		err := ctxData.context.CheckReady(true)
		if err != nil {
			treatmentErr = err
			variant = -1
			return
		}

		assignment = ctxData.context.GetAssignment(req.ExperimentName)
		variant = assignment.Variant

		if !assignment.Exposed.Load().(bool) {
			ctxData.context.QueueExposure(assignment)
		}
	}()

	if treatmentErr != nil {
		log.Printf("Error in treatment: %v", treatmentErr)
		variant = -1
	}

	newEvents := ctxData.eventCollector.events[eventsBefore:]

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

	eventsBefore := len(ctxData.eventCollector.events)

	variant, err := ctxData.context.PeekTreatment(req.ExperimentName)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	newEvents := ctxData.eventCollector.events[eventsBefore:]

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

	eventsBefore := len(ctxData.eventCollector.events)

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

	newEvents := ctxData.eventCollector.events[eventsBefore:]

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

	eventsBefore := len(ctxData.eventCollector.events)

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

	newEvents := ctxData.eventCollector.events[eventsBefore:]

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

	eventsBefore := len(ctxData.eventCollector.events)

	err = ctxData.context.Track(goalName, properties)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	newEvents := ctxData.eventCollector.events[eventsBefore:]

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

	eventsBefore := len(ctxData.eventCollector.events)

	value := ctxData.context.GetCustomFieldValue(req.ExperimentName, req.FieldName)
	log.Printf("GetCustomFieldValue(%s, %s) returned: %v (type: %T)", req.ExperimentName, req.FieldName, value, value)

	if mapValue, ok := value.(map[string]interface{}); ok {
		if rawValue, exists := mapValue["__raw_value"]; exists {
			value = rawValue
			log.Printf("Unwrapped __raw_value: %v (type: %T)", value, value)
		}
	}

	newEvents := ctxData.eventCollector.events[eventsBefore:]

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

	eventsBefore := len(ctxData.eventCollector.events)

	keys, err := ctxData.context.GetVariableKeys()
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	result := make([]string, 0, len(keys))
	for key := range keys {
		result = append(result, key)
	}

	newEvents := ctxData.eventCollector.events[eventsBefore:]

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

	eventsBefore := len(ctxData.eventCollector.events)

	keys, err := ctxData.context.GetCustomFieldValueKeys()
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	newEvents := ctxData.eventCollector.events[eventsBefore:]

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

	eventsBefore := len(ctxData.eventCollector.events)

	valueType := ctxData.context.GetCustomFieldValueType(req.ExperimentName, req.FieldName)

	newEvents := ctxData.eventCollector.events[eventsBefore:]

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

	eventsBefore := len(ctxData.eventCollector.events)

	err = ctxData.context.SetOverride(req.ExperimentName, int(req.Variant))
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	newEvents := ctxData.eventCollector.events[eventsBefore:]

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

	eventsBefore := len(ctxData.eventCollector.events)

	err = ctxData.context.SetCustomAssignment(req.ExperimentName, int(req.Variant))
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	newEvents := ctxData.eventCollector.events[eventsBefore:]

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

func publishHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	contextID := vars["contextId"]

	ctxData, err := getContext(contextID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	eventsBefore := len(ctxData.eventCollector.events)

	err = ctxData.context.Publish()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	newEvents := ctxData.eventCollector.events[eventsBefore:]

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

	var req struct {
		NewData jsonmodels.ContextData `json:"newData"`
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

	eventsBefore := len(ctxData.eventCollector.events)

	// Clear assignment cache before refresh (like JavaScript SDK does)
	ctxData.context.ContextLock_.Lock()
	for k := range ctxData.context.AssignmentCache {
		delete(ctxData.context.AssignmentCache, k)
	}
	ctxData.context.ContextLock_.Unlock()

	ctxData.context.SetData(req.NewData)

	ctxData.eventCollector.HandleEvent(*ctxData.context, sdk.Refresh, req.NewData)

	newEvents := ctxData.eventCollector.events[eventsBefore:]

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

	eventsBefore := len(ctxData.eventCollector.events)

	ctxData.context.Close()

	newEvents := ctxData.eventCollector.events[eventsBefore:]

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
	router.HandleFunc("/context_payload", storePayloadHandler).Methods("PUT")
	router.HandleFunc("/context_payload/{payloadId}", getPayloadHandler).Methods("GET")
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
