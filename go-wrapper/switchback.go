package main

// The switchback endpoints and the behavioural self-test behind the
// `switchback` capability (R33 cluster-defined switchbacks), ported from the
// java-wrapper's SwitchbackSelfTest. The wrapper builds only against a go-sdk
// with SwitchbackAssign and SwitchbackPermutation, so the endpoints always
// answer. The context battery decodes its payload from JSON through the SDK's
// own deserializer and checks observed treatments, exposures and the
// published batch, publishing in memory.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"reflect"
	"strconv"
	"sync"

	"github.com/absmartly/go-sdk/sdk"
	"github.com/absmartly/go-sdk/sdk/future"
	"github.com/absmartly/go-sdk/sdk/jsonmodels"
)

type switchbackAssignRequest struct {
	ClusterUnit json.RawMessage `json:"clusterUnit"`
	PeriodIndex json.RawMessage `json:"periodIndex"`
	CycleIndex  json.RawMessage `json:"cycleIndex"`
	Seed        json.RawMessage `json:"seed"`
	ArmCount    json.RawMessage `json:"armCount"`
}

func writeSwitchbackError(w http.ResponseWriter, status int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(map[string]interface{}{"error": message, "events": []interface{}{}})
}

// switchbackString reads a JSON string field.
func switchbackString(raw json.RawMessage, name string) (string, error) {
	var text string
	if len(raw) == 0 || raw[0] != '"' || json.Unmarshal(raw, &text) != nil {
		return "", fmt.Errorf("%s must be a string", name)
	}
	return text, nil
}

// switchbackInt64 reads a signed 64-bit value sent as a decimal string,
// exactly (never through a float).
func switchbackInt64(raw json.RawMessage, name string) (int64, error) {
	text, err := switchbackString(raw, name)
	if err != nil {
		return 0, err
	}
	value, err := strconv.ParseInt(text, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("%s must be a signed 64-bit decimal string", name)
	}
	return value, nil
}

// switchbackArmCount reads armCount, a JSON integer of at least 2. The check
// is the wrapper's, so it does not depend on how the SDK reacts.
func switchbackArmCount(raw json.RawMessage) (int, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var number json.Number
	if len(raw) == 0 || raw[0] == '"' || decoder.Decode(&number) != nil {
		return 0, fmt.Errorf("armCount must be an integer")
	}
	value, err := strconv.ParseInt(number.String(), 10, 32)
	if err != nil {
		return 0, fmt.Errorf("armCount must be an integer")
	}
	if value < 2 {
		return 0, fmt.Errorf("armCount must be at least 2, got %d", value)
	}
	return int(value), nil
}

func decodeSwitchbackRequest(w http.ResponseWriter, r *http.Request, index string) (string, int64, int64, int, bool) {
	var req switchbackAssignRequest
	decoder := json.NewDecoder(r.Body)
	if err := decoder.Decode(&req); err != nil {
		writeSwitchbackError(w, http.StatusBadRequest, err.Error())
		return "", 0, 0, 0, false
	}
	if _, err := decoder.Token(); err != io.EOF {
		writeSwitchbackError(w, http.StatusBadRequest, "the request body must be a single JSON object")
		return "", 0, 0, 0, false
	}
	indexRaw := req.PeriodIndex
	if index == "cycleIndex" {
		indexRaw = req.CycleIndex
	}
	unit, err := switchbackString(req.ClusterUnit, "clusterUnit")
	var period, seed int64
	if err == nil {
		period, err = switchbackInt64(indexRaw, index)
	}
	if err == nil {
		seed, err = switchbackInt64(req.Seed, "seed")
	}
	var arms int
	if err == nil {
		arms, err = switchbackArmCount(req.ArmCount)
	}
	if err != nil {
		writeSwitchbackError(w, http.StatusBadRequest, err.Error())
		return "", 0, 0, 0, false
	}
	return unit, period, seed, arms, true
}

// The endpoints answer independently of the self-test verdict, so a regressed
// assigner reports its actual outputs.
func switchbackAssignHandler(w http.ResponseWriter, r *http.Request) {
	unit, period, seed, arms, ok := decodeSwitchbackRequest(w, r, "periodIndex")
	if !ok {
		return
	}
	variant, err := sdk.SwitchbackAssign(unit, period, seed, arms)
	if err != nil {
		writeSwitchbackError(w, http.StatusBadRequest, err.Error())
		return
	}
	writeResult(w, variant, nil)
}

