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

// Dynamic additive fields must be checked on a constructed model. A declaration-only check would
// reject the released-compatible model, while ignoring the model entirely would miss these mutants.
$wireCheck = new ReflectionMethod(SwitchbackSelfTest::class, 'checkWireModel');
$payloads = json_decode(SwitchbackSelfTest::probePayload())->experiments;
$wireChecks = 0;
foreach ($payloads as $payload) {
    $model = new ABSmartly\SDK\Experiment($payload);
    check($wireCheck->invoke(null, $model, $payload), "wire model must preserve {$payload->name}");
    ++$wireChecks;
    foreach (['switchbackClusters', 'switchbackClusterValues'] as $field) {
        if (isset($payload->$field)) {
            $missing = (object) get_object_vars($model);
            unset($missing->$field);
            check(!$wireCheck->invoke(null, $missing, $payload), "missing {$field} must fail");
            ++$wireChecks;
            $wrong = (object) get_object_vars($model);
            $wrong->$field = $field === 'switchbackClusters' ? ['wrong_unit' => 'wrong_path'] : ['wrong_value'];
            check(!$wireCheck->invoke(null, $wrong, $payload), "wrong {$field} must fail");
            ++$wireChecks;
        }
    }
}
$legacy = (object) get_object_vars(new ABSmartly\SDK\Experiment($payloads[0]));
$legacy->switchbackClusterCount = 10;
check(!$wireCheck->invoke(null, $legacy, $payloads[0]), 'obsolete switchbackClusterCount must fail');
++$wireChecks;

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
echo "PHP wrapper: 7 checks passed (live SDK, 3 missing methods, good publish, 2 wrong treatments); {$wireChecks} wire-model checks passed\n";
