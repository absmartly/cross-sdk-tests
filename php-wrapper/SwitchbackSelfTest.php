<?php

declare(strict_types=1);

// Behavioral self-test for the `switchback` wrapper capability. Mirrors java-wrapper/SwitchbackSelfTest.java: a sample
// of the platform golden vectors through the SDK's own assigner, then switchback serving through a real Context on an
// R33 payload decoded from JSON the way the SDK's collector fetch decodes it (json_decode objects, so the cluster map's
// key order is the payload's), and finally the SDK's real publish path. Every check compares the treatment and the
// complete exposure record (every field except exposedAt). A php-sdk without switchbacks serves a sticky unit
// assignment and fails, so the capability reports false. Any throwable fails closed; the result is cached per process.

use ABSmartly\SDK\Client\Client;
use ABSmartly\SDK\Client\ClientConfig;
use ABSmartly\SDK\Config;
use ABSmartly\SDK\Context\Context;
use ABSmartly\SDK\Context\ContextConfig;
use ABSmartly\SDK\Context\ContextData;
use ABSmartly\SDK\Context\ContextDataProvider;
use ABSmartly\SDK\Context\ContextEventHandler;
use ABSmartly\SDK\Context\ContextEventLogger;
use ABSmartly\SDK\Context\ContextEventLoggerEvent;
use ABSmartly\SDK\PublishEvent;
use ABSmartly\SDK\SDK;

final class SwitchbackProbeLogger implements ContextEventLogger
{
    /** @var object[] */
    public array $exposures = [];

    public function handleEvent(Context $context, ContextEventLoggerEvent $event): void
    {
        if ($event->getEvent() === ContextEventLoggerEvent::Exposure) {
            $this->exposures[] = $event->getData();
        }
    }
}

final class SwitchbackProbeProvider extends ContextDataProvider
{
    public function __construct()
    {
    }

    public function getContextData(): ContextData
    {
        return new ContextData();
    }
}

final class SwitchbackProbeHandler extends ContextEventHandler
{
    /** @var string[] The bytes the SDK's own client would PUT. */
    public array $published = [];
    private Client $client;

    public function __construct(Client $client)
    {
        $this->client = $client;
    }

    public function publish(PublishEvent $event): void
    {
        $this->published[] = $this->client->encode($event);
    }
}

final class SwitchbackSelfTest
{
    private const PREFIX = '[switchback probe]';
    private const ASSIGNER = 'ABSmartly\\SDK\\Switchback\\SwitchbackAssigner';

    // Grid constants shared with generate_switchback_scenarios.py: with a 400,000 h period "now" stays in period 0
    // until 2069.
    private const PERIOD_MS = 400000 * 3600000;
    private const ANCHOR_PERIOD_0 = 1704067200000;

    // hashUnit("serving-9"): base64url-no-pad MD5, as published in PublishEvent.units.
    private const SERVING_UNIT_HASH = 'r5uxZRoi-JuM5yYJa0kmSQ';

    // [clusterUnit, periodIndex, seed, armCount, expectedVariant] from the platform golden vectors, including
    // Long.MIN_VALUE/MAX_VALUE periods and seeds.
    private const ASSIGN_CASES = [
        ['market_1', '0', '0', 2, 0],
        ['market_1', '-9223372036854775808', '1', 2, 1],
        ['market_1', '9223372036854775807', '1', 2, 1],
        ['123456789', '0', '0', 2, 1],
        ['market_1', '10', '0', 3, 2],
        ['123456789', '-2', '-1', 3, 0],
        ['market_1', '5', '1', 10, 0],
        ['market_2', '-11', '-9223372036854775808', 10, 3],
        ['market_2', '9223372036854775807', '-9223372036854775808', 10, 2],
        ['market_1', '1', '9223372036854775807', 2, 1],
    ];

    // [clusterUnit, cycleIndex, seed, expectedPermutation], from the same fixture.
    private const PERMUTATION_CASES = [
        ['market_1', '0', '0', [0, 3, 7, 5, 8, 2, 6, 4, 1, 9]],
        ['market_1', '-9223372036854775808', '0', [1, 0]],
        ['market_1', '9223372036854775807', '1', [0, 1]],
        ['123456789', '0', '0', [2, 0, 1]],
    ];

    private static ?bool $result = null;

    public static function run(): bool
    {
        return self::$result ??= self::runSafely();
    }

    public static function isAvailable(): bool
    {
        return class_exists(self::ASSIGNER);
    }

    public static function assign(string $clusterUnit, int $periodIndex, int $seed, int $armCount): int
    {
        return (self::ASSIGNER)::assign($clusterUnit, $periodIndex, $seed, $armCount);
    }