func switchbackPermutationHandler(w http.ResponseWriter, r *http.Request) {
	unit, cycle, seed, arms, ok := decodeSwitchbackRequest(w, r, "cycleIndex")
	if !ok {
		return
	}
	permutation, err := sdk.SwitchbackPermutation(unit, cycle, seed, arms)
	if err != nil {
		writeSwitchbackError(w, http.StatusBadRequest, err.Error())
		return
	}
	writeResult(w, permutation, nil)
}

var (
	switchbackOnce   sync.Once
	switchbackResult bool
)

func switchbackSupported() bool {
	switchbackOnce.Do(func() { switchbackResult = runProbe("[switchback probe]", switchbackBattery) })
	return switchbackResult
}

// Golden-vector samples: {clusterUnit, periodIndex, seed, armCount, variant},
// including the int64 extremes, from fixtures/switchback/golden-vectors.json
// (the java-wrapper's sample).
var switchbackAssignSample = [][5]string{
	{"market_1", "0", "0", "2", "0"},
	{"market_1", "-9223372036854775808", "1", "2", "1"},
	{"market_1", "9223372036854775807", "1", "2", "1"},
	{"123456789", "0", "0", "2", "1"},
	{"market_1", "10", "0", "3", "2"},
	{"123456789", "-2", "-1", "3", "0"},
	{"market_1", "5", "1", "10", "0"},
	{"market_2", "-11", "-9223372036854775808", "10", "3"},
	{"market_2", "9223372036854775807", "-9223372036854775808", "10", "2"},
	{"market_1", "1", "9223372036854775807", "2", "1"},
}

var switchbackPermutationSample = []struct {
	unit, cycle, seed string
	want              []int
}{
	{"market_1", "0", "0", []int{0, 3, 7, 5, 8, 2, 6, 4, 1, 9}},
	{"market_1", "-9223372036854775808", "0", []int{1, 0}},
	{"market_1", "9223372036854775807", "1", []int{0, 1}},
	{"123456789", "0", "0", []int{2, 0, 1}},
}

// The grid the scenarios use: a 400,000 h period keeps "now" in period 0
// until 2069.
const (
	switchbackProbePeriodMs = int64(400_000) * 3_600_000
	switchbackProbeAnchor   = int64(1_704_067_200_000)
)

func switchbackBattery() error {
	const prefix = "[switchback probe]"
	for _, c := range switchbackAssignSample {
		period, _ := strconv.ParseInt(c[1], 10, 64)
		seed, _ := strconv.ParseInt(c[2], 10, 64)
		arms, _ := strconv.Atoi(c[3])
		want, _ := strconv.Atoi(c[4])
		if got, err := sdk.SwitchbackAssign(c[0], period, seed, arms); err != nil || got != want {
			return fmt.Errorf("%s golden assign FAILED for %v: got %d, %v", prefix, c, got, err)
		}
	}
	for _, c := range switchbackPermutationSample {
		cycle, _ := strconv.ParseInt(c.cycle, 10, 64)
		seed, _ := strconv.ParseInt(c.seed, 10, 64)
		if got, err := sdk.SwitchbackPermutation(c.unit, cycle, seed, len(c.want)); err != nil || !reflect.DeepEqual(got, c.want) {
			return fmt.Errorf("%s golden permutation FAILED for %v: got %v, %v", prefix, c, got, err)
		}
	}
	return switchbackContextBattery(prefix)
}

func switchbackExperimentJSON(id int, name string, seedHi int, seedLo int, arms int, servingUnit string) map[string]interface{} {
	split := make([]float64, arms)
	variants := make([]map[string]interface{}, arms)
	for i := range split {
		split[i] = 1.0 / float64(arms)
		variants[i] = map[string]interface{}{"name": string(rune('A' + i)), "config": nil}
	}
	experiment := map[string]interface{}{
		"id": id, "name": name, "iteration": 1, "unitType": "unit_serving", "seedHi": seedHi, "seedLo": seedLo,
		"split": split, "trafficSeedHi": 1, "trafficSeedLo": 2, "trafficSplit": []int{0, 1}, "fullOnVariant": 0,
		"applications": []map[string]string{{"name": "website"}}, "variants": variants, "audienceStrict": false,
	}
	if servingUnit != "" {
		experiment["randomizationType"] = "switchback"
		experiment["switchbackStartAt"] = switchbackProbeAnchor
		experiment["switchbackPeriodMs"] = switchbackProbePeriodMs
		experiment["switchbackDesignType"] = "balanced_block"
		experiment["switchbackEstimand"] = "global_average_treatment_effect"
		experiment["unitType"] = servingUnit
	}
	return experiment
}

