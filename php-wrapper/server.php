<?php

declare(strict_types=1);

require __DIR__ . '/vendor/autoload.php';

use ABSmartly\SDK\SDK;
use ABSmartly\SDK\Config;
use ABSmartly\SDK\Client\Client;
use ABSmartly\SDK\Client\ClientConfig;
use ABSmartly\SDK\Http\ReactHttpClient;
use ABSmartly\SDK\Context\Context;
use ABSmartly\SDK\Context\ContextConfig;
use ABSmartly\SDK\Context\ContextData;
use ABSmartly\SDK\Context\ContextDataProvider;
use ABSmartly\SDK\Context\AsyncContextDataProvider;
use ABSmartly\SDK\Context\ContextEventHandler;
use ABSmartly\SDK\Context\ContextEventLogger;
use ABSmartly\SDK\Context\ContextEventLoggerEvent;
use ABSmartly\SDK\PublishEvent;
use React\Http\Server;
use React\Http\Message\Response;
use React\EventLoop\Loop;
use Psr\Http\Message\ServerRequestInterface;

class EventCollector implements ContextEventLogger
{
    private array $events = [];

    public function handleEvent(Context $context, ContextEventLoggerEvent $event): void
    {
        $eventName = $event->getEvent();
        $eventData = $event->getData();

        $eventName = strtolower($eventName);

        $data = null;
        if ($eventData !== null) {
            $data = $this->deepCopy($eventData);
        }

        $this->events[] = [
            'type' => $eventName,
            'data' => $data,
            'timestamp' => (int)(microtime(true) * 1000)
        ];
    }

    private function deepCopy($obj)
    {
        if (is_object($obj)) {
            if ($obj instanceof \JsonSerializable) {
                $obj = $obj->jsonSerialize();
                return $this->deepCopy($obj);
            }
            $obj = get_object_vars($obj);
        }

        if (is_array($obj)) {
            $result = [];
            foreach ($obj as $key => $value) {
                $result[$key] = $this->deepCopy($value);
            }
            return $result;
        }

        return $obj;
    }

    public function getEvents(): array
    {
        return $this->events;
    }

    public function getNewEvents(int $since): array
    {
        return array_slice($this->events, $since);
    }
}

class CustomContextEventHandler extends ContextEventHandler
{
    private EventCollector $eventCollector;
    public bool $shouldFail = false;

    public function __construct(EventCollector $eventCollector)
    {
        $this->eventCollector = $eventCollector;
    }

    public function publish(PublishEvent $event): void
    {
        if ($this->shouldFail) {
            $this->shouldFail = false;
            throw new \RuntimeException('publish failed');
        }
    }
}

class FailingAsyncContextDataProvider extends AsyncContextDataProvider
{
    public function __construct()
    {
        // no client needed
    }

    public function getContextDataAsync(): \React\Promise\PromiseInterface
    {
        $badData = new ContextData([(object)['name' => 'failing_load']]);
        return \React\Promise\resolve($badData);
    }
}

class DummyContextDataProvider extends ContextDataProvider
{
    public function __construct()
    {
    }

    public function getContextData(): ContextData
    {
        return new ContextData();
    }
}

class DeferredContextDataProvider extends AsyncContextDataProvider
{
    private int $throttleMs;
    private Client $httpClient;

    public function __construct(Client $client, int $throttleMs)
    {
        parent::__construct($client);
        $this->httpClient = $client;
        $this->throttleMs = $throttleMs;
    }

    public function getContextDataAsync(): \React\Promise\PromiseInterface
    {
        $deferred = new \React\Promise\Deferred();
        $loop = \React\EventLoop\Loop::get();
        $throttleMs = $this->throttleMs;
        $httpClient = $this->httpClient;

        $loop->addTimer($throttleMs / 1000.0, function() use ($deferred, $httpClient) {
            $httpClient->getContextDataAsync()->then(
                function($data) use ($deferred) {
                    $deferred->resolve($data);
                },
                function($error) use ($deferred) {
                    $deferred->reject($error);
                }
            );
        });

        return $deferred->promise();
    }
}

$contexts = [];
$payloadStore = [];

