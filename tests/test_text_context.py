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
    def __init__(self, state):
        self.state = state
        self.invoked = []
    async def close(self):
        pass
    async def discover(self, all=False):
        return [{'device_id': 'ios:fixture', 'native_id': 'fixture', 'backend': 'physical',
                 'connection_epoch': 1, 'ready': True,
                 'capabilities': {'input.text': {'status': 'supported'}}}]
    async def sensitive_state(self, device):
        return {'state': self.state, 'reason': 'Controlled sensitivity test'}
    async def invoke(self, tool, device, arguments, context):
        self.invoked.append((tool, arguments, context.get('sensitive_state')))
        return {'delivery': 'sent', 'verified': False}


@pytest.mark.asyncio
@pytest.mark.parametrize('state,declared,expected', [
    ('protected', True, 'protected_action_requires_human'),
    ('unknown', True, 'prerequisite_required'),
    ('unqualified', False, 'prerequisite_required'),
    ('unqualified', True, 'success'),
    ('clear', False, 'success'),
])
async def test_human_field_declaration_never_overrides_locked_or_unknown_state(tmp_path, state, declared, expected):
    backend = Physical(state)
    router = Router(backend, state_dir=tmp_path)
    args = {'device_id': 'ios:fixture', 'text': 'TapMesh', 'input_method': 'clipboard'}
    if declared:
        args['field_context'] = 'ordinary_app'
    initial = await router.call('owner-test', 'type_text', args)
    assert initial['error']['code'] == 'approval_required'
    assert not backend.invoked
    assert router.policy('type_text', args) == 'S'
    router.approvals.decide(initial['error']['details']['approval_id'], 'allow')
    result = await router.call('owner-test', 'type_text', args)
    if expected == 'success':
        assert result['status'] == 'success'
        assert backend.invoked[0][2]['state'] == state
    else:
        assert result['error']['code'] == expected
        assert not backend.invoked
    await router.close()
