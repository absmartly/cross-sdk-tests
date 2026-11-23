<?php
// Run: php switchback_self_test_test.php /path/to/php-sdk/vendor/autoload.php
// Isolated subprocesses exercise SDKs whose assigner class lacks one method.
declare(strict_types=1);

$autoload = $argv[1] ?? __DIR__ . '/vendor/autoload.php';
if (($argv[2] ?? '') === '--availability') {
    $missing = $argv[3];
    $methods = '';
    foreach (['assign', 'baseHash', 'fisherYatesPermutation'] as $method) {
        if ($method !== $missing) {
            $methods .= "public static function {$method}() {}";
        }
    }
    eval('namespace ABSmartly\\SDK\\Switchback; class SwitchbackAssigner {' . $methods . '}');
    require $autoload;
    require __DIR__ . '/SwitchbackSelfTest.php';
    exit(SwitchbackSelfTest::isAvailable() ? 1 : 0);
}

require $autoload;
require __DIR__ . '/SwitchbackSelfTest.php';

function check(bool $condition, string $message): void
{
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

function setContextAttribute(ABSmartly\SDK\Context\Context $context, string $name, $value): void
{
    if (!is_string($value) && method_exists($context, 'setAttributeValue')) {
        $context->setAttributeValue($name, $value);
    } else {
        $context->setAttribute($name, $value);
    }
}

check(SwitchbackSelfTest::isAvailable(), 'linked assigner must be available');
check(SwitchbackSelfTest::run(), 'linked SDK must pass the full behavioral probe');
foreach (['assign', 'baseHash', 'fisherYatesPermutation'] as $missing) {
    $command = [PHP_BINARY, __FILE__, $autoload, '--availability', $missing];
    $process = proc_open($command, [1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
    $output = stream_get_contents($pipes[1]) . stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    check(proc_close($process) === 0, "missing {$missing} must report unavailable: {$output}");
}

// Keep the queued/published exposures correct while changing only the return value.
class PublishFixtureContext extends ABSmartly\SDK\Context\Context
{
    private SwitchbackProbeHandler $handler;
    private string $wrong;
    private ?string $pending = null;
    public function __construct(SwitchbackProbeHandler $handler, string $wrong)
    {
        $this->handler = $handler;
        $this->wrong = $wrong;
    }
    public function getTreatment(string $experimentName): int
    {
        $this->pending = $experimentName;
        return $experimentName === $this->wrong ? 0 : 1;
    }
    public function publish(): void
    {
        if ($this->pending === null) {
            return;
        }
        $id = ['sb_unit_baseline' => 514, 'sb_payload' => 513, 'sb_unit_with_fields' => 527][$this->pending];
        $event = ['hashed' => true, 'publishedAt' => 1,
            'units' => [['type' => 'unit_serving', 'uid' => 'r5uxZRoi-JuM5yYJa0kmSQ']],
            'exposures' => [['id' => $id, 'name' => $this->pending, 'unit' => 'unit_serving', 'variant' => 1,
                'exposedAt' => 1, 'assigned' => true, 'eligible' => true, 'overridden' => false,
                'fullOn' => false, 'custom' => false, 'audienceMismatch' => false, 'ruleOverride' => false]]];
        $this->handler->published[] = json_encode($event);
        $this->pending = null;
    }
}

$client = new ABSmartly\SDK\Client\Client(new ABSmartly\SDK\Client\ClientConfig('http://unused', 'probe', 'test', 'test'));
$method = new ReflectionMethod(SwitchbackSelfTest::class, 'checkPublishPayload');
foreach (['', 'sb_unit_baseline', 'sb_payload'] as $wrong) {
    $handler = new SwitchbackProbeHandler($client);
    $context = new PublishFixtureContext($handler, $wrong);
    check($method->invoke(null, $context, $handler) === ($wrong === ''), "treatment check failed for {$wrong}");
}
echo "PHP wrapper: 7 checks passed (live SDK, 3 missing methods, good publish, 2 wrong treatments)\n";
