import sys
sys.path.insert(0,'orchestrator')
from test_runner import TestOrchestrator,load_capability_policy,load_scenarios
policy=load_capability_policy();scs,_,invalid=load_scenarios('test_scenarios_complete.json',set(policy['capabilities']));assert not invalid,invalid
o=TestOrchestrator({'dart':'http://127.0.0.1:3101','flutter':'http://127.0.0.1:3102'},verbose=False,capability_policy=policy)
working,failed=o.wait_for_services();o.sdks=working
for sc in scs:o.run_scenario(sc)
sys.exit(o.generate_report('test-results/merge-5390f0b/conformance.json',failed))
