package main

import (
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/absmartly/go-sdk/sdk"
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

func contextID(t *testing.T, created map[string]interface{}) string {
	t.Helper()
	var result, ok = created["result"].(map[string]interface{})
	if !ok {
		t.Fatalf("no context: %v", created)
	}
	return result["contextId"].(string)
}

func experimentJSON(t *testing.T, fields map[string]interface{}) string {
	t.Helper()
	var experiment = map[string]interface{}{"id": 1, "name": "exp", "unitType": "store_id", "iteration": 1,
		"seedHi": 1234567, "seedLo": 1, "split": []float64{0.34, 0.33, 0.33}, "trafficSeedHi": 1, "trafficSeedLo": 2,
		"trafficSplit": []float64{0, 1}, "applications": []interface{}{map[string]string{"name": "website"}},
		"variants": []interface{}{map[string]string{"name": "A"}, map[string]string{"name": "B"}, map[string]string{"name": "C"}}}
	for key, value := range fields {
		experiment[key] = value
	}
	var encoded, err = json.Marshal(experiment)
	if err != nil {
		t.Fatal(err)
	}
	return `{"experiments":[` + string(encoded) + `]}`
}

// writePaths runs one treatment with the attribute set at creation and one
// with it set afterwards, and returns the variant and queued exposures of each.
func writePaths(t *testing.T, router http.Handler, data string, name string, literal string) [2][2]interface{} {
	t.Helper()
	var results [2][2]interface{}
	var _, created = post(t, router, "/context", `{"data":`+data+`,"units":{"store_id":"s1"},"attributes":{"`+name+`":`+literal+`}}`)
	var id = contextID(t, created)
	var _, treatment = post(t, router, "/context/"+id+"/treatment", `{"experimentName":"exp"}`)
	results[0] = [2]interface{}{treatment["result"], len(treatment["events"].([]interface{}))}

	_, created = post(t, router, "/context", `{"data":`+data+`,"units":{"store_id":"s1"}}`)
	id = contextID(t, created)
	if code, _ := post(t, router, "/context/"+id+"/attribute", `{"name":"`+name+`","value":`+literal+`}`); code != http.StatusOK {
		t.Fatalf("set %s: %d", literal, code)
	}
	_, treatment = post(t, router, "/context/"+id+"/treatment", `{"experimentName":"exp"}`)
	results[1] = [2]interface{}{treatment["result"], len(treatment["events"].([]interface{}))}
	return results
}

// A strict numeric audience balance > 99 compares every literal as a number
// through both write paths: wide integers (beyond int64) and exponent
// literals included.
func TestAttributesCompareNumericallyThroughBothWritePaths(t *testing.T) {
	var router = newRouter()
	var audience, _ = json.Marshal(map[string]interface{}{"filter": []interface{}{
		map[string]interface{}{"gt": []interface{}{map[string]interface{}{"var": "balance"}, map[string]interface{}{"value": 99}}}}})
	var data = experimentJSON(t, map[string]interface{}{"split": []float64{0, 1, 0}, "audienceStrict": true, "audience": string(audience)})
	for literal, want := range map[string]float64{"9223372036854775808": 1, "9.223372036854776e18": 1,
		"123456789012345678901234567890": 1, "100": 1, "100.0": 1, "99": 0, "99.0": 0} {
		for path, result := range writePaths(t, router, data, "balance", literal) {
			if result[0] != want {
				t.Errorf("%s via path %d: variant %v, want %v", literal, path, result[0], want)
			}
		}
	}
}

// A whole-number attribute keys a switchback cluster by its digits, through
// both write paths, beyond int64 too; a float (3.0) gives no key.
func TestIntegerAttributesKeySwitchbackClustersThroughBothWritePaths(t *testing.T) {
	var router = newRouter()
	var switchback = func(allowed string) string {
		return experimentJSON(t, map[string]interface{}{"randomizationType": "switchback",
			"switchbackClusters": map[string]string{"store_id": "store_number"}, "switchbackClusterValues": []string{allowed},
			"switchbackStartAt": 1704067200000, "switchbackPeriodMs": 1440000000000})
	}
	for _, c := range []struct {
		literal  string
		allowed  string
		eligible bool
	}{
		{"4000000002", "4000000002", true},
		{"123456789012345678901234567890", "123456789012345678901234567890", true},
		{"3", "3", true},
		{"3.0", "3", false},
	} {
		var direct, _ = sbVariant(c.allowed)
		for path, result := range writePaths(t, router, switchback(c.allowed), "store_number", c.literal) {
			var want = [2]interface{}{float64(0), 0}
			if c.eligible {
				want = [2]interface{}{float64(direct), 1}
			}
			if result != want {
				t.Errorf("%s via path %d: variant and exposures %v, want %v", c.literal, path, result, want)
			}
		}
	}
}

// sbVariant is the period-0 draw of the test switchback for a cluster key.
func sbVariant(key string) (int, error) {
	return switchbackAssignForTest(key)
}

// An attribute that cannot be decoded is rejected before the SDK does any
// work: no context is created and no context fetch reaches the collector.
// A rejected update keeps the previous value readable.
func TestOutOfRangeAttributesAreRejectedBeforeTheContext(t *testing.T) {
	var fetches atomic.Int32
	// translateEndpoint rewrites loopback names to the wrapper's own port,
	// so the test collector listens on another loopback address.
	var listener, err = net.Listen("tcp", "127.0.0.2:0")
	if err != nil {
		t.Skipf("no second loopback address: %v", err)
	}
	var collector = httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fetches.Add(1)
		w.Write([]byte(`{"experiments":[]}`))
	}))
	collector.Listener.Close()
	collector.Listener = listener
	collector.Start()
	defer collector.Close()
	var endpoint = collector.URL
	var router = newRouter()
	contextsMu.Lock()
	var before = len(contexts)
	contextsMu.Unlock()
	if code, _ := post(t, router, "/context", `{"endpoint":"`+endpoint+`","units":{"u":"1"},"attributes":{"bad":[1e309]}}`); code != http.StatusBadRequest {
		t.Fatalf("create: %d", code)
	}
	contextsMu.Lock()
	var after = len(contexts)
	contextsMu.Unlock()
	if fetches.Load() != 0 || after != before {
		t.Fatalf("a rejected request fetched %d times and created %d contexts", fetches.Load(), after-before)
	}

	var _, created = post(t, router, "/context", `{"data":{"experiments":[]},"units":{"u":"1"},"attributes":{"ok":3}}`)
	var id = contextID(t, created)
	if code, _ := post(t, router, "/context/"+id+"/attribute", `{"name":"ok","value":{"a":[1e309]}}`); code != http.StatusBadRequest {
		t.Fatalf("set: %d", code)
	}
	if code, read := post(t, router, "/context/"+id+"/getAttribute", `{"name":"ok"}`); code != http.StatusOK || read["result"] != float64(3) {
		t.Fatalf("read after a rejected set: %d %v", code, read)
	}
}

