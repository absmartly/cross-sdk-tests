package main

// Behavioural self-test for the `holdouts` and `holdout_arms` capabilities,
// ported from javascript-wrapper/holdoutSelfTest.js (and the java-wrapper's
// HoldoutSelfTest/HoldoutArmsSelfTest). It builds a real context against the
// go-sdk the wrapper is compiled with, calls GetTreatment, and checks both
// the variant and the exact ordered exposures each call queues. The fixtures
// are decoded from JSON rather than built from struct fields, so a go-sdk
// without holdouts still compiles: it ignores the holdout keys, assigns the
// covered experiments normally, and the battery reports false.
//
// Each battery runs once per process; the result is cached. A panic anywhere
// in a battery makes the capability false instead of failing /capabilities.

import (
	"encoding/json"
	"fmt"
	"log"
	"sync"

	"github.com/absmartly/go-sdk/sdk"
	"github.com/absmartly/go-sdk/sdk/future"
	"github.com/absmartly/go-sdk/sdk/jsonmodels"
)

type probeExposure struct {
	Id      int
	Name    string
	Variant int
}

type exposureLog struct {
	mu        sync.Mutex
	exposures []probeExposure
}

func (l *exposureLog) HandleEvent(_ sdk.Context, eventType sdk.EventType, data interface{}) {
	if eventType != sdk.Exposure {
		return
	}
	if exposure, ok := data.(jsonmodels.Exposure); ok {
		l.mu.Lock()
		l.exposures = append(l.exposures, probeExposure{exposure.Id, exposure.Name, exposure.Variant})
		l.mu.Unlock()
	}
}

func (l *exposureLog) since(n int) []probeExposure {
	l.mu.Lock()
	defer l.mu.Unlock()
	return append([]probeExposure(nil), l.exposures[n:]...)
}

func (l *exposureLog) len() int {
	l.mu.Lock()
	defer l.mu.Unlock()
	return len(l.exposures)
}

type probeExperiment struct {
	id            int
	name          string
	unitType      string
	split         []float64
	fullOnVariant int
	variants      int
	holdoutIds    []int
}

type probeHoldout struct {
	id       int
	name     string
	unitType string
	seedHi   int
	seedLo   int
	split    []float64
}

func variantsJSON(n int) []map[string]interface{} {
	var variants = make([]map[string]interface{}, n)
	for i := range variants {
		variants[i] = map[string]interface{}{"name": string(rune('A' + i)), "config": nil}
	}
	return variants
}

// probeData builds the context payload as JSON, in the collector's shape, and
// decodes it into the SDK's model.
func probeData(experiments []probeExperiment, holdouts []probeHoldout) (jsonmodels.ContextData, error) {
	var payload = map[string]interface{}{}
	var exps []map[string]interface{}
	for _, e := range experiments {
		exps = append(exps, map[string]interface{}{
			"id": e.id, "name": e.name, "iteration": 1, "unitType": e.unitType, "seedHi": 100, "seedLo": 200,
			"split": e.split, "trafficSeedHi": 1, "trafficSeedLo": 2, "trafficSplit": []float64{0, 1},
			"fullOnVariant": e.fullOnVariant, "applications": []map[string]string{{"name": "website"}},
			"variants": variantsJSON(e.variants), "audience": nil, "audienceStrict": false,
			"customFieldValues": nil, "holdoutIds": e.holdoutIds,
		})
	}
	var hos []map[string]interface{}
	for _, h := range holdouts {
		var holdoutType = "full"
		if len(h.split) == 3 {
			holdoutType = "all_full_on"
		}
		hos = append(hos, map[string]interface{}{
			"id": h.id, "name": h.name, "iteration": 1, "unitType": h.unitType, "seedHi": h.seedHi,
			"seedLo": h.seedLo, "split": h.split, "holdoutType": holdoutType,
		})
	}
	payload["experiments"] = exps
	payload["holdouts"] = hos
	var raw, err = json.Marshal(payload)
	if err != nil {
		return jsonmodels.ContextData{}, err
	}
	var data jsonmodels.ContextData
	err = json.Unmarshal(raw, &data)
	return data, err
}

func probeContext(data jsonmodels.ContextData, units map[string]string) (*sdk.Context, *exposureLog) {
	var logger = &exposureLog{}
	var client = sdk.CreateDefaultClient(sdk.ClientConfig{
		Endpoint_: "http://localhost", ApiKey_: "probe", Application_: "website", Environment_: "test",
	})
	var absmartly = sdk.Create(sdk.ABSmartlyConfig{Client_: client, ContextEventLogger_: logger})
	var config = sdk.CreateDefaultContextConfig()
	config.Units_ = units
	config.PublishDelay_ = -1
	config.RefreshInterval_ = 0
	var context = absmartly.CreateContextWith(config, data)
	context.WaitUntilReady()
	return context, logger
}

