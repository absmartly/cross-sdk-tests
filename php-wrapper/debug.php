<?php
require __DIR__ . '/vendor/autoload.php';

use ABSmartly\SDK\SDK;
use ABSmartly\SDK\Config;
use ABSmartly\SDK\Context\ContextConfig;
use ABSmartly\SDK\Context\ContextData;
use ABSmartly\SDK\Context\ContextDataProvider;

class MockDataProvider implements ContextDataProvider {
    public function getContextData(): ContextData {
        return new ContextData((object)[]);
    }
}

$config = new Config(
    'http://localhost',
    'key',
    'app',
    'env',
    new MockDataProvider()
);

$sdk = new SDK($config);

$data = new ContextData((object)[
    'experiments' => [
        (object)[
            'id' => 1,
            'name' => 'exp_test_variables',
            'iteration' => 1,
            'unitType' => 'session_id',
            'seedHi' => 0,
            'seedLo' => 1,
            'split' => [0.5, 0.5],
            'trafficSeedHi' => 0,
            'trafficSeedLo' => 0,
            'trafficSplit' => [1, 0],
            'fullOnVariant' => 0,
            'applications' => [(object)['name' => 'website']],
            'variants' => [
                (object)['name' => 'A', 'config' => null],
                (object)['name' => 'B', 'config' => '{"button_color":"red"}']
            ]
        ]
    ]
]);

$contextConfig = new ContextConfig();
$contextConfig->setUnit('session_id', 'test123');
$contextConfig->setPublishDelay(-1);

$context = $sdk->createContextWithData($data, $contextConfig);

echo "Context ready: " . ($context->isReady() ? 'true' : 'false') . "\n";

// Get treatment first to see what variant we get
$treatment = $context->getTreatment('exp_test_variables');
echo "Treatment: $treatment\n";

// Get variable value
$value = $context->getVariableValue('button_color', 'blue');
echo "Value: $value\n";

// Check pending events
$pending = $context->getPendingCount();
echo "Pending events: $pending\n";
