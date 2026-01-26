<?php

declare(strict_types=1);

require __DIR__ . '/vendor/autoload.php';

use ABSmartly\SDK\SDK;
use ABSmartly\SDK\Config;
use ABSmartly\SDK\Client\Client;
use ABSmartly\SDK\Client\ClientConfig;
use ABSmartly\SDK\Http\HTTPClient;
use ABSmartly\SDK\Context\Context;
use ABSmartly\SDK\Context\ContextConfig;
use ABSmartly\SDK\Context\ContextData;
use ABSmartly\SDK\Context\ContextDataProvider;
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

    public function __construct(EventCollector $eventCollector)
    {
        $this->eventCollector = $eventCollector;
    }

    public function publish(PublishEvent $event): void
    {
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
        return jsonResponse(200, [
            'asyncContext' => false,
            'attrsSeq' => false
        ]);
    }

    if ($method === 'PUT' && $path === '/context_payload') {
        $body = parseJsonBody($request);
        $payloadId = 'payload-' . time() . '-' . mt_rand();
        $payloadStore[$payloadId] = $body['data'] ?? ['experiments' => []];

        $url = "http://php-sdk:3000/context_payload/" . $payloadId;

        return jsonResponse(200, [
            'payloadUrl' => $url,
            'payloadId' => $payloadId
        ]);
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
        $clientConfig = new ClientConfig(
            $endpoint,
            'dummy',
            'test',
            'test'
        );
        $client = new Client($clientConfig, new HTTPClient());

        $sdkConfig = new Config($client);
        $sdkConfig->setContextEventHandler($eventHandler);
        $sdkConfig->setContextDataProvider(new DummyContextDataProvider());

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
            // Sync: createContextWithData
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
        } else {
            // Async: createContext (SDK will fetch from endpoint)
            $context = $sdk->createContext($contextConfig);
        }

        $contexts[$contextId] = [
            'context' => $context,
            'eventCollector' => $eventCollector,
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
            $reflection = new ReflectionClass($context);
            $unitsProperty = $reflection->getProperty('units');
            $unitsProperty->setAccessible(true);
            $units = $unitsProperty->getValue($context);
            $units[$body['unitType']] = (string)$body['uid'];
            $unitsProperty->setValue($context, $units);

            $newEvents = $eventCollector->getNewEvents($eventsBefore);

            return jsonResponse(200, ['result' => null, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(400, ['error' => $e->getMessage()]);
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

        try {
            $keys = $context->getVariableKeys();
            // Extract just the keys from the dict
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
            $reflection = new ReflectionClass($context);
            $dataProperty = $reflection->getProperty('data');
            $dataProperty->setAccessible(true);
            $contextData = $dataProperty->getValue($context);

            $keys = [];
            if (isset($contextData->experiments)) {
                foreach ($contextData->experiments as $experiment) {
                    if (isset($experiment->customFieldValues)) {
                        foreach (get_object_vars($experiment->customFieldValues) as $key => $value) {
                            if (!str_ends_with($key, '_type')) {
                                $keys[] = $key;
                            }
                        }
                    }
                }
            }
            $keys = array_values(array_unique($keys));
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
            $reflection = new ReflectionClass($context);
            $dataProperty = $reflection->getProperty('data');
            $dataProperty->setAccessible(true);
            $contextData = $dataProperty->getValue($context);

            $result = null;
            if (isset($contextData->experiments)) {
                foreach ($contextData->experiments as $experiment) {
                    if ($experiment->name === $body['experimentName']) {
                        if (isset($experiment->customFieldValues)) {
                            $typeKey = $body['fieldName'] . '_type';
                            if (property_exists($experiment->customFieldValues, $typeKey)) {
                                $result = $experiment->customFieldValues->{$typeKey};
                            }
                        }
                        break;
                    }
                }
            }

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

        $body = parseJsonBody($request);

        try {
            $reflection = new ReflectionClass($context);

            $cacheProperty = $reflection->getProperty('assignmentCache');
            $cacheProperty->setAccessible(true);
            $cacheProperty->setValue($context, []);

            $method = $reflection->getMethod('setData');
            $method->setAccessible(true);

            $contextData = new ContextData();
            if (isset($body['newData']['experiments'])) {
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
                }, $body['newData']['experiments']);
            } else {
                $contextData->experiments = [];
            }

            $method->invoke($context, $contextData);

            $eventCollector->handleEvent(
                $context,
                new ContextEventLoggerEvent('Refresh', (object)$body['newData'])
            );

            $newEvents = $eventCollector->getNewEvents($eventsBefore);

            return jsonResponse(200, ['result' => null, 'events' => $newEvents]);
        } catch (Throwable $e) {
            return jsonResponse(500, ['error' => $e->getMessage()]);
        }
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
