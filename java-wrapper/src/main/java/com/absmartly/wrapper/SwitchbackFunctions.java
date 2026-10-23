package com.absmartly.wrapper;

/**
 * The switchback pure functions the wrapper serves and the self-test samples. {@link #LINKED}
 * calls the linked core-api; tests substitute their own implementation.
 */
interface SwitchbackFunctions {
    boolean isAvailable();

    int assign(String clusterUnit, long periodIndex, long seed, int armCount);

    int[] permutation(String clusterUnit, long cycleIndex, long seed, int armCount);

    // A core-api with the assigner but without buckets predates the bucket model.
    default boolean isBucketAvailable() {
        return false;
    }

    default int bucketOf(String key, long seed, int count) {
        throw new UnsupportedOperationException("no switchback bucket function");
    }

    SwitchbackFunctions LINKED = new SwitchbackFunctions() {
        @Override
        public boolean isAvailable() {
            return SwitchbackAssignerAccess.isAvailable();
        }

        @Override
        public int assign(String clusterUnit, long periodIndex, long seed, int armCount) {
            return SwitchbackAssignerAccess.assign(clusterUnit, periodIndex, seed, armCount);
        }

        @Override
        public int[] permutation(String clusterUnit, long cycleIndex, long seed, int armCount) {
            return SwitchbackAssignerAccess.permutation(clusterUnit, cycleIndex, seed, armCount);
        }

        @Override
        public boolean isBucketAvailable() {
            return SwitchbackAssignerAccess.isBucketAvailable();
        }

        @Override
        public int bucketOf(String key, long seed, int count) {
            return SwitchbackAssignerAccess.bucketOf(key, seed, count);
        }
    };
}
