package main

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/absmartly/go-sdk/sdk"
	"github.com/absmartly/go-sdk/sdk/jsonmodels"
)

func TestCustomFieldNormalization(t *testing.T) {
	for _, test := range []struct {
		name     string
		fields   string
		keys     string
		typeJSON string
	}{
		{"absent", "", "[]", "null"},
		{"empty", `,"customFieldValues":[]`, "[]", "null"},
		{"populated", `,"customFieldValues":[{"name":"owner","type":"string","value":"sdk-team"}]`, `["owner"]`, `"string"`},
		{"number", `,"customFieldValues":[{"name":"owner","type":"number","value":"42"}]`, `["owner"]`, `"number"`},
		{"boolean", `,"customFieldValues":[{"name":"owner","type":"boolean","value":"false"}]`, `["owner"]`, `"boolean"`},
		{"json", `,"customFieldValues":[{"name":"owner","type":"json","value":"{}"}]`, `["owner"]`, `"json"`},
	} {
		t.Run(test.name, func(t *testing.T) {
			var data jsonmodels.ContextData
			if err := json.Unmarshal([]byte(`{"experiments":[{"id":800,"name":"exp"`+test.fields+`}]}`), &data); err != nil {
				t.Fatal(err)
			}
			a := sdk.Create(sdk.ABSmartlyConfig{})
			config := sdk.CreateDefaultContextConfig()
			config.PublishDelay_ = -1
			config.RefreshInterval_ = 0
			cd := &ContextData{context: a.CreateContextWith(config, data)}
			cd.context.WaitUntilReady()
			for _, call := range []struct {
				name string
				fn   func(*ContextData, func(interface{}) error) (interface{}, error)
				want string
			}{
				{"keys", customFieldKeys, test.keys},
				{"type", customFieldValueType, test.typeJSON},
			} {
				t.Run(call.name, func(t *testing.T) {
					result, err := call.fn(cd, json.NewDecoder(strings.NewReader(`{"experimentName":"exp","fieldName":"owner"}`)).Decode)
					if err != nil {
						t.Fatal(err)
					}
					wire, err := json.Marshal(result)
					if err != nil {
						t.Fatal(err)
					}
					if string(wire) != call.want {
						t.Fatalf("got %s, want %s", wire, call.want)
					}
				})
			}
		})
	}
}
