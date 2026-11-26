import pytest
from Contracts.catalog import validate, ContractError
from agent.core import Router


def test_text_context_and_layout_are_closed_declarations():
    validate('type_text', {'text': 'TapMesh', 'input_method': 'keyboard',
                           'keyboard_layout': 'us', 'field_context': 'ordinary_app'})
    validate('type_text', {'text': 'تجربة', 'input_method': 'clipboard', 'field_context': 'ordinary_app'})
    for invalid in ({'keyboard_layout': 'ar'}, {'field_context': 'device_unlock'},
                    {'input_method': 'clipboard', 'keyboard_layout': 'us'}):
        with pytest.raises(ContractError):
            validate('type_text', {'text': 'TapMesh', **invalid})


def test_text_target_validation_is_explicit_ordinary_and_non_submitting():
    target={'x':10,'y':20,'coordinate_space':'device_points','observation_id':'owned'}
    base={'target':target,'text':'TapMesh','frame_validation':'text_target','field_context':'ordinary_app'}
    validate('fill',base)
    for changes in [{'submit':True},{'field_context':None},{'frame_validation':'ignore_changes'}]:
        with pytest.raises(ContractError):validate('fill',{**base,**changes})
    del base['field_context']
    with pytest.raises(ContractError):validate('fill',base)
    with pytest.raises(ContractError):validate('type_text',{'text':'TapMesh','frame_validation':'text_target'})


class Physical:
    def __init__(self):
        self.invoked = []
    async def close(self):
        pass
    async def discover(self, all=False):
        return [{'device_id': 'ios:fixture', 'native_id': 'fixture', 'backend': 'physical',
                 'connection_epoch': 1, 'ready': True,
                 'capabilities': {'input.text': {'status': 'supported'}}}]
    async def sensitive_state(self, device):
        raise AssertionError('Physical text entry has no accessibility preflight')
    async def invoke(self, tool, device, arguments, context):
        self.invoked.append((tool, arguments))
        return {'delivery': 'sent', 'verified': False}


@pytest.mark.asyncio
@pytest.mark.parametrize('declared', [True, False])
async def test_physical_text_entry_runs_without_accessibility_preflight(tmp_path, declared):
    backend = Physical()
    router = Router(backend, state_dir=tmp_path)
    args = {'device_id': 'ios:fixture', 'text': 'TapMesh', 'input_method': 'clipboard'}
    if declared:
        args['field_context'] = 'ordinary_app'
    assert router.policy('type_text', args) == 'S'
    result = await router.call('owner-test', 'type_text', args)
    if result['status'] == 'error' and result['error']['code'] == 'approval_required':
        assert not backend.invoked
        router.approvals.decide(result['error']['details']['approval_id'], 'allow')
        result = await router.call('owner-test', 'type_text', args)
    assert result['status'] == 'success'
    assert backend.invoked == [('type_text', args)]
    await router.close()