    /** @return int[] */
    public static function permutation(string $clusterUnit, int $cycleIndex, int $seed, int $armCount): array
    {
        return (self::ASSIGNER)::fisherYatesPermutation($armCount, (self::ASSIGNER)::baseHash($clusterUnit, $cycleIndex, $seed));
    }

    private static function runSafely(): bool
    {
        try {
            if (!self::checkGoldenSample() || !self::checkContextServing()) {
                return false;
            }
            echo self::PREFIX . " behavioral self-test PASSED\n";
            return true;
        } catch (Throwable $e) {
            echo self::PREFIX . " behavioral self-test FAILED with throwable: " . get_class($e) . ': ' . $e->getMessage() . "\n";
            return false;
        }
    }

    private static function fail(string $message): bool
    {
        echo self::PREFIX . " {$message}\n";
        return false;
    }

    private static function checkGoldenSample(): bool
    {
        if (!self::isAvailable()) {
            return self::fail('FAILED: linked php-sdk has no switchback assigner');
        }
        foreach (self::ASSIGN_CASES as [$unit, $period, $seed, $arms, $expected]) {
            $actual = self::assign($unit, (int) $period, (int) $seed, $arms);
            if ($actual !== $expected) {
                return self::fail("golden assign FAILED for {$unit}/{$period}/{$seed}/{$arms}: got {$actual}");
            }
        }
        foreach (self::PERMUTATION_CASES as [$unit, $cycle, $seed, $expected]) {
            $actual = self::permutation($unit, (int) $cycle, (int) $seed, count($expected));
            if ($actual !== $expected) {
                return self::fail("golden permutation FAILED for {$unit}/{$cycle}/{$seed}: got " . json_encode($actual));
            }
        }
        return true;
    }

    private static function base(int $id, string $name, int $seedHi, int $seedLo, int $arms): array
    {
        $variants = [];
        for ($i = 0; $i < $arms; ++$i) {
            $variants[] = ['name' => chr(65 + $i), 'config' => null];
        }
        return [
            'id' => $id, 'name' => $name, 'iteration' => 1, 'unitType' => 'unit_serving', 'seedHi' => $seedHi,
            'seedLo' => $seedLo, 'split' => array_fill(0, $arms, 1.0 / $arms), 'trafficSeedHi' => 1, 'trafficSeedLo' => 2,
            'trafficSplit' => [0, 1], 'fullOnVariant' => 0, 'applications' => [['name' => 'website']],
            'variants' => $variants, 'audienceStrict' => false,
        ];
    }

    private static function switchback(int $id, string $name, int $seedHi, int $seedLo, int $arms, ?array $clusters): array
    {
        $experiment = self::base($id, $name, $seedHi, $seedLo, $arms);
        $experiment['randomizationType'] = 'switchback';
        if ($clusters !== null) {
            // A JSON object in declared order, even when PHP would otherwise reorder nothing.
            $experiment['switchbackClusters'] = (object) $clusters;
            $experiment['unitType'] = array_key_first($clusters);
        }
        $experiment['switchbackStartAt'] = self::ANCHOR_PERIOD_0;
        $experiment['switchbackPeriodMs'] = self::PERIOD_MS;
        $experiment['switchbackDesignType'] = 'balanced_block';
        $experiment['switchbackEstimand'] = 'global_average_treatment_effect';
        return $experiment;
    }

    /** The unit baseline: seeds whose 3-arm unit draw for serving-9 is 1. */
    private static function unit(int $id, string $name): array
    {
        return self::base($id, $name, -559038737, 1234567890, 3);
    }