func TestAttributeValueKeepsTheDigitsThatWereSent(t *testing.T) {
	for literal, want := range map[string]interface{}{"3": json.Number("3"), "3.0": json.Number("3.0"), "1e1": json.Number("1e1"),
		"9223372036854775808": json.Number("9223372036854775808"),
		`[1,2.5,{"a":4}]`:     []interface{}{json.Number("1"), json.Number("2.5"), map[string]interface{}{"a": json.Number("4")}}} {
		var got, err = attributeValue(json.RawMessage(literal))
		if err != nil || !reflect.DeepEqual(got, want) {
			t.Errorf("%s: %#v, %v; want %#v", literal, got, err, want)
		}
	}
}

// Each context keeps the digits it was sent: an accepted or rejected write
// of a value that rounds to the same float64 in another context changes
// neither its read-back nor its switchback treatment.
func TestWideIntegersStayWithTheirContext(t *testing.T) {
	var router = newRouter()
	var data = experimentJSON(t, map[string]interface{}{"randomizationType": "switchback",
		"switchbackClusters": map[string]string{"store_id": "store_number"}, "switchbackClusterValues": []string{"9223372036854775808"},
		"switchbackStartAt": 1704067200000, "switchbackPeriodMs": 1440000000000})
	var _, created = post(t, router, "/context", `{"data":`+data+`,"units":{"store_id":"s1"},"attributes":{"store_number":9223372036854775808}}`)
	var a = contextID(t, created)
	_, created = post(t, router, "/context", `{"data":{"experiments":[]},"units":{"u":"1"}}`)
	var b = contextID(t, created)
	post(t, router, "/context/"+b+"/attribute", `{"name":"x","value":9223372036854775809}`)
	post(t, router, "/context/"+b+"/attribute", `{"name":"y","value":[9223372036854775809,1e309]}`)
	if _, read := post(t, router, "/context/"+a+"/getAttribute", `{"name":"store_number"}`); read["result"] != float64(9223372036854775808) {
		t.Fatalf("read-back %v", read["result"])
	}
	var recorder = httptest.NewRecorder()
	router.ServeHTTP(recorder, httptest.NewRequest(http.MethodPost, "/context/"+a+"/getAttribute", strings.NewReader(`{"name":"store_number"}`)))
	if !strings.Contains(recorder.Body.String(), `"result":9223372036854775808`) {
		t.Fatalf("read-back body %s", recorder.Body.String())
	}
	var direct, _ = sbVariant("9223372036854775808")
	if _, treatment := post(t, router, "/context/"+a+"/treatment", `{"experimentName":"exp"}`); treatment["result"] != float64(direct) ||
		len(treatment["events"].([]interface{})) != 1 {
		t.Fatalf("treatment %v, want %d with one exposure", treatment, direct)
	}
}

// A numeric semver operand converts like the JS SDK's number through both
// write paths: 9007199254740993 rounds to 9007199254740992.
func TestNumericSemverAudienceThroughBothWritePaths(t *testing.T) {
	var router = newRouter()
	var audience, _ = json.Marshal(map[string]interface{}{"filter": []interface{}{
		map[string]interface{}{"semver_eq": []interface{}{map[string]interface{}{"var": "app_version"}, map[string]interface{}{"value": "9007199254740992"}}}}})
	var data = experimentJSON(t, map[string]interface{}{"split": []float64{0, 1, 0}, "audienceStrict": true, "audience": string(audience)})
	for _, literal := range []string{"9007199254740993", "9007199254740992", "9007199254740992.0"} {
		for path, result := range writePaths(t, router, data, "app_version", literal) {
			if result[0] != float64(1) {
				t.Errorf("%s via path %d: variant %v, want 1", literal, path, result[0])
			}
		}
	}
}

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

// switchbackAssignForTest is the period-0, 3-arm draw under the test
// switchback's seed (1234567, 1).
func switchbackAssignForTest(key string) (int, error) {
	return sdk.SwitchbackAssign(key, 0, 1234567<<32|1, 3)
}
