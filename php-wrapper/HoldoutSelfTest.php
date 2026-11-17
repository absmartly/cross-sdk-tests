<?php

declare(strict_types=1);

// Behavioral self-test for the `holdouts` / `holdout_arms` wrapper capabilities. Mirrors
// javascript-wrapper/holdoutSelfTest.js: build a real ContextData from fixtures ported from the
// scenario battery (203-208, 221, 227, 305 and 214-216), create a real Context against the built php-sdk,
// call getTreatment() and require BOTH the returned variant and the exact ordered id/name/variant
// sequence of exposure events that single call logs. A php-sdk without holdouts ignores the
// fields and fails these checks, so the capability reports false. Any throwable fails closed.
// Each battery runs once per wrapper process and the result is cached.

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

final class HoldoutProbeLogger implements ContextEventLogger
{
    public array $exposures = [];

    public function handleEvent(Context $context, ContextEventLoggerEvent $event): void
    {
        if ($event->getEvent() === ContextEventLoggerEvent::Exposure) {
            $data = $event->getData();
            $this->exposures[] = ['id' => $data->id, 'name' => $data->name, 'variant' => $data->variant];
        }
    }
}

final class HoldoutProbeNoopProvider extends ContextDataProvider
{
    public function __construct()
    {
    }

    public function getContextData(): ContextData
    {
        return new ContextData();
    }
}

final class HoldoutProbeNoopHandler extends ContextEventHandler
{
    public function __construct()
    {
    }

    public function publish(PublishEvent $event): void
    {
    }
}

final class HoldoutSelfTest
{
    private static ?bool $holdouts = null;
    private static ?bool $holdoutArms = null;

    public static function runHoldouts(): bool
    {
        return self::$holdouts ??= self::runSafely('[holdouts probe]', fn() => self::holdoutsBattery());
    }

    // Gated on runHoldouts(): a build failing the 2-arm battery never reports holdout_arms.
    public static function runHoldoutArms(): bool
    {
        return self::$holdoutArms ??= self::runHoldouts()
            && self::runSafely('[holdout_arms probe]', fn() => self::holdoutArmsBattery());
    }

    private static function runSafely(string $prefix, callable $battery): bool
    {
        try {
            return $battery();
        } catch (Throwable $e) {
            echo "{$prefix} behavioral self-test FAILED with throwable: {$e->getMessage()}\n";
            return false;
        }
    }

    private static function experiment(int $id, string $name, string $unitType, int $fullOnVariant, array $variantNames, ?array $holdoutIds, array $split = [0.5, 0.5]): object
    {
        return (object)[
            'id' => $id, 'name' => $name, 'iteration' => 1, 'unitType' => $unitType, 'seedHi' => 100, 'seedLo' => 200,
            'split' => $split, 'trafficSeedHi' => 1, 'trafficSeedLo' => 2, 'trafficSplit' => [0, 1],
            'fullOnVariant' => $fullOnVariant, 'applications' => [(object)['name' => 'website']],
            'variants' => array_map(fn($n) => (object)['name' => $n, 'config' => null], $variantNames),
            'audience' => null, 'audienceStrict' => false, 'customFieldValues' => null, 'holdoutIds' => $holdoutIds,
        ];
    }

    private static function holdout(int $id, string $name, string $unitType, int $seedHi, int $seedLo, array $split, string $holdoutType): object
    {
        return (object)[
            'id' => $id, 'name' => $name, 'iteration' => 1, 'unitType' => $unitType, 'seedHi' => $seedHi,
            'seedLo' => $seedLo, 'split' => $split, 'trafficSeedHi' => 0, 'trafficSeedLo' => 0, 'trafficSplit' => [0, 1],
            'fullOnVariant' => 0, 'applications' => [],
            'variants' => array_map(fn($n) => (object)['name' => $n, 'config' => null], count($split) === 3 ? ['A', 'B', 'C'] : ['A', 'B']),
            'audience' => null, 'audienceStrict' => false, 'customFieldValues' => null, 'holdoutType' => $holdoutType,
        ];
    }

    private static function context(array $experiments, array $holdouts, array $units, HoldoutProbeLogger $logger): Context
    {
        $config = new Config(new Client(new ClientConfig('http://127.0.0.1:1/v1', 'probe', 'website', 'test')));
        $config->setContextDataProvider(new HoldoutProbeNoopProvider());
        $config->setContextEventHandler(new HoldoutProbeNoopHandler());
        $contextConfig = new ContextConfig();
        $contextConfig->setUnits($units);
        $contextConfig->setEventLogger($logger);

        return (new SDK($config))->createContextWithData($contextConfig, new ContextData($experiments, $holdouts));
    }