    /**
     * The probe payload, as the Collector serves it (R33), encoded and decoded as JSON. Every expected variant is a
     * platform golden vector, chosen (java-wrapper's Python reference) to differ from every wrong model for both context
     * uids: the hashed key, the uid as key, the hashed uid as key, a Phase 1E bucket and the sticky unit assignment.
     *   assignSwitchback("market_1", 0, 42, 10) = 3; assignSwitchback("123456789", 0, 42, 10) = 6 (the float renderings
     *   "123456789.0", "1.23456789E8" and "1.23456789e+08" draw 4, 7 and 0); assignSwitchback("market_1", 0, 1, 3) = 1.
     */
    public static function probePayload(): string
    {
        $filtered = self::switchback(525, 'sb_value_filtered', 0, 1, 10, ['unit_serving' => 'city']);
        $filtered['switchbackClusterValues'] = ['market_2'];
        $admitAll = self::switchback(528, 'sb_allow_empty', 0, 42, 10, ['unit_serving' => 'city']);
        $admitAll['switchbackClusterValues'] = [];
        // Only the abandoned Phase 1E fields: no cluster map, so the legacy ineligible control.
        $legacy = self::switchback(526, 'sb_phase1e_only', 0, 1, 10, null);
        $legacy['switchbackClusterUnitType'] = 'unit_serving';
        $legacy['switchbackClusterCount'] = 10;
        $legacy['switchbackClusterAttribute'] = 'city';
        $withFields = self::unit(527, 'sb_unit_with_fields');
        $withFields['randomizationType'] = 'unit';
        $withFields['switchbackClusters'] = (object) ['unit_serving' => 'city'];
        $withFields['switchbackClusterValues'] = ['market_1'];
        $withFields['switchbackStartAt'] = self::ANCHOR_PERIOD_0;
        $withFields['switchbackPeriodMs'] = self::PERIOD_MS;
        $withFields['switchbackDesignType'] = 'balanced_block';

        return json_encode(['experiments' => [
            self::switchback(521, 'sb_agree', 0, 42, 10, ['unit_serving' => 'city', 'unit_user' => 'profile/city']),
            self::switchback(522, 'sb_declared_order', 0, 42, 10, ['unit_user' => 'profile/city', 'unit_serving' => 'city']),
            self::switchback(523, 'sb_disagree', 0, 42, 10, ['unit_serving' => 'city', 'unit_user' => 'profile/zone']),
            self::switchback(524, 'sb_whole_number', 0, 42, 10, ['unit_serving' => 'store_number']),
            self::switchback(529, 'sb_integral_float', 0, 42, 10, ['unit_serving' => 'store_float']),
            $admitAll,
            $filtered,
            $legacy,
            self::switchback(513, 'sb_payload', 0, 1, 3, ['unit_serving' => 'city']),
            self::unit(514, 'sb_unit_baseline'),
            $withFields,
        ]], JSON_THROW_ON_ERROR | JSON_PRESERVE_ZERO_FRACTION);
    }

    /** [id, name, unit, variant, assigned, eligible, overridden, fullOn, custom, audienceMismatch, ruleOverride] */
    private static function record(object $exposure): array
    {
        return [$exposure->id, $exposure->name, $exposure->unit, $exposure->variant, $exposure->assigned, $exposure->eligible,
            $exposure->overridden, $exposure->fullOn, $exposure->custom, $exposure->audienceMismatch, $exposure->ruleOverride ?? false];
    }

    private static function served(int $id, string $name, string $unit, int $variant): array
    {
        return [$id, $name, $unit, $variant, true, true, false, false, false, false, false];
    }

    private static function expect(Context $context, SwitchbackProbeLogger $logger, string $check, string $experiment, int $treatment, array ...$exposures): bool
    {
        $before = count($logger->exposures);
        $actual = $context->getTreatment($experiment);
        $emitted = array_map([self::class, 'record'], array_slice($logger->exposures, $before));
        foreach (array_slice($logger->exposures, $before) as $exposure) {
            if (!($exposure->exposedAt > 0)) {
                return self::fail("check {$check} FAILED: exposedAt is " . json_encode($exposure->exposedAt));
            }
        }
        if ($actual !== $treatment || $emitted !== $exposures) {
            return self::fail("check {$check} FAILED: expected treatment={$treatment} exposures=" . json_encode($exposures)
                . ", got treatment={$actual} exposures=" . json_encode($emitted));
        }
        return true;
    }

    private static function checkContextServing(): bool
    {
        if (!property_exists('ABSmartly\\SDK\\Experiment', 'switchbackClusters')
            || !property_exists('ABSmartly\\SDK\\Experiment', 'switchbackClusterValues')
            || property_exists('ABSmartly\\SDK\\Experiment', 'switchbackClusterCount')) {
            return self::fail('FAILED: linked php-sdk has no R33 switchback wire model');
        }

        // Decoded like the SDK's collector fetch (Client::decode: json_decode to objects).
        $client = new Client(new ClientConfig('http://127.0.0.1:1/v1', 'probe', 'website', 'test'));
        $decoded = $client->decode(self::probePayload());
        foreach ($decoded->experiments as $experiment) {
            if ($experiment->name === 'sb_declared_order'
                && array_keys(get_object_vars($experiment->switchbackClusters)) !== ['unit_user', 'unit_serving']) {
                return self::fail('FAILED: switchbackClusters lost its declared order');
            }
        }

        $config = new Config($client);
        $config->setContextDataProvider(new SwitchbackProbeProvider());
        $handler = new SwitchbackProbeHandler($client);
        $config->setContextEventHandler($handler);
        $logger = new SwitchbackProbeLogger();
        $contextConfig = new ContextConfig();
        // unit_user is set first, so declared order, not context order, must pick the serving unit.
        $contextConfig->setUnits(['unit_user' => 'probe-user', 'unit_serving' => 'serving-9']);
        $contextConfig->setEventLogger($logger);
        $context = (new SDK($config))->createContextWithData($contextConfig, new ContextData($decoded->experiments));
        if (!$context->isReady() || $context->isFailed()) {
            return self::fail('FAILED: context not ready');
        }
        $context->setAttribute('city', 'market_1');
        $context->setAttribute('profile', ['city' => 'market_1', 'zone' => 'market_2']);
        $context->setAttribute('store_number', 123456789);
        $context->setAttribute('store_float', 123456789.0);

        $served = self::expect($context, $logger, 'agreement', 'sb_agree', 3, self::served(521, 'sb_agree', 'unit_serving', 3))
            && self::expect($context, $logger, 'agreement repeat', 'sb_agree', 3)
            && self::expect($context, $logger, 'declared order', 'sb_declared_order', 3, self::served(522, 'sb_declared_order', 'unit_user', 3))
            && self::expect($context, $logger, 'disagreement', 'sb_disagree', 0)
            && self::expect($context, $logger, 'whole number', 'sb_whole_number', 6, self::served(524, 'sb_whole_number', 'unit_serving', 6))
            && self::expect($context, $logger, 'a float never keys a cluster', 'sb_integral_float', 0)
            && self::expect($context, $logger, 'value filtered out', 'sb_value_filtered', 0)
            && self::expect($context, $logger, 'empty allow-list admits all', 'sb_allow_empty', 3, self::served(528, 'sb_allow_empty', 'unit_serving', 3))
            && self::expect($context, $logger, 'Phase 1E fields only', 'sb_phase1e_only', 0);
        if (!$served) {
            return false;
        }

        return self::checkPublishPayload($context, $handler);
    }

