import pytest


def test_catalog_is_closed_and_has_all_public_routes():
    from Contracts.catalog import CATALOG, validate
    assert len(CATALOG) == 50
    for tool in CATALOG.values():
        assert tool['inputSchema']['additionalProperties'] is False
        with pytest.raises(Exception):
            validate(tool['name'], {'unrecognized': True})
    validate('tap', {'target': {'x': 1, 'y': 2, 'coordinate_space': 'device_points', 'observation_id': 'obs'}})
    with pytest.raises(Exception):
        validate('tap', {'target': {'x': 1, 'y': 2, 'observation_id': 'obs'}})
    with pytest.raises(Exception):
        validate('start_simulator', {'simulator_id': 'a', 'runtime_id': 'b', 'device_type_id': 'c'})
    with pytest.raises(Exception):
        validate('set_location', {'latitude': float('nan'), 'longitude': 0})


@pytest.mark.parametrize('tool,args', [
    ('wait', {'duration_ms': 120001}),
    ('set_location', {'latitude': 91, 'longitude': 0}),
    ('type_text', {'text': '😀' * 20000}),
    ('batch_commands', {'commands': []}),
    ('set_accessibility', {}),
    ('handle_alert', {'snapshot_id': 's', 'button_ref': 'a', 'button_label': 'b'}),
])
def test_contracts_reject_before_dispatch(tool, args):
    from Contracts.catalog import validate
    with pytest.raises(Exception):
        validate(tool, args)


@pytest.mark.parametrize('tool,args', [
    ('drag',{'start':{'x':1,'y':1,'coordinate_space':'device_points','observation_id':'o'},'end':{'x':2,'y':2,'coordinate_space':'device_points','observation_id':'o'},'hold_ms':6000,'duration_ms':6000}),
    ('install_app',{'artifact_path':'/safe/app\x00.app'}),
])
def test_combined_gesture_duration_and_nul_paths_rejected(tool,args):
    from Contracts.catalog import validate
    with pytest.raises(Exception): validate(tool,args)
