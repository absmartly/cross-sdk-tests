package com.absmartly.wrapper;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;

/**
 * Calls core-api's public com.absmartly.sdk.internal.SwitchbackAssigner. It is resolved by name
 * rather than imported because CI builds this wrapper against java-sdk releases that predate
 * switchback; there it is simply absent and {@link #isAvailable()} returns false.
 */
final class SwitchbackAssignerAccess {
    private SwitchbackAssignerAccess() {}

    private static final String CLASS_NAME = "com.absmartly.sdk.internal.SwitchbackAssigner";

    private static final Method ASSIGN;
    private static final Method BASE_HASH;
    private static final Method PERMUTATION;

    static {
        Method assign = null;
        Method baseHash = null;
        Method permutation = null;
        try {
            Class<?> type = Class.forName(CLASS_NAME);
            assign = type.getMethod("assign", CharSequence.class, long.class, long.class, int.class);
            baseHash = type.getMethod("baseHash", CharSequence.class, long.class, long.class);
            permutation = type.getMethod("fisherYatesPermutation", int.class, int.class);
        } catch (Throwable t) {
            assign = null;
            baseHash = null;
            permutation = null;
        }
        ASSIGN = assign;
        BASE_HASH = baseHash;
        PERMUTATION = permutation;
    }

    static boolean isAvailable() {
        return ASSIGN != null;
    }

    static int assign(String clusterUnit, long periodIndex, long seed, int armCount) {
        return (Integer) invoke(ASSIGN, clusterUnit, periodIndex, seed, armCount);
    }

    static int[] permutation(String clusterUnit, long cycleIndex, long seed, int armCount) {
        int baseHash = (Integer) invoke(BASE_HASH, clusterUnit, cycleIndex, seed);
        return (int[]) invoke(PERMUTATION, armCount, baseHash);
    }

    private static Object invoke(Method method, Object... args) {
        if (method == null) {
            throw new UnsupportedOperationException("linked core-api has no " + CLASS_NAME);
        }
        try {
            return method.invoke(null, args);
        } catch (InvocationTargetException e) {
            Throwable cause = e.getCause();
            if (cause instanceof RuntimeException) {
                throw (RuntimeException) cause;
            }
            throw new IllegalStateException(cause);
        } catch (IllegalAccessException e) {
            throw new IllegalStateException(e);
        }
    }
}
