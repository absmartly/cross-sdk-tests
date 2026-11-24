"""Run with the wrapper's installed SDK and Flask dependencies."""
import importlib.util
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('pre_ready_wrapper', Path(__file__).with_name('server.py'))
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_gate_defers_provider_until_release():
    data = Future()
    delegate = Mock()
    delegate.get_context_data.return_value = data
    gate = server.GatedContextDataProvider(delegate)
    pending = gate.get_context_data()
    assert not pending.done()
    delegate.get_context_data.assert_not_called()
    gate.release()
    data.set_result('real-data')
    assert pending.result() == 'real-data'
    delegate.get_context_data.assert_called_once()


def test_gate_propagates_error():
    delegate = Mock()
    delegate.get_context_data.side_effect = RuntimeError('fetch failed')
    gate = server.GatedContextDataProvider(delegate)
    pending = gate.get_context_data()
    gate.release()
    assert str(pending.exception()) == 'fetch failed'


def test_validation_is_atomic():
    for value in (None, {}, [None], [{'goalName': ''}], [{'goalName': 'purchase', 'properties': []}]):
        assert server.validate_pre_ready_goals({'mode': 'e2e', 'preReadyGoals': value})
    assert server.validate_pre_ready_goals({'mode': 'e2e', 'preReadyGoals': [{'goalName': 'purchase', 'properties': {'amount': 0.1}}]}) is None
