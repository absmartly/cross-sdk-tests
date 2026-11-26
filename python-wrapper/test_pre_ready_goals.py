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


def test_route_tracks_real_sdk_before_ready_then_publishes(monkeypatch):
    from sdk.json.context_data import ContextData
    from sdk.default_context_data_provider import DefaultContextDataProvider
    from sdk.default_context_publisher import DefaultContextPublisher
    from sdk.context import Context
    payloads = []
    readiness = []
    original_track = Context.track
    def track(self, name, properties=None):
        readiness.append(self.is_ready())
        return original_track(self, name, properties)
    def fetch(self):
        future = Future()
        data = ContextData()
        data.experiments = []
        future.set_result(data)
        return future
    def publish(self, context, event):
        payloads.append(event)
        future = Future()
        future.set_result(None)
        return future
    monkeypatch.setattr(Context, 'track', track)
    monkeypatch.setattr(DefaultContextDataProvider, 'get_context_data', fetch)
    monkeypatch.setattr(DefaultContextPublisher, 'publish', publish)
    for key,value in dict(ENDPOINT='https://collector.invalid/v1',API_KEY='test',APPLICATION='e2e-tests',ENVIRONMENT='prod').items():
        monkeypatch.setenv('ABSMARTLY_E2E_'+key,value)
    client=server.app.test_client()
    response=client.post('/context',json={'mode':'e2e','units':{'user_id':'test'},
                         'preReadyGoals':[{'goalName':'purchase','properties':{'amount':0.1}},
                                          {'goalName':'purchase','properties':{'gift':True}}]})
    assert response.status_code==200, response.json
    assert response.json['result']['preReadyTracked'] is True
    assert readiness==[False,False]
    assert payloads==[]
    cid=response.json['result']['contextId']
    response=client.post('/context/'+cid+'/publish')
    assert response.status_code==200,response.json
    assert len(payloads)==1
    assert [g.name for g in payloads[0].goals]==['purchase','purchase']
    assert [g.properties for g in payloads[0].goals]==[{'amount':0.1},{'gift':True}]
