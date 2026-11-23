"""Unit tests for the python wrapper's switchback probe and publish reporting.

They need python3-sdk importable (run from python-wrapper/ with the SDK
installed, as in the wrapper image):

    python -m unittest test_switchback_self_test
"""
import json
import unittest
from unittest import mock

import switchback_self_test
from sdk.default_context_event_serializer import DefaultContextEventSerializer
from sdk.json.exposure import Exposure
from sdk.json.publish_event import PublishEvent


class SwitchbackSelfTestTest(unittest.TestCase):

    def setUp(self):
        switchback_self_test._result = None

    def tearDown(self):
        switchback_self_test._result = None

    def test_passes_on_the_linked_sdk(self):
        self.assertTrue(switchback_self_test.run())

    def test_fails_when_the_serialized_switchback_variant_is_wrong(self):
        # The in-memory exposure stays correct; only the publish bytes change.
        serialize = DefaultContextEventSerializer.serialize

        def wrong_variant(serializer, event):
            payload = json.loads(serialize(serializer, event))
            for exposure in payload.get('exposures') or []:
                if exposure.get('name') == 'sb_payload':
                    exposure['variant'] = 0
            return bytearray(json.dumps(payload), 'utf-8')

        with mock.patch.object(DefaultContextEventSerializer, 'serialize',
                               wrong_variant):
            self.assertFalse(switchback_self_test.run())

    def test_fails_when_a_serialized_flag_is_wrong(self):
        serialize = DefaultContextEventSerializer.serialize

        def wrong_flag(serializer, event):
            payload = json.loads(serialize(serializer, event))
            for exposure in payload.get('exposures') or []:
                if exposure.get('name') == 'sb_payload':
                    exposure['eligible'] = 0
            return bytearray(json.dumps(payload), 'utf-8')

        with mock.patch.object(DefaultContextEventSerializer, 'serialize',
                               wrong_flag):
            self.assertFalse(switchback_self_test.run())

    def test_fails_when_publish_fixture_returns_the_wrong_treatment(self):
        from sdk.context import Context
        treatment = Context.get_treatment
        for name in ('sb_unit_baseline', 'sb_payload'):
            with self.subTest(name=name):
                switchback_self_test._result = None

                def wrong_result(context, experiment, *args, **kwargs):
                    result = treatment(context, experiment, *args, **kwargs)
                    return 0 if experiment == name else result

                with mock.patch.object(Context, 'get_treatment', wrong_result):
                    self.assertFalse(switchback_self_test.run())

    def test_fails_when_inert_publish_reuses_the_baseline_identity(self):
        serialize = DefaultContextEventSerializer.serialize

        def wrong_identity(serializer, event):
            payload = json.loads(serialize(serializer, event))
            for exposure in payload.get('exposures') or []:
                if exposure.get('id') == 527:
                    exposure.update(id=514, name='sb_unit_baseline')
            return bytearray(json.dumps(payload), 'utf-8')

        with mock.patch.object(DefaultContextEventSerializer, 'serialize', wrong_identity):
            self.assertFalse(switchback_self_test.run())


class SwitchbackSignedDecimalsTest(unittest.TestCase):
    def test_both_routes_accept_explicit_positive_signs(self):
        import server
        client = server.app.test_client()
        for route, index, compute in (
                ('assign', 'periodIndex', 'assign'),
                ('permutation', 'cycleIndex', 'permutation')):
            result = 3 if route == 'assign' else [0, 1]
            with mock.patch.object(switchback_self_test, 'assigner', return_value=object()), \
                    mock.patch.object(switchback_self_test, compute, return_value=result) as call:
                response = client.post('/switchback/' + route, json={
                    'clusterUnit': 'market_1', index: '+1', 'seed': '+42', 'armCount': 2})
                self.assertEqual(200, response.status_code)
                self.assertEqual({'result': result, 'events': []}, response.json)
                call.assert_called_once_with('market_1', 1, 42, 2)

    def test_signed_decimal_validation_keeps_its_bounds_and_syntax(self):
        import server
        for value in ('+', '-', '++1', '+-1', ' 1', '1.0',
                      '+9223372036854775808', '-9223372036854775809'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server._switchback_long({'seed': value}, 'seed')
        for value in ('+9223372036854775807', '-9223372036854775808', '+0', '-0'):
            self.assertEqual(int(value), server._switchback_long({'seed': value}, 'seed'))


class PublishReportingTest(unittest.TestCase):

    def test_publish_events_are_reported_as_serialized(self):
        import server

        exposure = Exposure()
        exposure.id = 1
        exposure.name = 'e'
        exposure.unit = 'u'
        exposure.variant = 1
        exposure.exposedAt = 5
        exposure.assigned = True
        exposure.eligible = True
        exposure.overridden = False
        exposure.fullOn = False
        exposure.custom = False
        exposure.audienceMismatch = False
        event = PublishEvent()
        event.hashed = True
        event.publishedAt = 9
        event.exposures = [exposure]
        event.attributes = None
        collector = server.EventCollector()
        collector.handle_event('publish', event)
        self.assertEqual(
            json.loads(DefaultContextEventSerializer().serialize(event)),
            collector.since(0)[0]['data'])
        self.assertNotIn('attributes', collector.since(0)[0]['data'])


if __name__ == '__main__':
    unittest.main()
