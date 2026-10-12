package com.absmartly.wrapper;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

class SwitchbackEndpointsTest {

    // Always-wrong functions: variant 9 and a reversed permutation, so every golden sample fails.
    private static final class WrongFunctions implements SwitchbackFunctions {
        final AtomicInteger calls = new AtomicInteger();

        @Override
        public boolean isAvailable() {
            return true;
        }

        @Override
        public int assign(String clusterUnit, long periodIndex, long seed, int armCount) {
            calls.incrementAndGet();
            return 9;
        }

        @Override
        public int[] permutation(String clusterUnit, long cycleIndex, long seed, int armCount) {
            calls.incrementAndGet();
            int[] result = new int[armCount];
            for (int i = 0; i < armCount; i++) {
                result[i] = armCount - 1 - i;
            }
            return result;
        }
    }

    // Fixed values; parameter validation under test happens in the controller before any call.
    private static final class FixedFunctions implements SwitchbackFunctions {
        @Override
        public boolean isAvailable() {
            return true;
        }

        @Override
        public int assign(String clusterUnit, long periodIndex, long seed, int armCount) {
            return 1;
        }

        @Override
        public int[] permutation(String clusterUnit, long cycleIndex, long seed, int armCount) {
            return new int[] {1, 0};
        }
    }

    private static final SwitchbackFunctions ABSENT = new SwitchbackFunctions() {
        @Override
        public boolean isAvailable() {
            return false;
        }

        @Override
        public int assign(String clusterUnit, long periodIndex, long seed, int armCount) {
            throw new AssertionError("absent assigner called");
        }

        @Override
        public int[] permutation(String clusterUnit, long cycleIndex, long seed, int armCount) {
            throw new AssertionError("absent assigner called");
        }
    };

    private static Map<String, Object> assignRequest() {
        Map<String, Object> request = new HashMap<>();
        request.put("clusterUnit", "market_1");
        request.put("periodIndex", "-9223372036854775808");
        request.put("seed", "9223372036854775807");
        request.put("armCount", 2);
        return request;
    }

    private static Map<String, Object> permutationRequest() {
        Map<String, Object> request = assignRequest();
        request.put("cycleIndex", request.remove("periodIndex"));
        return request;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> body(ResponseEntity<?> response) {
        return (Map<String, Object>) response.getBody();
    }

    static List<Object[]> badInputs() {
        return Arrays.asList(
            new Object[] {"clusterUnit", null},
            new Object[] {"clusterUnit", 42},
            new Object[] {"seed", null},
            new Object[] {"seed", 1L},
            new Object[] {"seed", 1},
            new Object[] {"seed", "1.5"},
            new Object[] {"seed", "0x10"},
            new Object[] {"seed", ""},
            new Object[] {"seed", "9223372036854775808"},
            new Object[] {"seed", "-9223372036854775809"},
            new Object[] {"armCount", null},
            new Object[] {"armCount", "2"},
            new Object[] {"armCount", 2.0});
    }

    // The arm-count bound is enforced by the SDK's own assigner, so these run against it.
    @ParameterizedTest
    @ValueSource(ints = {1, 0, -3})
    void linkedAssignerRejectsTooFewArmsWith400(int armCount) {
        assumeTrue(SwitchbackFunctions.LINKED.isAvailable(), "linked core-api has no SwitchbackAssigner");
        WrapperController controller = new WrapperController();

        Map<String, Object> assign = assignRequest();
        assign.put("armCount", armCount);
        assertEquals(HttpStatus.BAD_REQUEST, controller.switchbackAssign(assign).getStatusCode());

        Map<String, Object> permutation = permutationRequest();
        permutation.put("armCount", armCount);
        assertEquals(HttpStatus.BAD_REQUEST, controller.switchbackPermutation(permutation).getStatusCode());
    }

    @ParameterizedTest
    @MethodSource("badInputs")
    void assignRejectsBadInputWith400(String field, Object value) {
        Map<String, Object> request = assignRequest();
        request.put(field, value);
        ResponseEntity<?> response = new WrapperController(new FixedFunctions()).switchbackAssign(request);
        assertEquals(HttpStatus.BAD_REQUEST, response.getStatusCode());
        assertTrue(body(response).get("error") instanceof String);
    }

    @ParameterizedTest
    @MethodSource("badInputs")
    void permutationRejectsBadInputWith400(String field, Object value) {
        Map<String, Object> request = permutationRequest();
        request.put(field, value);
        ResponseEntity<?> response = new WrapperController(new FixedFunctions()).switchbackPermutation(request);
        assertEquals(HttpStatus.BAD_REQUEST, response.getStatusCode());
    }

    @Test
    void numericPeriodIndexIsRejectedWith400() {
        Map<String, Object> request = assignRequest();
        request.put("periodIndex", 5);
        assertEquals(HttpStatus.BAD_REQUEST,
            new WrapperController(new FixedFunctions()).switchbackAssign(request).getStatusCode());

        Map<String, Object> permutation = permutationRequest();
        permutation.put("cycleIndex", 5);
        assertEquals(HttpStatus.BAD_REQUEST,
            new WrapperController(new FixedFunctions()).switchbackPermutation(permutation).getStatusCode());
    }

    @Test
    void absentAssignerAnswers501() {
        WrapperController controller = new WrapperController(ABSENT);
        assertEquals(HttpStatus.NOT_IMPLEMENTED, controller.switchbackAssign(assignRequest()).getStatusCode());
        assertEquals(HttpStatus.NOT_IMPLEMENTED,
            controller.switchbackPermutation(permutationRequest()).getStatusCode());
    }

    @Test
    void failingProbeStillServesTheAssignersActualValues() {
        WrongFunctions wrong = new WrongFunctions();
        assertFalse(SwitchbackSelfTest.checkGoldenSample(wrong));
        assertTrue(wrong.calls.get() > 0);

        WrapperController controller = new WrapperController(wrong);
        ResponseEntity<?> assign = controller.switchbackAssign(assignRequest());
        assertEquals(HttpStatus.OK, assign.getStatusCode());
        assertEquals(9, body(assign).get("result"));

        Map<String, Object> request = permutationRequest();
        request.put("armCount", 3);
        ResponseEntity<?> permutation = controller.switchbackPermutation(request);
        assertEquals(HttpStatus.OK, permutation.getStatusCode());
        assertEquals(Arrays.asList(2, 1, 0), body(permutation).get("result"));
    }
}
