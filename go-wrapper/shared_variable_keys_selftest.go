package main

// Behavioural self-test for the `sharedVariableKeys` capability, ported from
// javascript-wrapper/sharedVariableKeysSelfTest.js: a key shared by several
// experiments resolves as java-sdk Context.getVariableAssignment. Each case
// builds a real context against the go-sdk the wrapper is compiled with and
// checks the returned value and the exact exposures, as scenarios 341, 350
// and 351:
//
//   - a strict-mismatched lower-id candidate listed after an assigned higher-id
//     one is skipped and never exposed, and the assigned one wins;
//   - two assigned candidates, the lower id listed second in the payload: the
//     candidates are visited in id order, so the lower id still wins;
//   - the winner's variant lacks the key: the default is returned (no
//     fall-through to the later candidate) and only the winner is exposed.
//
// PeekVariableValue must expose nothing in any of them. The result is cached;
// a panic makes the capability false.

import (
	"encoding/json"
	"fmt"
	"sync"

	"github.com/absmartly/go-sdk/sdk"
	"github.com/absmartly/go-sdk/sdk/jsonmodels"
)

const (
	svkUnitType = "session_id"
	svkUID      = "svk-probe-unit"
	svkKey      = "shared.key"
	svkDefault  = "caller-default"
)

type svkExperiment struct {
	id       int
	name     string
	configs  []map[string]interface{}
	audience string
}

type svkCase struct {
	label       string
	experiments []svkExperiment
	attributes  map[string]interface{}
	wantValue   interface{}
	wantId      int
}

var svkUSAudience = `{"filter":[{"eq":[{"var":"Country"},{"value":"US"}]}]}`

var svkBattery = []svkCase{
	{"strict mismatch skipped", []svkExperiment{
		{176, "svk_assigned", []map[string]interface{}{{svkKey: "assigned-control"}, {svkKey: "assigned-treatment"}}, ""},
		{175, "svk_strict", []map[string]interface{}{{svkKey: "strict-control"}, {svkKey: "strict-treatment"}}, svkUSAudience},
	}, map[string]interface{}{"Country": "IE"}, "assigned-treatment", 176},
	{"visited in id order", []svkExperiment{
		{191, "svk_high_id", []map[string]interface{}{{svkKey: "high-control"}, {svkKey: "high-treatment"}}, ""},
		{190, "svk_low_id", []map[string]interface{}{{svkKey: "low-control"}, {svkKey: "low-treatment"}}, ""},
	}, nil, "low-treatment", 190},
	{"winner lacks key", []svkExperiment{
		{195, "svk_lacks_key", []map[string]interface{}{{svkKey: "lacks-control"}, {"other.key": "x"}}, ""},
		{196, "svk_after", []map[string]interface{}{{svkKey: "after-control"}, {svkKey: "after-treatment"}}, ""},
	}, nil, svkDefault, 195},
}

// svkData builds the payload as JSON, in the collector's shape; split [0, 1]
// assigns every unit variant 1.
func svkData(experiments []svkExperiment) (jsonmodels.ContextPayload, error) {
	var exps []map[string]interface{}
	for _, e := range experiments {
		var variants []map[string]interface{}
		for i, config := range e.configs {
			var text, err = json.Marshal(config)
			if err != nil {
				return jsonmodels.ContextPayload{}, err
			}
			variants = append(variants, map[string]interface{}{"name": string(rune('A' + i)), "config": string(text)})
		}
		var audience interface{}
		if e.audience != "" {
			audience = e.audience
		}
		exps = append(exps, map[string]interface{}{
			"id": e.id, "name": e.name, "iteration": 1, "unitType": svkUnitType, "seedHi": 0, "seedLo": 0,
			"split": []float64{0, 1}, "trafficSeedHi": 0, "trafficSeedLo": 0, "trafficSplit": []float64{0, 1},
			"fullOnVariant": 0, "applications": []map[string]string{{"name": "website"}}, "variants": variants,
			"audience": audience, "audienceStrict": e.audience != "", "customFieldValues": nil,
		})
	}
	var raw, err = json.Marshal(map[string]interface{}{"experiments": exps})
	if err != nil {
		return jsonmodels.ContextPayload{}, err
	}
	return sdk.DefaultContextDataDeserializer{}.DeserializePayload(raw)
}

func svkCheck(prefix string, c svkCase) error {
	var data, err = svkData(c.experiments)
	if err != nil {
		return fmt.Errorf("%s FAILED (%s): payload: %v", prefix, c.label, err)
	}
	var context, logger = probeContext(data, map[string]string{svkUnitType: svkUID})
	for name, value := range c.attributes {
		if err := context.SetAttribute(name, value); err != nil {
			return fmt.Errorf("%s FAILED (%s): %v", prefix, c.label, err)
		}
	}
	peeked, err := context.PeekVariableValue(svkKey, svkDefault)
	if err != nil {
		return fmt.Errorf("%s FAILED (%s): peek: %v", prefix, c.label, err)
	}
	var peekExposures = logger.len()
	value, err := context.GetVariableValue(svkKey, svkDefault)
	if err != nil {
		return fmt.Errorf("%s FAILED (%s): %v", prefix, c.label, err)
	}
	var exposures = logger.since(0)
	if peeked != c.wantValue || peekExposures != 0 || value != c.wantValue || context.GetPendingCount() != 1 ||
		len(exposures) != 1 || exposures[0].Id != c.wantId || exposures[0].Variant != 1 {
		return fmt.Errorf("%s FAILED (%s): want %v from %d with one exposure, got peek %v (%d exposures), value %v, exposures %v, pending %d",
			prefix, c.label, c.wantValue, c.wantId, peeked, peekExposures, value, exposures, context.GetPendingCount())
	}
	return nil
}

func sharedVariableKeysBattery() error {
	const prefix = "[sharedVariableKeys probe]"
	for _, c := range svkBattery {
		if err := svkCheck(prefix, c); err != nil {
			return err
		}
	}
	return nil
}

var (
	sharedVariableKeysOnce   sync.Once
	sharedVariableKeysResult bool
)

func sharedVariableKeysSupported() bool {
	sharedVariableKeysOnce.Do(func() {
		sharedVariableKeysResult = runProbe("[sharedVariableKeys probe]", sharedVariableKeysBattery)
	})
	return sharedVariableKeysResult
}