    /** One publish of the exposures queued since the last; null unless exactly one was sent. */
    private static function publishOne(Context $context, SwitchbackProbeHandler $handler): ?object
    {
        $before = count($handler->published);
        $context->publish();
        return count($handler->published) === $before + 1 ? json_decode($handler->published[$before]) : null;
    }

    /** The publish bytes with what legitimately differs between two single-exposure publishes zeroed. */
    private static function normalized(object $event): string
    {
        $event = json_decode(json_encode($event));
        unset($event->publishedAt);
        $event->exposures[0]->id = 0;
        $event->exposures[0]->name = '';
        $event->exposures[0]->exposedAt = 0;
        return json_encode($event);
    }

    private static function publishedExactly(?object $event, array $expected): bool
    {
        if ($event === null || ($event->hashed ?? false) !== true || count($event->exposures ?? []) !== 1
            || self::record($event->exposures[0]) !== $expected || !is_int($event->exposures[0]->exposedAt)
            || $event->exposures[0]->exposedAt <= 0) {
            return false;
        }
        foreach ($event->units ?? [] as $unit) {
            if ($unit->type === 'unit_serving' && $unit->uid === self::SERVING_UNIT_HASH) {
                return true;
            }
        }
        return false;
    }

    /**
     * Through the SDK's real publish path: a unit baseline and then a switchback on the same unit, each published on
     * its own, carry the same fields at every depth; and a unit experiment carrying every R33 field publishes the same
     * bytes as the plain unit baseline.
     */
    private static function checkPublishPayload(Context $context, SwitchbackProbeHandler $handler): bool
    {
        $context->publish();

        $context->getTreatment('sb_unit_baseline');
        $baseline = self::publishOne($context, $handler);
        $context->getTreatment('sb_payload');
        $switchback = self::publishOne($context, $handler);
        if (!self::publishedExactly($baseline, self::served(514, 'sb_unit_baseline', 'unit_serving', 1))
            || !self::publishedExactly($switchback, self::served(513, 'sb_payload', 'unit_serving', 1))) {
            return self::fail('check publish payload FAILED: baseline ' . json_encode($baseline) . ', switchback ' . json_encode($switchback));
        }
        if (self::shape($baseline) !== self::shape($switchback)) {
            return self::fail('check publish shape FAILED: ' . json_encode(self::shape($switchback)) . ' differs from ' . json_encode(self::shape($baseline)));
        }

        if ($context->getTreatment('sb_unit_with_fields') !== 1) {
            return self::fail('check unit inertness FAILED: sb_unit_with_fields did not serve 1');
        }
        $inert = self::publishOne($context, $handler);
        if (!self::publishedExactly($inert, self::served(527, 'sb_unit_with_fields', 'unit_serving', 1))
            || self::normalized($inert) !== self::normalized($baseline)) {
            return self::fail('check unit inertness FAILED: publish ' . json_encode($inert) . ' differs from ' . json_encode($baseline));
        }
        return true;
    }

    /** Every key path of a decoded JSON value, with the type at each leaf. */
    private static function shape($value, string $path = '$'): array
    {
        if (is_object($value) || is_array($value)) {
            $shape = [$path => is_object($value) ? 'object' : 'array'];
            foreach ($value as $key => $child) {
                $shape += self::shape($child, "{$path}.{$key}");
            }
            return $shape;
        }
        return [$path => gettype($value)];
    }
}
