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
	if len(data.Holdouts) != 2 || data.Holdouts[0].HoldoutType != "all_full_on" || data.Holdouts[1].HoldoutType != "" {
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
