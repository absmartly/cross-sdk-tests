package main

import (
	"reflect"
	"testing"

	"github.com/absmartly/go-sdk/sdk/jsonmodels"
)

func TestThreeArmProbeOmitsHoldoutType(t *testing.T) {
	data, err := probeData(nil, []probeHoldout{
		{621, "typed", probeHeldOutType, 0, 1, []float64{0.3, 0.3, 0.4}},
		{622, "untyped", probeHeldOutType, 0, 3, []float64{0.3, 0.3, 0.4}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if data.Holdouts[0].HoldoutType != "all_full_on" || data.Holdouts[1].HoldoutType != "" {
		t.Fatalf("holdout types: %+v", data.Holdouts)
	}
	if err := holdoutArmsBattery(); err != nil {
		t.Fatal(err)
	}
}

func TestSwitchbackProjectionRetainsAllFlags(t *testing.T) {
	baseline := jsonmodels.Exposure{Id: 521, Name: "sb_agree", Unit: "unit_serving", Variant: 3,
		Assigned: true, Eligible: true}
	want := switchbackProbeExposure{521, "sb_agree", "unit_serving", 3, true, true, [5]bool{}}
	if got := switchbackExposure(baseline); got != want {
		t.Fatalf("baseline %+v, want %+v", got, want)
	}
	for _, flag := range []string{"Assigned", "Eligible", "Overridden", "FullOn", "Custom", "AudienceMismatch", "RuleOverride"} {
		t.Run(flag, func(t *testing.T) {
			changed := baseline
			field := reflect.ValueOf(&changed).Elem().FieldByName(flag)
			field.SetBool(!field.Bool())
			if switchbackExposure(changed) == want {
				t.Fatalf("projection discarded %s", flag)
			}
		})
	}
	if err := switchbackBattery(); err != nil {
		t.Fatal(err)
	}
}

func TestSharedVariableKeysBatteryPasses(t *testing.T) {
	if err := sharedVariableKeysBattery(); err != nil {
		t.Fatal(err)
	}
	if !sharedVariableKeysSupported() {
		t.Fatal("capability false")
	}
}

// Each check compares the value and the single exposure, so a different
// winner, a fall-through value or an extra exposure fails the battery.
func TestSharedVariableKeysCheckIsNotVacuous(t *testing.T) {
	for _, c := range svkBattery {
		var wrongWinner = c
		wrongWinner.wantId = c.experiments[1].id + c.experiments[0].id - c.wantId
		if wrongWinner.wantId == c.wantId {
			wrongWinner.wantId++
		}
		if svkCheck("test", wrongWinner) == nil {
			t.Errorf("%s: a different winner passed", c.label)
		}
		var wrongValue = c
		wrongValue.wantValue = "fall-through"
		if svkCheck("test", wrongValue) == nil {
			t.Errorf("%s: a different value passed", c.label)
		}
	}
}