// switchbackProbePayload is the java-wrapper's probe payload as raw JSON. The
// cluster maps are written out as text, so their key order is the declared
// order: sb_declared_order lists unit_user before unit_serving, which
// alphabetical order would reverse.
//
//	assignSwitchback("market_1", 0, 42, 10) = 3 (hashed key 5; serving-9
//	  uid 8, sticky 5; probe-user uid 9, sticky 7).
//	assignSwitchback("123456789", 0, 42, 10) = 6 ("123456789.0" 4).
func switchbackProbePayload() ([]byte, error) {
	type entry struct {
		experiment map[string]interface{}
		clusters   string
		values     string
		extra      map[string]interface{}
	}
	entries := []entry{
		{switchbackExperimentJSON(521, "sb_agree", 0, 42, 10, "unit_serving"), `{"unit_serving":"city","unit_user":"profile/city"}`, "", nil},
		{switchbackExperimentJSON(522, "sb_declared_order", 0, 42, 10, "unit_user"), `{"unit_user":"profile/city","unit_serving":"city"}`, "", nil},
		{switchbackExperimentJSON(523, "sb_disagree", 0, 42, 10, "unit_serving"), `{"unit_serving":"city","unit_user":"profile/zone"}`, "", nil},
		{switchbackExperimentJSON(524, "sb_whole_number", 0, 42, 10, "unit_serving"), `{"unit_serving":"store_number"}`, "", nil},
		{switchbackExperimentJSON(529, "sb_float_value", 0, 42, 10, "unit_serving"), `{"unit_serving":"store_float"}`, "", nil},
		{switchbackExperimentJSON(528, "sb_allow_empty", 0, 42, 10, "unit_serving"), `{"unit_serving":"city"}`, `[]`, nil},
		{switchbackExperimentJSON(525, "sb_value_filtered", 0, 1, 10, "unit_serving"), `{"unit_serving":"city"}`, `["market_2"]`, nil},
		{switchbackExperimentJSON(526, "sb_phase1e_only", 0, 1, 10, "unit_serving"), "", "",
			map[string]interface{}{"switchbackClusterUnitType": "unit_serving", "switchbackClusterCount": 10, "switchbackClusterAttribute": "city"}},
		{switchbackExperimentJSON(514, "sb_unit_baseline", -559038737, 1234567890, 3, ""), "", "", nil},
	}
	experiments := make([]map[string]interface{}, len(entries))
	for i, e := range entries {
		for k, v := range e.extra {
			e.experiment[k] = v
		}
		if e.clusters != "" {
			e.experiment["switchbackClusters"] = json.RawMessage(e.clusters)
		}
		if e.values != "" {
			e.experiment["switchbackClusterValues"] = json.RawMessage(e.values)
		}
		experiments[i] = e.experiment
	}
	return json.Marshal(map[string]interface{}{"experiments": experiments})
}

type switchbackProbeExposure struct {
	id       int
	name     string
	unit     string
	variant  int
	assigned bool
	eligible bool
	flags    [5]bool // overridden, fullOn, custom, audienceMismatch, ruleOverride; all false in this battery
}

func switchbackExposure(e jsonmodels.Exposure) switchbackProbeExposure {
	return switchbackProbeExposure{e.Id, e.Name, e.Unit, e.Variant, e.Assigned, e.Eligible,
		[5]bool{e.Overridden, e.FullOn, e.Custom, e.AudienceMismatch, e.RuleOverride}}
}

type switchbackExposureLog struct {
	mu        sync.Mutex
	exposures []jsonmodels.Exposure
}

func (l *switchbackExposureLog) HandleEvent(_ sdk.Context, eventType sdk.EventType, data interface{}) {
	if exposure, ok := data.(jsonmodels.Exposure); ok && eventType == sdk.Exposure {
		l.mu.Lock()
		l.exposures = append(l.exposures, exposure)
		l.mu.Unlock()
	}
}

func (l *switchbackExposureLog) len() int {
	l.mu.Lock()
	defer l.mu.Unlock()
	return len(l.exposures)
}

func (l *switchbackExposureLog) since(n int) []jsonmodels.Exposure {
	l.mu.Lock()
	defer l.mu.Unlock()
	return append([]jsonmodels.Exposure(nil), l.exposures[n:]...)
}

