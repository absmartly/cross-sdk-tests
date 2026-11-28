<?php

declare(strict_types=1);

// Behavioral sharedVariableKeys probe, following javascript-wrapper/sharedVariableKeysSelfTest.js.
// Real SDK contexts must return the winner's value AND queue only its exposure. A winner whose
// variant lacks the key returns the default, never a later candidate's value; peeks expose nothing.
// Cached per process and fail-closed, including on SDKs predating winner-only resolution.

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

final class SharedVariableKeysProbeLogger implements ContextEventLogger
{
    public array $exposures = [];

    public function handleEvent(Context $context, ContextEventLoggerEvent $event): void
    {
        if ($event->getEvent() === ContextEventLoggerEvent::Exposure) {
            $data = $event->getData();
            $this->exposures[] = ['id' => $data->id, 'variant' => $data->variant];
        }
    }
}

final class SharedVariableKeysProbeProvider extends ContextDataProvider
{
    public function __construct()
    {
    }

    public function getContextData(): ContextData
    {
        return new ContextData();
    }
}

final class SharedVariableKeysProbeHandler extends ContextEventHandler
{
    public function __construct()
    {
    }

    public function publish(PublishEvent $event): void
    {
    }
}

final class SharedVariableKeysSelfTest
{
    private const KEY = 'shared.key';
    private const DEFAULT_VALUE = 'caller-default';
    private static ?bool $result = null;

    public static function run(): bool
    {
        if (self::$result === null) {
            try {
                self::$result = self::battery();
            } catch (Throwable $e) {
                echo "[sharedVariableKeys probe] FAILED with throwable: {$e->getMessage()}\n";
                self::$result = false;
            }
        }
        return self::$result;
    }

    private static function experiment(int $id, string $name, array $configs, bool $strict = false): object
    {
        return (object)[
            'id' => $id, 'name' => $name, 'iteration' => 1, 'unitType' => 'session_id',
            'seedHi' => 0, 'seedLo' => 0, 'split' => [0, 1],
            'trafficSeedHi' => 0, 'trafficSeedLo' => 0, 'trafficSplit' => [0, 1],
            'fullOnVariant' => 0, 'applications' => [(object)['name' => 'website']],
            'variants' => array_map(fn($config) => (object)['name' => 'arm', 'config' => json_encode($config)], $configs),
            'audience' => $strict ? '{"filter":[{"eq":[{"var":"Country"},{"value":"US"}]}]}' : null,
            'audienceStrict' => $strict, 'customFieldValues' => null,
        ];
    }

    private static function verify(string $label, Context $context, SharedVariableKeysProbeLogger $logger, $expectedValue, array $expectedExposures): bool
    {
        $peeked = $context->peekVariableValue(self::KEY, self::DEFAULT_VALUE);
        $peekExposures = $logger->exposures;
        $peekPending = $context->getPendingCount();
        $value = $context->getVariableValue(self::KEY, self::DEFAULT_VALUE);
        $pending = $context->getPendingCount();
        if ($peeked !== $expectedValue || $peekExposures !== [] || $peekPending !== 0
            || $value !== $expectedValue || $logger->exposures !== $expectedExposures
            || $pending !== count($expectedExposures)) {
            echo '[sharedVariableKeys probe] FAILED (' . $label . '): ' . json_encode([
                'peeked' => $peeked, 'peekExposures' => $peekExposures, 'peekPending' => $peekPending,
                'value' => $value, 'exposures' => $logger->exposures, 'pending' => $pending,
                'expectedValue' => $expectedValue, 'expectedExposures' => $expectedExposures,
            ]) . "\n";
            return false;
        }
        return true;
    }

    private static function battery(): bool
    {
        $key = self::KEY;
        $assigned = self::experiment(176, 'svk_assigned', [[$key => 'assigned-control'], [$key => 'assigned-treatment']]);
        $strict = self::experiment(175, 'svk_strict', [[$key => 'strict-control'], [$key => 'strict-treatment']], true);
        $cases = [
            // Mirrors 341: the strict-mismatched candidate is visited first but never exposed.
            ['strict mismatch skipped', [$assigned, $strict], 'assigned-treatment', [['id' => 176, 'variant' => 1]]],
            // Mirrors 350: reversed payload order must not change which candidate wins.
            ['visited in id order', [
                self::experiment(191, 'svk_high_id', [[$key => 'high-control'], [$key => 'high-treatment']]),
                self::experiment(190, 'svk_low_id', [[$key => 'low-control'], [$key => 'low-treatment']]),
            ], 'low-treatment', [['id' => 190, 'variant' => 1]]],
            // Mirrors 351: the winning variant has no shared key; still expose only that winner.
            ['winner lacks key', [
                self::experiment(195, 'svk_lacks_key', [[$key => 'lacks-control'], ['other.key' => 'x']]),
                self::experiment(196, 'svk_after', [[$key => 'after-control'], [$key => 'after-treatment']]),
            ], self::DEFAULT_VALUE, [['id' => 195, 'variant' => 1]]],
            ['no winner', [$strict], self::DEFAULT_VALUE, []],
        ];
        foreach ($cases as [$label, $experiments, $value, $exposures]) {
            $logger = new SharedVariableKeysProbeLogger();
            $config = new Config(new Client(new ClientConfig('http://127.0.0.1:1/v1', 'probe', 'website', 'test')));
            $config->setContextDataProvider(new SharedVariableKeysProbeProvider());
            $config->setContextEventHandler(new SharedVariableKeysProbeHandler());
            $contextConfig = new ContextConfig();
            $contextConfig->setUnits(['session_id' => 'svk-probe-unit']);
            $contextConfig->setEventLogger($logger);
            $context = (new SDK($config))->createContextWithData($contextConfig, new ContextData($experiments));
            if (!$context->isReady() || $context->isFailed()) {
                throw new RuntimeException('context not ready');
            }
            $context->setAttribute('Country', 'IE');
            if (!self::verify($label, $context, $logger, $value, $exposures)) {
                return false;
            }
        }
        echo "[sharedVariableKeys probe] PASSED: 4 checks\n";
        return true;
    }
}