    private static function verify(string $prefix, string $label, Context $context, HoldoutProbeLogger $logger, string $experiment, int $expectedTreatment, array $expectedSequence): bool
    {
        $before = count($logger->exposures);
        $treatment = $context->getTreatment($experiment);
        $exposures = array_slice($logger->exposures, $before);

        if ($treatment !== $expectedTreatment || $exposures !== $expectedSequence) {
            echo "{$prefix} check {$label} FAILED: expected treatment={$expectedTreatment} with exposure sequence "
                . json_encode($expectedSequence) . ", got treatment={$treatment} exposures=" . json_encode($exposures) . "\n";
            return false;
        }
        return true;
    }

    private static function exposure(int $id, string $name, int $variant): array
    {
        return ['id' => $id, 'name' => $name, 'variant' => $variant];
    }

    // Two-arm semantics, fixture values from scenarios 203-208 and 221 (synthetic ids so all
    // checks share one context), plus 305 (an assignment rule beats suppression).
    private static function holdoutsBattery(): bool
    {
        $heldOut = 'session_id';
        $notHeldOut = 'session_id_b';
        $late = 'user_id';
        $uid = 'e791e240fcd3df7d238cfc285f475e8152fcc0ec';

        $expRule = self::experiment(309, 'probe_h_rule', $heldOut, 0, ['A', 'B'], [409]);
        $expRule->assignmentRules = '{"rules":[{"name":"all","type":"assign","conditions":null,"environments":[],"variant":1}]}';

        $experiments = [
            self::experiment(301, 'probe_a_covered', $heldOut, 0, ['A', 'B'], [401]),
            self::experiment(302, 'probe_b_covered', $notHeldOut, 0, ['A', 'B'], [402]),
            self::experiment(304, 'probe_c_union', $heldOut, 0, ['A', 'B'], [404, 405]),
            self::experiment(305, 'probe_d_covered', $heldOut, 0, ['A', 'B'], [406]),
            self::experiment(306, 'probe_d_uncovered_sibling', $heldOut, 0, ['A', 'B'], null),
            self::experiment(307, 'probe_e_dangling_plus_valid', $heldOut, 0, ['A', 'B'], [999, 407]),
            self::experiment(303, 'probe_f_fullon', $heldOut, 2, ['A', 'B', 'C'], [403]),
            self::experiment(308, 'probe_g_late_unit', $late, 2, ['A', 'B', 'C'], [408], [0.5, 0.5, 0.0]),
            $expRule,
            self::experiment(310, 'probe_i_unit_type', $notHeldOut, 0, ['A', 'B'], [410]),
        ];
        $holdouts = [
            self::holdout(401, 'probe_a_holdout', $heldOut, 13, 111, [0.1, 0.9], 'full'),
            self::holdout(402, 'probe_b_holdout', $notHeldOut, 13, 111, [0.1, 0.9], 'full'),
            self::holdout(404, 'probe_c_holdout_low', $heldOut, 1, 222, [0.1, 0.9], 'full'),
            self::holdout(405, 'probe_c_holdout_high', $heldOut, 13, 111, [0.1, 0.9], 'full'),
            self::holdout(406, 'probe_d_holdout', $heldOut, 13, 111, [0.1, 0.9], 'full'),
            self::holdout(407, 'probe_e_holdout', $heldOut, 13, 111, [0.1, 0.9], 'full'),
            self::holdout(403, 'probe_f_holdout', $heldOut, 13, 111, [0.1, 0.9], 'full'),
            self::holdout(408, 'probe_g_holdout', $late, 1, 222, [0.1, 0.9], 'full'),
            self::holdout(409, 'probe_h_holdout', $heldOut, 13, 111, [0.1, 0.9], 'full'),
            // Declares the held-out unit type, but covers a session_id_b experiment: its arm must be drawn from the
            // covered experiment's unit (not held out), never the holdout's own (held out).
            self::holdout(410, 'probe_i_holdout', $heldOut, 13, 111, [0.1, 0.9], 'full'),
        ];

        $logger = new HoldoutProbeLogger();
        $context = self::context($experiments, $holdouts,
            [$heldOut => $uid, $notHeldOut => 'b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3'], $logger);
        if (!$context->isReady() || $context->isFailed()) {
            echo "[holdouts probe] behavioral self-test FAILED: context not ready\n";
            return false;
        }

        $p = '[holdouts probe]';
        $ok = self::verify($p, 'A (mirrors 203)', $context, $logger, 'probe_a_covered', 0, [self::exposure(401, 'probe_a_holdout', 0)])
            && self::verify($p, 'B (mirrors 204)', $context, $logger, 'probe_b_covered', 0,
                [self::exposure(302, 'probe_b_covered', 0), self::exposure(402, 'probe_b_holdout', 1)])
            && self::verify($p, 'C (mirrors 205)', $context, $logger, 'probe_c_union', 0,
                [self::exposure(404, 'probe_c_holdout_low', 1), self::exposure(405, 'probe_c_holdout_high', 0)])
            && self::verify($p, 'D (mirrors 206, covered)', $context, $logger, 'probe_d_covered', 0, [self::exposure(406, 'probe_d_holdout', 0)])
            && self::verify($p, 'D (mirrors 206, uncovered sibling)', $context, $logger, 'probe_d_uncovered_sibling', 1,
                [self::exposure(306, 'probe_d_uncovered_sibling', 1)])
            && self::verify($p, 'E (mirrors 207)', $context, $logger, 'probe_e_dangling_plus_valid', 0, [self::exposure(407, 'probe_e_holdout', 0)])
            && self::verify($p, 'F (mirrors 208)', $context, $logger, 'probe_f_fullon', 0, [self::exposure(403, 'probe_f_holdout', 0)])
            && self::verify($p, 'H (mirrors 305)', $context, $logger, 'probe_h_rule', 1,
                [self::exposure(309, 'probe_h_rule', 1), self::exposure(409, 'probe_h_holdout', 0)])
            && self::verify($p, 'I (mirrors 227)', $context, $logger, 'probe_i_unit_type', 0,
                [self::exposure(310, 'probe_i_unit_type', 0), self::exposure(410, 'probe_i_holdout', 1)]);
        if (!$ok) {
            return false;
        }

        // G (mirrors 221): a peek before the unit exists exposes nothing; once the unit is set the
        // treatment publishes the experiment and the previously unavailable holdout exposure.
        $before = count($logger->exposures);
        $peeked = $context->peekTreatment('probe_g_late_unit');
        if ($peeked !== 2 || count($logger->exposures) !== $before) {
            echo "{$p} check G (mirrors 221, pre-unit peek) FAILED: expected treatment=2 with no exposures, got treatment={$peeked}\n";
            return false;
        }
        $context->setUnit($late, $uid);
        if (!self::verify($p, 'G (mirrors 221, post-unit treatment)', $context, $logger, 'probe_g_late_unit', 2,
            [self::exposure(308, 'probe_g_late_unit', 2), self::exposure(408, 'probe_g_holdout', 1)])) {
            return false;
        }

        echo "{$p} behavioral self-test PASSED: all 10 checks mirroring scenarios 203-208, 221, 227 and 305 passed\n";
        return true;
    }

