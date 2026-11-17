package com.absmartly.sdk;

/**
 * A {@link Client} that reports no environment, for the cross-SDK "no environment
 * configured" conformance case (the 2026-11 system-attributes round, §4 test 6).
 *
 * <p>{@code Client}'s constructor rejects a null or empty environment, exactly as
 * the SDK intends for a real client, so it cannot itself express "application set,
 * environment absent". This adapter constructs the real client with a placeholder
 * environment (satisfying that validation) and then reports the environment as
 * absent, so {@code Context} leaves {@code environment} out of the audience/rule
 * evaluation map — the same effect the SDK's own unit tests obtain by stubbing
 * {@code getEnvironment()}. It lives in {@code com.absmartly.sdk} only because the
 * {@code Client} constructor is package-private; it uses no reflection and touches
 * no private state.
 */
public final class NoEnvironmentClient extends Client {
	public NoEnvironmentClient(final ClientConfig config, final HTTPClient httpClient) {
		super(config, httpClient);
	}

	@Override
	public String getEnvironment() {
		return null;
	}
}
