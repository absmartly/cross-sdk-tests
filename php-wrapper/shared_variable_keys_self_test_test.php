<?php
// Run: php shared_variable_keys_self_test_test.php /path/to/php-sdk/vendor/autoload.php
// Like switchback_self_test_test.php, regressions fail the wrapper image build. Each subprocess
// starts with a fresh probe cache; its SDK model changes one rule at a time, never SDK source.
declare(strict_types=1);

$autoload = $argv[1] ?? __DIR__ . '/vendor/autoload.php';
$mutation = $argv[2] ?? null;
require $autoload;
if ($mutation !== null) {
    // Substitute only SDK construction when loading the probe, like the JS wrapper's module stub.
    // Composer may eagerly load the real SDK class, so do not redeclare or replace that class.
    $source = file_get_contents(__DIR__ . '/SharedVariableKeysSelfTest.php');
    $source = str_replace('use ABSmartly\\SDK\\SDK;', 'use SharedVariableKeysFixtureSDK as SDK;', $source, $replaced);
    if ($replaced !== 1) {
        throw new RuntimeException('probe SDK import must be replaced exactly once');
    }
    eval(substr($source, strlen("<?php\n")));
} else {
    require __DIR__ . '/SharedVariableKeysSelfTest.php';
}

function check(bool $condition, string $message): void
{
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

if ($mutation !== null) {
    class SharedVariableKeysFixtureSDK
    {
        public function __construct($config)
        {
        }
        public function createContextWithData($config, $data)
        {
            return new SharedVariableKeysFixtureContext($data->experiments, $config->getEventLogger());
        }
    }

    // Extending the linked Context keeps the public method signatures checked.
    class SharedVariableKeysFixtureContext extends ABSmartly\SDK\Context\Context
    {
        private array $experiments;
        private SharedVariableKeysProbeLogger $logger;
        private array $attributes = [];
        private array $exposed = [];
        private bool $consumed = false;

        public function __construct(array $experiments, SharedVariableKeysProbeLogger $logger)
        {
            $this->experiments = $experiments;
            $this->logger = $logger;
        }
        public function isReady(): bool { return true; }
        public function isFailed(): bool { return false; }
        public function setAttribute(string $name, string $value): ABSmartly\SDK\Context\Context
        {
            $this->attributes[$name] = $value;
            return $this;
        }
        public function getPendingCount(): int
        {
            return count($this->exposed) + ($GLOBALS['mutation'] === 'extraPending' || ($GLOBALS['mutation'] === 'consumeExtraPending' && $this->consumed) ? 1 : 0);
        }
        private function expose(object $experiment): void
        {
            if (!isset($this->exposed[$experiment->id])) {
                $this->exposed[$experiment->id] = true;
                $this->logger->handleEvent($this, new ABSmartly\SDK\Context\ContextEventLoggerEvent(
                    ABSmartly\SDK\Context\ContextEventLoggerEvent::Exposure,
                    (object)['id' => $experiment->id, 'variant' => 1]
                ));
            }
        }
        private function resolve(string $key, $default, bool $expose)
        {
            $mutation = $GLOBALS['mutation'];
            if ($mutation === 'throwable') {
                throw new RuntimeException('simulated SDK failure');
            }
            $candidates = $this->experiments;
            if ($mutation !== 'payloadOrder') {
                usort($candidates, fn($a, $b) => $a->id <=> $b->id);
            }
            foreach ($candidates as $i => $experiment) {
                $assigned = !$experiment->audienceStrict || ($this->attributes['Country'] ?? null) === 'US';
                if (!$assigned) {
                    if ($expose && $mutation === 'exposeVisited') {
                        $this->expose($experiment);
                    }
                    continue;
                }
                if ($expose || $mutation === 'peekExposes') {
                    $this->expose($experiment);
                }
                if ($expose && $mutation === 'extraExposure' && isset($candidates[$i + 1])) {
                    $this->expose($candidates[$i + 1]);
                }
                $config = json_decode($experiment->variants[1]->config);
                if (property_exists($config, $key)) {
                    return $config->$key;
                }
                if ($mutation !== 'fallThrough') {
                    return $default;
                }
            }
            return $default;
        }
        public function peekVariableValue(string $key, $default)
        {
            $value = $this->resolve($key, $default, false);
            return $GLOBALS['mutation'] === 'peekWrongValue' ? 'wrong-' . $value : $value;
        }
        public function getVariableValue(string $key, $defaultValue = null)
        {
            $value = $this->resolve($key, $defaultValue, true);
            $this->consumed = true;
            return $GLOBALS['mutation'] === 'consumeWrongValue' ? 'wrong-' . $value : $value;
        }
    }
    $expected = $mutation === 'correct';
    check(SharedVariableKeysSelfTest::run() === $expected, "probe must detect {$mutation}");
    // Changing the model after the first call must not rerun either a successful or failed probe.
    $GLOBALS['mutation'] = $expected ? 'throwable' : 'correct';
    check(SharedVariableKeysSelfTest::run() === $expected, 'probe result must be cached');
    exit(0);
}

check(SharedVariableKeysSelfTest::run(), 'linked SDK must pass the sharedVariableKeys behavioral probe');
foreach (['correct', 'extraExposure', 'fallThrough', 'payloadOrder', 'peekExposes', 'exposeVisited',
          'peekWrongValue', 'extraPending', 'consumeWrongValue', 'consumeExtraPending', 'throwable'] as $case) {
    $process = proc_open([PHP_BINARY, __FILE__, $autoload, $case], [1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
    $output = stream_get_contents($pipes[1]) . stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    check(proc_close($process) === 0, "{$case}: {$output}");
}
echo "PHP sharedVariableKeys wrapper: 12 checks passed (live SDK, correct model, 10 mutations; cache checked)\n";