// checkTreatment calls GetTreatment once and requires the variant and the
// exact ordered exposures that call queued.
func checkTreatment(prefix string, label string, context *sdk.Context, logger *exposureLog, experiment string,
	want int, wantExposures ...probeExposure) error {
	var before = logger.len()
	var got, err = context.GetTreatment(experiment)
	if err != nil {
		return fmt.Errorf("%s check %s: %v", prefix, label, err)
	}
	var exposures = logger.since(before)
	var same = got == want && len(exposures) == len(wantExposures)
	for i := 0; same && i < len(exposures); i++ {
		same = exposures[i] == wantExposures[i]
	}
	if !same {
		return fmt.Errorf("%s check %s FAILED: expected treatment=%d exposures=%v, got treatment=%d exposures=%v",
			prefix, label, want, wantExposures, got, exposures)
	}
	return nil
}

func runProbe(prefix string, battery func() error) (passed bool) {
	defer func() {
		if r := recover(); r != nil {
			log.Printf("%s behavioural self-test FAILED: panic: %v", prefix, r)
			passed = false
		}
	}()
	if err := battery(); err != nil {
		log.Print(err)
		return false
	}
	log.Printf("%s behavioural self-test PASSED", prefix)
	return true
}

const (
	probeUID         = "e791e240fcd3df7d238cfc285f475e8152fcc0ec"
	probeOtherUID    = "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3"
	probeHeldOutType = "session_id"
	probeOtherType   = "session_id_b"
	probeLateType    = "user_id"
)

// Mirrors scenarios 203-208 and 221.
func holdoutsBattery() error {
	const prefix = "[holdouts probe]"
	var twoArm = []float64{0.1, 0.9}
	var data, err = probeData([]probeExperiment{
		{301, "probe_a_covered", probeHeldOutType, []float64{0.5, 0.5}, 0, 2, []int{401}},
		{302, "probe_b_covered", probeOtherType, []float64{0.5, 0.5}, 0, 2, []int{402}},
		{304, "probe_c_union", probeHeldOutType, []float64{0.5, 0.5}, 0, 2, []int{404, 405}},
		{305, "probe_d_covered", probeHeldOutType, []float64{0.5, 0.5}, 0, 2, []int{406}},
		{306, "probe_d_uncovered_sibling", probeHeldOutType, []float64{0.5, 0.5}, 0, 2, nil},
		{307, "probe_e_dangling_plus_valid", probeHeldOutType, []float64{0.5, 0.5}, 0, 2, []int{999, 407}},
		{303, "probe_f_fullon", probeHeldOutType, []float64{0.5, 0.5}, 2, 3, []int{403}},
		{308, "probe_g_late_unit", probeLateType, []float64{0.5, 0.5, 0}, 2, 3, []int{408}},
	}, []probeHoldout{
		{401, "probe_a_holdout", probeHeldOutType, 13, 111, twoArm},
		{402, "probe_b_holdout", probeOtherType, 13, 111, twoArm},
		{404, "probe_c_holdout_low", probeHeldOutType, 1, 222, twoArm},
		{405, "probe_c_holdout_high", probeHeldOutType, 13, 111, twoArm},
		{406, "probe_d_holdout", probeHeldOutType, 13, 111, twoArm},
		{407, "probe_e_holdout", probeHeldOutType, 13, 111, twoArm},
		{403, "probe_f_holdout", probeHeldOutType, 13, 111, twoArm},
		{408, "probe_g_holdout", probeLateType, 1, 222, twoArm},
	})
	if err != nil {
		return err
	}
	var context, logger = probeContext(data, map[string]string{probeHeldOutType: probeUID, probeOtherType: probeOtherUID})
	if !context.IsReady() || context.IsFailed() {
		return fmt.Errorf("%s context not ready", prefix)
	}
	var checks = []func() error{
		func() error {
			return checkTreatment(prefix, "A (203)", context, logger, "probe_a_covered", 0,
				probeExposure{401, "probe_a_holdout", 0})
		},
		func() error {
			return checkTreatment(prefix, "B (204)", context, logger, "probe_b_covered", 0,
				probeExposure{302, "probe_b_covered", 0}, probeExposure{402, "probe_b_holdout", 1})
		},
		func() error {
			return checkTreatment(prefix, "C (205)", context, logger, "probe_c_union", 0,
				probeExposure{404, "probe_c_holdout_low", 1}, probeExposure{405, "probe_c_holdout_high", 0})
		},
		func() error {
			return checkTreatment(prefix, "D (206, covered)", context, logger, "probe_d_covered", 0,
				probeExposure{406, "probe_d_holdout", 0})
		},
		func() error {
			return checkTreatment(prefix, "D (206, uncovered sibling)", context, logger, "probe_d_uncovered_sibling", 1,
				probeExposure{306, "probe_d_uncovered_sibling", 1})
		},
		func() error {
			return checkTreatment(prefix, "E (207)", context, logger, "probe_e_dangling_plus_valid", 0,
				probeExposure{407, "probe_e_holdout", 0})
		},
		func() error {
			return checkTreatment(prefix, "F (208)", context, logger, "probe_f_fullon", 0,
				probeExposure{403, "probe_f_holdout", 0})
		},
		func() error {
			var before = logger.len()
			var peeked, err = context.PeekTreatment("probe_g_late_unit")
			if err != nil || peeked != 2 || len(logger.since(before)) != 0 {
				return fmt.Errorf("%s check G (221, pre-unit peek) FAILED: got %d, %v, exposures %v", prefix, peeked, err,
					logger.since(before))
			}
			if err := context.SetUnit(probeLateType, probeUID); err != nil {
				return err
			}
			return checkTreatment(prefix, "G (221, post-unit treatment)", context, logger, "probe_g_late_unit", 2,
				probeExposure{308, "probe_g_late_unit", 2}, probeExposure{408, "probe_g_holdout", 1})
		},
	}
	for _, check := range checks {
		if err := check(); err != nil {
			return err
		}
	}
	return nil
}