    // Three-arm semantics (214-216): arm 0 holds out every covered experiment, arm 1 only the
    // non-full-on ones, arm 2 none. Seeds from the scenarios: 0/1 -> arm 0, 0/3 -> arm 1, 0/0 -> arm 2.
    private static function holdoutArmsBattery(): bool
    {
        $unitType = 'session_id';
        $split = [0.3, 0.3, 0.4];
        $experiments = [];
        $holdouts = [];
        foreach ([[0, 1, 621], [1, 3, 622], [2, 0, 623]] as [$arm, $seedLo, $holdoutId]) {
            $experiments[] = self::experiment(601 + 2 * $arm, "arms_{$arm}_non_fullon", $unitType, 0, ['A', 'B'], [$holdoutId]);
            $experiments[] = self::experiment(602 + 2 * $arm, "arms_{$arm}_fullon", $unitType, 2, ['A', 'B', 'C'], [$holdoutId]);
            $holdouts[] = self::holdout($holdoutId, "arms_holdout_{$arm}", $unitType, 0, $seedLo, $split, 'all_full_on');
        }

        $logger = new HoldoutProbeLogger();
        $context = self::context($experiments, $holdouts, [$unitType => 'e791e240fcd3df7d238cfc285f475e8152fcc0ec'], $logger);

        $p = '[holdout_arms probe]';
        $ok = self::verify($p, '0 non-full-on (mirrors 214)', $context, $logger, 'arms_0_non_fullon', 0, [self::exposure(621, 'arms_holdout_0', 0)])
            && self::verify($p, '0 full-on (mirrors 214)', $context, $logger, 'arms_0_fullon', 0, [])
            && self::verify($p, '1 non-full-on (mirrors 215)', $context, $logger, 'arms_1_non_fullon', 0, [self::exposure(622, 'arms_holdout_1', 1)])
            && self::verify($p, '1 full-on (mirrors 215)', $context, $logger, 'arms_1_fullon', 2, [self::exposure(604, 'arms_1_fullon', 2)])
            && self::verify($p, '2 non-full-on (mirrors 216)', $context, $logger, 'arms_2_non_fullon', 1,
                [self::exposure(605, 'arms_2_non_fullon', 1), self::exposure(623, 'arms_holdout_2', 2)])
            && self::verify($p, '2 full-on (mirrors 216)', $context, $logger, 'arms_2_fullon', 2, [self::exposure(606, 'arms_2_fullon', 2)]);
        if ($ok) {
            echo "{$p} behavioral self-test PASSED: all 6 arm checks mirroring scenarios 214-216 passed\n";
        }
        return $ok;
    }
}