function jsonResponse(int $status, $data): Response
{
    return new Response(
        $status,
        ['Content-Type' => 'application/json'],
        json_encode($data)
    );
}

function parseJsonBody(ServerRequestInterface $request)
{
    $body = (string) $request->getBody();
    if (empty($body)) {
        return [];
    }
    return json_decode($body, true) ?? [];
}

function parseJsonBodyPreserveObjects(ServerRequestInterface $request)
{
    $body = (string) $request->getBody();
    if (empty($body)) {
        return null;
    }
    return json_decode($body);
}

function waitForClosed(Context $context, int $timeoutMs = 5000, int $pollIntervalMs = 5): void
{
    $deadline = microtime(true) + ($timeoutMs / 1000.0);
    while (!$context->isClosed()) {
        if (microtime(true) >= $deadline) {
            throw new RuntimeException('Context did not finalize within timeout');
        }
        usleep($pollIntervalMs * 1000);
    }
}

function translateEndpoint(string $endpoint): string
{
    return preg_replace('/localhost:\d+/', '127.0.0.1:3000', $endpoint);
}

$server = new Server(function (ServerRequestInterface $request) use (&$contexts, &$payloadStore) {
    $method = $request->getMethod();
    $path = $request->getUri()->getPath();

    if ($method === 'GET' && $path === '/health') {
        return jsonResponse(200, [
            'status' => 'healthy',
            'sdk' => 'php',
            'version' => '1.0.0'
        ]);
    }

    if ($method === 'GET' && $path === '/capabilities') {
        return jsonResponse(200, ['diagnostics' => true,
            'attrsSeq' => false,
            'publishFail' => true,
            'variableKeysMap' => true,
            'globalCustomFieldKeys' => true,
            'getUnits' => true,
            'getAttributes' => true,
            'readyError' => true
        ]);
    }

    if ($method === 'POST' && $path === '/diagnostic') {
        $body = parseJsonBodyPreserveObjects($request);
        $op = (is_object($body) && property_exists($body, 'operation')) ? $body->operation : null;
        $value = (is_object($body) && property_exists($body, 'value')) ? $body->value : null;

        try {
            if ($op === 'hashUnit') {
                $publishEvent = new PublishEvent();
                $result = $publishEvent->hashUnit((string)$value);
            } elseif ($op === 'base64UrlNoPadding') {
                $str = (string)$value;
                $result = rtrim(strtr(base64_encode($str), '+/', '-_'), '=');
            } elseif ($op === 'utf8Bytes') {
                $str = (string)$value;
                $bytes = unpack('C*', $str);
                $result = $bytes ? array_values($bytes) : [];
            } elseif ($op === 'isObject') {
                $result = is_object($value);
            } elseif ($op === 'isNumeric') {
                $result = is_int($value) || is_float($value);
            } elseif ($op === 'isPromise') {
                $result = false;
            } else {
                return jsonResponse(400, ['error' => "Unsupported diagnostic operation: {$op}"]);
            }

            return jsonResponse(200, ['result' => $result, 'events' => []]);
        } catch (Throwable $e) {
            return jsonResponse(500, ['error' => $e->getMessage()]);
        }
    }

    if ($method === 'PUT' && preg_match('#^/context_payload/([^/]+)$#', $path, $matches)) {
        $payloadId = $matches[1];
        $body = parseJsonBody($request);
        $payloadStore[$payloadId] = $body['data'] ?? ['experiments' => []];

        return jsonResponse(200, ['success' => true]);
    }

    if ($method === 'GET' && preg_match('#^/context_payload/([^/]+)/context$#', $path, $matches)) {
        $payloadId = $matches[1];
        $data = $payloadStore[$payloadId] ?? ['experiments' => []];
        return jsonResponse(200, $data);
    }

    if ($method === 'GET' && preg_match('#^/context_payload/(.+)$#', $path, $matches)) {
        $payloadId = $matches[1];
        $throttle = (int)($request->getQueryParams()['throttle'] ?? 0);

        if ($throttle > 0) {
            usleep($throttle * 1000);
        }

        $data = $payloadStore[$payloadId] ?? ['experiments' => []];

        return jsonResponse(200, $data);
    }

    if ($method === 'POST' && $path === '/context') {
        $body = parseJsonBody($request);
        $contextId = 'ctx-' . time() . '-' . mt_rand();

        $eventCollector = new EventCollector();
        $eventHandler = new CustomContextEventHandler($eventCollector);

        $endpoint = $body['endpoint'] ?? 'http://dummy';
        $endpoint = translateEndpoint($endpoint);

        $clientConfig = new ClientConfig(
            $endpoint,
            'dummy',
            'test',
            'test'
        );

        $reactHttpClient = new ReactHttpClient();
        $reactHttpClient->timeout = 10000;
        $client = new Client($clientConfig, $reactHttpClient);

        $sdkConfig = new Config($client);
        $sdkConfig->setContextEventHandler($eventHandler);

        $payloadThrottle = (int)($body['options']['payloadThrottle'] ?? 0);

        $failLoad = (bool)($body['failLoad'] ?? false);

        if (isset($body['data'])) {
            $sdkConfig->setContextDataProvider(new DummyContextDataProvider());
        } elseif ($failLoad) {
            $sdkConfig->setContextDataProvider(new FailingAsyncContextDataProvider());
        } elseif ($payloadThrottle > 0) {
            $sdkConfig->setContextDataProvider(new DeferredContextDataProvider($client, $payloadThrottle));
        } else {
            $sdkConfig->setContextDataProvider(new AsyncContextDataProvider($client));
        }

        $sdk = new SDK($sdkConfig);

        $contextConfig = new ContextConfig();
        $contextConfig->setPublishDelay(-1);
        $contextConfig->setRefreshInterval(0);

        if (isset($body['units'])) {
            foreach ($body['units'] as $unitType => $uid) {
                $contextConfig->setUnit($unitType, (string)$uid);
            }
        }

        if (isset($body['options']['publishDelay'])) {
            $contextConfig->setPublishDelay((int)$body['options']['publishDelay']);
        }
        if (isset($body['options']['refreshPeriod'])) {
            $contextConfig->setRefreshInterval((int)$body['options']['refreshPeriod']);
        }

        $contextConfig->setEventLogger($eventCollector);

        if (isset($body['data'])) {
            $contextData = new ContextData();
            if (isset($body['data']['experiments'])) {
                $contextData->experiments = array_map(function($exp) {
                    if (isset($exp['customFieldValues']) && is_array($exp['customFieldValues'])) {
                        $customFieldValuesObj = new \stdClass();
                        foreach ($exp['customFieldValues'] as $field) {
                            $name = $field['name'];
                            $customFieldValuesObj->{$name} = $field['value'];
                            $customFieldValuesObj->{$name . '_type'} = $field['type'];
                        }
                        $exp['customFieldValues'] = $customFieldValuesObj;
                    }
                    return json_decode(json_encode($exp));
                }, $body['data']['experiments']);
            } else {
                $contextData->experiments = [];
            }
            $context = $sdk->createContextWithData($contextConfig, $contextData);

            $contexts[$contextId] = [
                'context' => $context,
                'eventCollector' => $eventCollector,
                'eventHandler' => $eventHandler,
                'sdk' => $sdk
            ];

            return jsonResponse(200, [
                'result' => [
                    'contextId' => $contextId,
                    'ready' => $context->isReady(),
                    'failed' => $context->isFailed(),
                    'finalized' => $context->isClosed()
                ],
                'events' => $eventCollector->getEvents()
            ]);
        } elseif ($failLoad) {
            $result = $sdk->createContextPending($contextConfig);
            $promise = $result['promise'];

            return $promise->then(function($ctx) use ($contextId, $eventCollector, $eventHandler, $sdk, &$contexts) {
                $contexts[$contextId] = [
                    'context' => $ctx,
                    'eventCollector' => $eventCollector,
                    'eventHandler' => $eventHandler,
                    'sdk' => $sdk
                ];

                return jsonResponse(200, [
                    'result' => [
                        'contextId' => $contextId,
                        'ready' => $ctx->isReady(),
                        'failed' => $ctx->isFailed(),
                        'finalized' => $ctx->isClosed()
                    ],
                    'events' => $eventCollector->getEvents()
                ]);
            });
        } else {
            if ($payloadThrottle > 0) {
                $result = $sdk->createContextPending($contextConfig);
                $context = $result['context'];
                $promise = $result['promise'];

                $contexts[$contextId] = [
                    'context' => $context,
                    'eventCollector' => $eventCollector,
                    'eventHandler' => $eventHandler,
                    'sdk' => $sdk,
                    'pendingPromise' => $promise
                ];

                return jsonResponse(200, [
                    'result' => [
                        'contextId' => $contextId,
                        'ready' => $context->isReady(),
                        'failed' => $context->isFailed(),
                        'finalized' => $context->isClosed()
                    ],
                    'events' => $eventCollector->getEvents()
                ]);
            }

            return $sdk->createContextAsync($contextConfig)->then(
                function($context) use ($contextId, $eventCollector, $eventHandler, $sdk, &$contexts) {
                    for ($i = 0; $i < 50 && empty($eventCollector->getEvents()); $i++) {
                        usleep(10000);
                    }

                    $contexts[$contextId] = [
                        'context' => $context,
                        'eventCollector' => $eventCollector,
                        'eventHandler' => $eventHandler,
                        'sdk' => $sdk
                    ];

                    return jsonResponse(200, [
                        'result' => [
                            'contextId' => $contextId,
                            'ready' => $context->isReady(),
                            'failed' => $context->isFailed(),
                            'finalized' => $context->isClosed()
                        ],
                        'events' => $eventCollector->getEvents()
                    ]);
                },
                function($error) {
                    return jsonResponse(500, ['error' => $error->getMessage()]);
                }
            );
        }
    }

    if (preg_match('#^/context/([^/]+)/setUnit$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());

        $body = parseJsonBody($request);

        try {
            $context->setUnit($body['unitType'], (string)$body['uid']);

            $newEvents = $eventCollector->getNewEvents($eventsBefore);

            return jsonResponse(200, ['result' => null, 'events' => $newEvents]);
        } catch (Throwable $e) {
            $error = $e->getMessage();
            if (stripos($error, 'already set') !== false) {
                $unitType = $body['unitType'] ?? '';
                $error = "Unit '{$unitType}' UID already set.";
            }
            return jsonResponse(400, ['error' => $error]);
        }
    }

    if (preg_match('#^/context/([^/]+)/getUnit$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());

        $body = parseJsonBody($request);

        try {
            $result = $context->getUnit($body['unitType']);
            if ($result !== null && is_numeric($result)) {
                if (strpos($result, '.') !== false) {
                    $result = (float) $result;
                } else {
                    $result = (int) $result;
                }
            }
            $newEvents = $eventCollector->getNewEvents($eventsBefore);

            return jsonResponse(200, ['result' => $result, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/attribute$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());

        $body = parseJsonBody($request);

        try {
            $context->setAttribute($body['name'], $body['value']);
            $newEvents = $eventCollector->getNewEvents($eventsBefore);

            return jsonResponse(200, ['result' => null, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage() . ' at ' . $e->getFile() . ':' . $e->getLine()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/getAttribute$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());

        $body = parseJsonBody($request);

        try {
            $result = $context->getAttribute($body['name']);
            $newEvents = $eventCollector->getNewEvents($eventsBefore);

            return jsonResponse(200, ['result' => $result, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/treatment$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());

        $body = parseJsonBody($request);

        if (!$context->isReady()) {
            return jsonResponse(200, ['result' => 0, 'events' => []]);
        }

        try {
            $experimentName = $body['experimentName'] ?? $body['experiment'] ?? null;
            $variant = $context->getTreatment($experimentName);
            $newEvents = $eventCollector->getNewEvents($eventsBefore);

            return jsonResponse(200, ['result' => $variant, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage() . ' at ' . $e->getFile() . ':' . $e->getLine()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/peek$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());

        $body = parseJsonBody($request);

        try {
            $variant = $context->peekTreatment($body['experimentName']);
            $newEvents = $eventCollector->getNewEvents($eventsBefore);

            return jsonResponse(200, ['result' => $variant, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/variableValue$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());

        $body = parseJsonBody($request);

        if (!$context->isReady()) {
            return jsonResponse(200, ['result' => $body['defaultValue'] ?? null, 'events' => []]);
        }

        try {
            $value = $context->getVariableValue($body['key'], $body['defaultValue'] ?? null);
            $newEvents = $eventCollector->getNewEvents($eventsBefore);

            return jsonResponse(200, ['result' => $value, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/peekVariableValue$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());

        $body = parseJsonBody($request);

        try {
            $value = $context->peekVariableValue($body['key'], $body['defaultValue'] ?? null);
            $newEvents = $eventCollector->getNewEvents($eventsBefore);

            return jsonResponse(200, ['result' => $value, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/track$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());

        $body = parseJsonBody($request);

        try {
            $properties = isset($body['properties']) ? (object)$body['properties'] : null;
            $context->track($body['goalName'], $properties);
            $newEvents = $eventCollector->getNewEvents($eventsBefore);

            return jsonResponse(200, ['result' => null, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/override$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];

        $body = parseJsonBody($request);

        try {
            $context->setOverride($body['experimentName'], (int)$body['variant']);

            return jsonResponse(200, ['result' => null, 'events' => []]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/customAssignment$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];

        $body = parseJsonBody($request);

        try {
            $context->setCustomAssignment($body['experimentName'], (int)$body['variant']);

            return jsonResponse(200, ['result' => null, 'events' => []]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/customFieldValue$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());

        $body = parseJsonBody($request);

        try {
            $value = $context->customFieldValue($body['experimentName'], $body['fieldName']);
            $newEvents = $eventCollector->getNewEvents($eventsBefore);

            return jsonResponse(200, ['result' => $value, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/variableKeys$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());

        if (!$context->isReady()) {
            return jsonResponse(200, ['result' => [], 'events' => []]);
        }

        try {
            $keys = $context->getVariableKeys();
            $keyArray = is_array($keys) ? array_keys($keys) : $keys;
            $newEvents = $eventCollector->getNewEvents($eventsBefore);

            return jsonResponse(200, ['result' => $keyArray, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/customFieldKeys$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());

        $body = parseJsonBody($request);

        try {
            $keys = $context->getCustomFieldKeys();
            sort($keys);

            $newEvents = $eventCollector->getNewEvents($eventsBefore);

            return jsonResponse(200, ['result' => $keys, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/customFieldValueType$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());

        $body = parseJsonBody($request);

        try {
            $result = $context->getCustomFieldValueType($body['experimentName'], $body['fieldName']);

            $newEvents = $eventCollector->getNewEvents($eventsBefore);

            return jsonResponse(200, ['result' => $result, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/setOverride$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());

        $body = parseJsonBody($request);

        try {
            $context->setOverride($body['experimentName'], (int)$body['variant']);
            $newEvents = $eventCollector->getNewEvents($eventsBefore);

            return jsonResponse(200, ['result' => null, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/setCustomAssignment$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());

        $body = parseJsonBody($request);

        try {
            $context->setCustomAssignment($body['experimentName'], (int)$body['variant']);
            $newEvents = $eventCollector->getNewEvents($eventsBefore);

            return jsonResponse(200, ['result' => null, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/pending$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];

        try {
            return jsonResponse(200, ['result' => $context->pending(), 'events' => []]);
        } catch (Throwable $e) {
            return jsonResponse(500, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/isFinalized$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];

        return jsonResponse(200, ['result' => $context->isClosed(), 'events' => []]);
    }

    if (preg_match('#^/context/([^/]+)/isReady$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];

        return jsonResponse(200, ['result' => $context->isReady(), 'events' => []]);
    }

    if (preg_match('#^/context/([^/]+)/isFailed$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];

        return jsonResponse(200, ['result' => $context->isFailed(), 'events' => []]);
    }

    if (preg_match('#^/context/([^/]+)/experiments$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];

        if (!$context->isReady()) {
            return jsonResponse(200, ['result' => [], 'events' => []]);
        }

        try {
            $experiments = $context->getExperiments();
            return jsonResponse(200, ['result' => $experiments, 'events' => []]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/getUnits$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) return jsonResponse(404, ['error' => 'Context not found']);
        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());
        try {
            $units = $context->getUnits();
            $result = [];
            foreach ($units as $k => $v) {
                if (is_numeric($v)) {
                    $result[$k] = strpos($v, '.') !== false ? (float)$v : (int)$v;
                } else {
                    $result[$k] = $v;
                }
            }
            $newEvents = $eventCollector->getNewEvents($eventsBefore);
            return jsonResponse(200, ['result' => (object)$result, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/getAttributes$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) return jsonResponse(404, ['error' => 'Context not found']);
        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());
        try {
            $result = $context->getAttributes();
            $newEvents = $eventCollector->getNewEvents($eventsBefore);
            return jsonResponse(200, ['result' => (object)$result, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/readyError$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) return jsonResponse(404, ['error' => 'Context not found']);
        $context = $data['context'];
        try {
            $error = $context->readyError();
            $result = $error ? ['isError' => true, 'message' => $error->getMessage()] : null;
            return jsonResponse(200, ['result' => $result, 'events' => []]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/variableKeysMap$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) return jsonResponse(404, ['error' => 'Context not found']);
        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());
        try {
            $keysMap = [];
            foreach ($context->getContextData()->experiments as $experiment) {
                $expName = $experiment->name;
                foreach ($experiment->variants as $variant) {
                    if (empty($variant->config)) continue;
                    $parsed = json_decode($variant->config, false);
                    if (!$parsed) continue;
                    foreach (array_keys(get_object_vars($parsed)) as $varKey) {
                        if (!isset($keysMap[$varKey])) {
                            $keysMap[$varKey] = [];
                        }
                        if (!in_array($expName, $keysMap[$varKey], true)) {
                            $keysMap[$varKey][] = $expName;
                        }
                    }
                }
            }
            $newEvents = $eventCollector->getNewEvents($eventsBefore);
            return jsonResponse(200, ['result' => (object)$keysMap, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/globalCustomFieldKeys$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) return jsonResponse(404, ['error' => 'Context not found']);
        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());
        try {
            $keys = $context->getCustomFieldKeys();
            sort($keys);
            $newEvents = $eventCollector->getNewEvents($eventsBefore);
            return jsonResponse(200, ['result' => $keys, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/publishFail$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) return jsonResponse(404, ['error' => 'Context not found']);
        $data['eventHandler']->shouldFail = true;
        $contexts[$contextId] = $data;
        return jsonResponse(200, ['result' => null, 'events' => []]);
    }

    if (preg_match('#^/context/([^/]+)/publish$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());

        try {
            $context->publish();
            $newEvents = $eventCollector->getNewEvents($eventsBefore);

            return jsonResponse(200, ['result' => null, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(500, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)/refresh$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());

        return \React\Async\async(function() use ($context, $eventCollector, $eventsBefore) {
            try {
                $context->refresh();

                $newEvents = $eventCollector->getNewEvents($eventsBefore);

                return jsonResponse(200, ['result' => null, 'events' => $newEvents]);
            } catch (Throwable $e) {
                return jsonResponse(500, ['error' => $e->getMessage()]);
            }
        })();
    }

    if (preg_match('#^/context/([^/]+)/finalize$#', $path, $matches)) {
        $contextId = $matches[1];
        $data = $contexts[$contextId] ?? null;
        if (!$data) {
            return jsonResponse(404, ['error' => 'Context not found']);
        }

        $context = $data['context'];
        $eventCollector = $data['eventCollector'];
        $eventsBefore = count($eventCollector->getEvents());

        try {
            $context->close();
            waitForClosed($context);
            $newEvents = $eventCollector->getNewEvents($eventsBefore);

            return jsonResponse(200, ['result' => null, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(500, ['error' => $e->getMessage()]);
        }
    }

    if (preg_match('#^/context/([^/]+)$#', $path, $matches) && $method === 'DELETE') {
        $contextId = $matches[1];
        unset($contexts[$contextId]);

        return jsonResponse(200, ['result' => 'deleted']);
    }

    return jsonResponse(404, ['error' => 'Not found']);
});

$socket = new React\Socket\SocketServer('0.0.0.0:3000');
$server->listen($socket);

echo "PHP ReactPHP server listening on port 3000\n";
