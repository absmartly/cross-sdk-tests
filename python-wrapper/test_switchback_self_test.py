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
