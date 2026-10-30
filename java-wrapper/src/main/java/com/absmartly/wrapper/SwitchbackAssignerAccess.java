package com.absmartly.wrapper;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;

/**
 * Calls core-api's public com.absmartly.sdk.internal.SwitchbackAssigner and SwitchbackBuckets.
 * They are resolved by name rather than imported because CI builds this wrapper against java-sdk
 * releases that predate switchback; there they are simply absent and the is*Available methods
 * return false.
 */
final class SwitchbackAssignerAccess {
    private SwitchbackAssignerAccess() {}

    private static final String CLASS_NAME = "com.absmartly.sdk.internal.SwitchbackAssigner";
    private static final String BUCKETS_CLASS_NAME = "com.absmartly.sdk.internal.SwitchbackBuckets";

    private static final Method ASSIGN;
    private static final Method BASE_HASH;
    private static final Method PERMUTATION;
    private static final Method BUCKET_OF;

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

        Method bucketOf;
        try {
            bucketOf = Class.forName(BUCKETS_CLASS_NAME)
                .getMethod("bucketOf", int.class, int.class, String.class, int.class);
        } catch (Throwable t) {
            bucketOf = null;
        }
        BUCKET_OF = bucketOf;
    }

    static boolean isAvailable() {
        return ASSIGN != null;
    }

    static boolean isBucketAvailable() {
        return BUCKET_OF != null;
    }

    static int assign(String clusterUnit, long periodIndex, long seed, int armCount) {
        return (Integer) invoke(ASSIGN, clusterUnit, periodIndex, seed, armCount);
    }

    static int[] permutation(String clusterUnit, long cycleIndex, long seed, int armCount) {
        int baseHash = (Integer) invoke(BASE_HASH, clusterUnit, cycleIndex, seed);
        return (int[]) invoke(PERMUTATION, armCount, baseHash);
    }

    // The SDK takes the seed's signed 32-bit halves, as ContextData carries them.
    static int bucketOf(String key, long seed, int count) {
        return (Integer) invoke(BUCKET_OF, (int) (seed >> 32), (int) seed, key, count);
    }

    private static Object invoke(Method method, Object... args) {
        if (method == null) {
            throw new UnsupportedOperationException("linked core-api has no switchback function");
        }
        try {
            return method.invoke(null, args);
        } catch (InvocationTargetException e) {
            if (e.getCause() instanceof RuntimeException cause) {
                throw cause;
            }
            throw new IllegalStateException(e.getCause());
        } catch (IllegalAccessException e) {
            throw new IllegalStateException(e);
        }
    }
}