func switchbackContextBattery(prefix string) error {
	payload, err := switchbackProbePayload()
	if err != nil {
		return err
	}
	data, err := sdk.DefaultContextDataDeserializer{}.Deserialize(payload)
	if err != nil {
		return fmt.Errorf("%s FAILED: payload: %v", prefix, err)
	}
	logger := &switchbackExposureLog{}
	client := sdk.CreateDefaultClient(sdk.ClientConfig{
		Endpoint_: "http://localhost", ApiKey_: "probe", Application_: "website", Environment_: "test",
	})
	// Published in memory: /capabilities must not wait on the network.
	publisher := &switchbackCapturingPublisher{}
	absmartly := sdk.Create(sdk.ABSmartlyConfig{Client_: client, ContextPublisher_: publisher, ContextEventLogger_: logger})
	config := sdk.CreateDefaultContextConfig()
	config.Units_ = map[string]string{"unit_user": "probe-user", "unit_serving": "serving-9"}
	config.PublishDelay_ = -1
	config.RefreshInterval_ = 0
	context := absmartly.CreateContextWith(config, data)
	context.WaitUntilReady()
	if !context.IsReady() || context.IsFailed() {
		return fmt.Errorf("%s FAILED: context not ready", prefix)
	}
	defer context.Close()
	attributes, err := attributeValue(json.RawMessage(`{"city":"market_1","profile":{"city":"market_1","zone":"market_2"},
		"store_number":123456789,"store_float":123456789.0}`))
	if err != nil {
		return err
	}
	for name, value := range attributes.(map[string]interface{}) {
		if err := context.SetAttribute(name, value); err != nil {
			return err
		}
	}

	served := func(id int, name string, unit string, variant int) switchbackProbeExposure {
		return switchbackProbeExposure{id, name, unit, variant, true, true, [5]bool{}}
	}
	for _, c := range []struct {
		check      string
		experiment string
		treatment  int
		exposures  []switchbackProbeExposure
	}{
		{"agreement", "sb_agree", 3, []switchbackProbeExposure{served(521, "sb_agree", "unit_serving", 3)}},
		{"agreement repeat", "sb_agree", 3, nil},
		{"declared order", "sb_declared_order", 3, []switchbackProbeExposure{served(522, "sb_declared_order", "unit_user", 3)}},
		{"disagreement", "sb_disagree", 0, nil},
		{"whole number", "sb_whole_number", 6, []switchbackProbeExposure{served(524, "sb_whole_number", "unit_serving", 6)}},
		{"float value", "sb_float_value", 0, nil},
		{"value filtered out", "sb_value_filtered", 0, nil},
		{"empty allow-list admits all", "sb_allow_empty", 3, []switchbackProbeExposure{served(528, "sb_allow_empty", "unit_serving", 3)}},
		{"Phase 1E fields only", "sb_phase1e_only", 0, nil},
		{"unit baseline", "sb_unit_baseline", 1, []switchbackProbeExposure{served(514, "sb_unit_baseline", "unit_serving", 1)}},
	} {
		before := logger.len()
		got, err := context.GetTreatment(c.experiment)
		if err != nil {
			return fmt.Errorf("%s check %s: %v", prefix, c.check, err)
		}
		var emitted []switchbackProbeExposure
		for _, e := range logger.since(before) {
			emitted = append(emitted, switchbackExposure(e))
		}
		if got != c.treatment || !reflect.DeepEqual(emitted, c.exposures) {
			return fmt.Errorf("%s check %s FAILED: expected treatment=%d exposures=%v, got treatment=%d exposures=%v",
				prefix, c.check, c.treatment, c.exposures, got, emitted)
		}
	}
	if err := context.Publish(); err != nil {
		return fmt.Errorf("%s publish FAILED: %v", prefix, err)
	}
	publisher.mu.Lock()
	defer publisher.mu.Unlock()
	if len(publisher.events) != 1 || !reflect.DeepEqual(publisher.events[0].Exposures, logger.since(0)) {
		return fmt.Errorf("%s publish FAILED: published %+v, logged %+v", prefix, publisher.events, logger.since(0))
	}
	return nil
}

// switchbackCapturingPublisher records publish batches in memory.
type switchbackCapturingPublisher struct {
	mu     sync.Mutex
	events []jsonmodels.PublishEvent
}

func (p *switchbackCapturingPublisher) Publish(_ sdk.Context, event jsonmodels.PublishEvent) *future.Future {
	p.mu.Lock()
	p.events = append(p.events, event)
	p.mu.Unlock()
	result, done := future.New()
	done(nil, nil)
	return result
}