// Mirrors scenarios 214-216, 219, 220 and 222: the arm count comes from the
// split, and in a three-arm holdout arm 1 holds out only experiments that are
// not full-on.
func holdoutArmsBattery() error {
	const prefix = "[holdout_arms probe]"
	var covered = []float64{0.5, 0.5}
	var threeArm = []float64{0.3, 0.3, 0.4}
	var data, err = probeData([]probeExperiment{
		{601, "arms_0_non_fullon", probeHeldOutType, covered, 0, 2, []int{621}},
		{602, "arms_0_fullon", probeHeldOutType, covered, 2, 3, []int{621}},
		{603, "arms_1_non_fullon", probeHeldOutType, covered, 0, 2, []int{622}},
		{604, "arms_1_fullon", probeHeldOutType, covered, 2, 3, []int{622}},
		{605, "arms_2_non_fullon", probeHeldOutType, covered, 0, 2, []int{623}},
		{606, "arms_2_fullon", probeHeldOutType, covered, 2, 3, []int{623}},
		{607, "arms_union_two_arm", probeHeldOutType, covered, 2, 3, []int{624, 623}},
	}, []probeHoldout{
		{621, "arms_holdout_0", probeHeldOutType, 0, 1, threeArm},
		{622, "arms_holdout_1", probeHeldOutType, 0, 3, threeArm},
		{623, "arms_holdout_2", probeHeldOutType, 0, 0, threeArm},
		{624, "arms_holdout_two_arm", probeHeldOutType, 13, 111, []float64{0.1, 0.9}},
	})
	if err != nil {
		return err
	}
	var context, logger = probeContext(data, map[string]string{probeHeldOutType: probeUID})
	if !context.IsReady() || context.IsFailed() {
		return fmt.Errorf("%s context not ready", prefix)
	}
	var checks = []func() error{
		func() error {
			return checkTreatment(prefix, "0 non-full-on (214)", context, logger, "arms_0_non_fullon", 0,
				probeExposure{621, "arms_holdout_0", 0})
		},
		func() error {
			return checkTreatment(prefix, "0 full-on (214)", context, logger, "arms_0_fullon", 0)
		},
		func() error {
			return checkTreatment(prefix, "1 non-full-on (215)", context, logger, "arms_1_non_fullon", 0,
				probeExposure{622, "arms_holdout_1", 1})
		},
		func() error {
			return checkTreatment(prefix, "1 full-on (215)", context, logger, "arms_1_fullon", 2,
				probeExposure{604, "arms_1_fullon", 2})
		},
		func() error {
			return checkTreatment(prefix, "2 non-full-on (216)", context, logger, "arms_2_non_fullon", 1,
				probeExposure{605, "arms_2_non_fullon", 1}, probeExposure{623, "arms_holdout_2", 2})
		},
		func() error {
			return checkTreatment(prefix, "2 full-on (216)", context, logger, "arms_2_fullon", 2,
				probeExposure{606, "arms_2_fullon", 2})
		},
		func() error {
			// Union (219): the two-arm holdout holds the unit out although
			// the three-arm one, at arm 2, does not.
			return checkTreatment(prefix, "union (219)", context, logger, "arms_union_two_arm", 0,
				probeExposure{624, "arms_holdout_two_arm", 0})
		},
	}
	for _, check := range checks {
		if err := check(); err != nil {
			return err
		}
	}
	return nil
}

