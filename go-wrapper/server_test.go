package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func post(t *testing.T, router http.Handler, path string, body string) (int, map[string]interface{}) {
	t.Helper()
	var recorder = httptest.NewRecorder()
	router.ServeHTTP(recorder, httptest.NewRequest(http.MethodPost, path, strings.NewReader(body)))
	var response map[string]interface{}
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatalf("%s: %d %q is not JSON", path, recorder.Code, recorder.Body.String())
	}
	return recorder.Code, response
}

// A strict numeric audience balance > 99: every attribute literal compares
// as a number, an integer beyond int64 and an exponent literal included,
// through both attribute write paths.
func TestAttributesCompareNumericallyThroughBothWritePaths(t *testing.T) {
	var router = newRouter()
	var audience, _ = json.Marshal(map[string]interface{}{"filter": []interface{}{
		map[string]interface{}{"gt": []interface{}{map[string]interface{}{"var": "balance"}, map[string]interface{}{"value": 99}}}}})
	var experiment, _ = json.Marshal(map[string]interface{}{"id": 1, "name": "exp", "unitType": "session_id", "iteration": 1,
		"seedHi": 3603515, "seedLo": 233373850, "split": []float64{0, 1}, "trafficSeedHi": 1, "trafficSeedLo": 2,
		"trafficSplit": []float64{0, 1}, "applications": []interface{}{map[string]string{"name": "website"}},
		"variants":       []interface{}{map[string]string{"name": "A"}, map[string]string{"name": "B"}},
		"audienceStrict": true, "audience": string(audience)})
	var data = `{"experiments":[` + string(experiment) + `]}`
	for literal, want := range map[string]float64{"9223372036854775808": 1, "9.223372036854776e18": 1,
		"123456789012345678901234567890": 1, "100": 1, "100.0": 1, "99": 0, "99.0": 0} {
		var _, created = post(t, router, "/context", `{"data":`+data+`,"units":{"session_id":"u1"},"attributes":{"balance":`+literal+`}}`)
		var id = created["result"].(map[string]interface{})["contextId"].(string)
		if _, treatment := post(t, router, "/context/"+id+"/treatment", `{"experimentName":"exp"}`); treatment["result"] != want {
			t.Errorf("create with %s: variant %v, want %v", literal, treatment["result"], want)
		}
		_, created = post(t, router, "/context", `{"data":`+data+`,"units":{"session_id":"u1"}}`)
		id = created["result"].(map[string]interface{})["contextId"].(string)
		if code, _ := post(t, router, "/context/"+id+"/attribute", `{"name":"balance","value":`+literal+`}`); code != http.StatusOK {
			t.Fatalf("set %s: %d", literal, code)
		}
		if _, treatment := post(t, router, "/context/"+id+"/treatment", `{"experimentName":"exp"}`); treatment["result"] != want {
			t.Errorf("set %s: variant %v, want %v", literal, treatment["result"], want)
		}
	}
}

// A number beyond float64 is rejected on both write paths, before a context
// is created, and a rejected update keeps the previous value readable.
func TestOutOfRangeAttributesAreRejected(t *testing.T) {
	var router = newRouter()
	if code, _ := post(t, router, "/context", `{"data":{"experiments":[]},"units":{"u":"1"},"attributes":{"bad":[1e309]}}`); code != http.StatusBadRequest {
		t.Fatalf("create: %d", code)
	}
	var _, created = post(t, router, "/context", `{"data":{"experiments":[]},"units":{"u":"1"},"attributes":{"ok":3}}`)
	var id = created["result"].(map[string]interface{})["contextId"].(string)
	if code, _ := post(t, router, "/context/"+id+"/attribute", `{"name":"ok","value":{"a":[1e309]}}`); code != http.StatusBadRequest {
		t.Fatalf("set: %d", code)
	}
	if code, read := post(t, router, "/context/"+id+"/getAttribute", `{"name":"ok"}`); code != http.StatusOK || read["result"] != float64(3) {
		t.Fatalf("read after a rejected set: %d %v", code, read)
	}
}

// Integer literals within int reach the SDK as int and everything else as
// float64, so a switchback cluster key tells 3 from 3.0.
func TestAttributeValueKeepsIntegersApartFromFloats(t *testing.T) {
	for literal, want := range map[string]interface{}{"3": 3, "3.0": 3.0, "1e1": 10.0, "-7": -7,
		"9223372036854775808": 9223372036854775808.0, `[1,2.5,{"a":4}]`: []interface{}{1, 2.5, map[string]interface{}{"a": 4}}} {
		var got, err = attributeValue(json.RawMessage(literal))
		if err != nil || !jsonEqual(got, want) {
			t.Errorf("%s: %#v, %v; want %#v", literal, got, err, want)
		}
	}
}

func jsonEqual(a interface{}, b interface{}) bool {
	switch av := a.(type) {
	case []interface{}:
		var bv, ok = b.([]interface{})
		if !ok || len(av) != len(bv) {
			return false
		}
		for i := range av {
			if !jsonEqual(av[i], bv[i]) {
				return false
			}
		}
		return true
	case map[string]interface{}:
		var bv, ok = b.(map[string]interface{})
		if !ok || len(av) != len(bv) {
			return false
		}
		for k := range av {
			if !jsonEqual(av[k], bv[k]) {
				return false
			}
		}
		return true
	}
	return a == b
}

// The switchback helpers require exactly one JSON object.
func TestSwitchbackHelpersRejectTrailingJSON(t *testing.T) {
	var router = newRouter()
	var body = `{"clusterUnit":"market_1","periodIndex":"0","seed":"0","armCount":2}`
	for _, path := range []string{"/switchback/assign", "/switchback/permutation"} {
		var valid = body
		if path == "/switchback/permutation" {
			valid = strings.Replace(body, "periodIndex", "cycleIndex", 1)
		}
		if code, _ := post(t, router, path, valid+"  \n"); code != http.StatusOK {
			t.Errorf("%s: %d", path, code)
		}
		if code, _ := post(t, router, path, valid+"{}"); code != http.StatusBadRequest {
			t.Errorf("%s with trailing JSON: %d", path, code)
		}
	}
}