// dedupProvider serves the payload a refresh installs.
type dedupProvider struct {
	mu   sync.Mutex
	next jsonmodels.ContextData
}

func (p *dedupProvider) GetContextData() *future.Future {
	p.mu.Lock()
	var data = p.next
	p.mu.Unlock()
	var ft, done = future.New()
	done(data, nil)
	return ft
}

// Mirrors Holdout Conformance scenario 500 and the JS wrapper's
// runHoldoutIdsDedupBattery: a holdoutIds list naming one holdout twice is
// one holdout, and a refresh that only drops the duplicate is not a coverage
// change, so nothing is exposed again.
func holdoutIdsDedupBattery() error {
	const prefix = "[holdoutIdsDedup probe]"
	var payload = func(adjacent []int, apart []int) (jsonmodels.ContextData, error) {
		var data, err = probeData([]probeExperiment{
			{801, "dedup_covered", "session_id", []float64{0, 1}, 0, 2, adjacent},
			{802, "dedup_apart", "user_id", []float64{0, 1}, 0, 2, apart},
		}, []probeHoldout{
			{901, "dedup_holdout", "session_id", 1, 1, []float64{0.5, 0.5}},
			{902, "dedup_apart_a", "user_id", 1, 2, []float64{0.5, 0.5}},
			{903, "dedup_apart_b", "user_id", 1, 4, []float64{0.5, 0.5}},
		})
		return data, err
	}
	var initial, err = payload([]int{901, 901}, []int{902, 903, 902, 903})
	if err != nil {
		return err
	}
	var refreshed, rerr = payload([]int{901}, []int{902, 903})
	if rerr != nil {
		return rerr
	}
	var provider = &dedupProvider{next: refreshed}
	var logger = &exposureLog{}
	var client = sdk.CreateDefaultClient(sdk.ClientConfig{
		Endpoint_: "http://localhost", ApiKey_: "probe", Application_: "website", Environment_: "test",
	})
	var absmartly = sdk.Create(sdk.ABSmartlyConfig{Client_: client, ContextDataProvider_: provider, ContextEventLogger_: logger})
	var config = sdk.CreateDefaultContextConfig()
	config.Units_ = map[string]string{"session_id": "svk-unit-1", "user_id": "svk-user-1"}
	config.PublishDelay_ = -1
	config.RefreshInterval_ = 0
	var context = absmartly.CreateContextWith(config, initial)
	context.WaitUntilReady()
	if !context.IsReady() || context.IsFailed() {
		return fmt.Errorf("%s context not ready", prefix)
	}
	if err := checkTreatment(prefix, "adjacent duplicate", context, logger, "dedup_covered", 1,
		probeExposure{801, "dedup_covered", 1}, probeExposure{901, "dedup_holdout", 1}); err != nil {
		return err
	}
	if err := checkTreatment(prefix, "non-adjacent duplicate", context, logger, "dedup_apart", 1,
		probeExposure{802, "dedup_apart", 1}, probeExposure{902, "dedup_apart_a", 1},
		probeExposure{903, "dedup_apart_b", 1}); err != nil {
		return err
	}
	context.Refresh()
	if err := checkTreatment(prefix, "refresh dropping the adjacent duplicate", context, logger, "dedup_covered", 1); err != nil {
		return err
	}
	return checkTreatment(prefix, "refresh dropping the non-adjacent duplicate", context, logger, "dedup_apart", 1)
}

var (
	holdoutsOnce      sync.Once
	holdoutsResult    bool
	holdoutArmsOnce   sync.Once
	holdoutArmsResult bool
	dedupOnce         sync.Once
	dedupResult       bool
)

// holdoutIdsDedupSupported is gated on the base holdouts battery.
func holdoutIdsDedupSupported() bool {
	dedupOnce.Do(func() {
		dedupResult = holdoutsSupported() && runProbe("[holdoutIdsDedup probe]", holdoutIdsDedupBattery)
	})
	return dedupResult
}

func holdoutsSupported() bool {
	holdoutsOnce.Do(func() { holdoutsResult = runProbe("[holdouts probe]", holdoutsBattery) })
	return holdoutsResult
}

// holdoutArmsSupported is gated on the base holdouts battery: three-arm
// support cannot exist without it.
func holdoutArmsSupported() bool {
	holdoutArmsOnce.Do(func() {
		holdoutArmsResult = holdoutsSupported() && runProbe("[holdout_arms probe]", holdoutArmsBattery)
	})
	return holdoutArmsResult
}
