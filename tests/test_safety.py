import asyncio
from pathlib import Path
import pytest


def test_path_grants_reject_traversal_and_symlink_escape(tmp_path):
    from agent.safety import PathGrants, BridgeError
    allowed = tmp_path/'allowed'; allowed.mkdir()
    outside = tmp_path/'outside'; outside.mkdir()
    (allowed/'escape').symlink_to(outside, target_is_directory=True)
    paths = PathGrants([allowed])
    assert paths.check(allowed/'new.png', exists=False) == allowed/'new.png'
    for bad in [allowed/'..'/'outside'/'a', allowed/'escape'/'a', outside/'a']:
        with pytest.raises(BridgeError): paths.check(bad, exists=False)


def test_observations_bind_session_epoch_and_geometry():
    from agent.safety import Observations, BridgeError
    obs = Observations(max_age=30)
    issued = obs.issue('a', 'sim:1', 1, {'width':100, 'height':200}, (200,400), 'simctl')
    target = {'x':10,'y':20,'coordinate_space':'device_points','observation_id':issued['observation_id']}
    assert obs.resolve('a','sim:1',1,target)[0:2] == (10,20)
    with pytest.raises(BridgeError): obs.resolve('b','sim:1',1,target)
    with pytest.raises(BridgeError): obs.resolve('a','sim:1',2,target)
    obs.invalidate('sim:1')
    with pytest.raises(BridgeError): obs.resolve('a','sim:1',1,target)


def test_policy_digest_cannot_cross_session_epoch_or_arguments():
    from agent.safety import Approvals, BridgeError
    approvals = Approvals(ttl=20)
    action = ('a','sim:1',1,'uninstall_app',{'bundle_id':'com.example.fixture'})
    with pytest.raises(BridgeError) as pending: approvals.require(*action, policy='D')
    aid = pending.value.details['approval_id']
    approvals.decide(aid,'allow')
    approvals.require(*action,policy='D')
    for changed in [('b', *action[1:]), ('a','sim:1',2,*action[3:]), (*action[:4],{'bundle_id':'com.other.app'})]:
        with pytest.raises(BridgeError): approvals.require(*changed,policy='D')


async def test_process_cancellation_reaps_owned_child(tmp_path):
    from agent.process import Runner
    import sys
    runner=Runner()
    task=asyncio.create_task(runner.run([sys.executable,'-c','import time; time.sleep(60)'], timeout=100))
    await asyncio.sleep(.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert not runner.processes


def test_transform_uses_explicit_affine_top_left_mapping():
    from agent.safety import Observations
    data=Observations().issue('s','sim:1',1,{'width':402,'height':874},(1206,2622),'simctl')
    assert data['image_to_device_transform']=={'a':1/3,'b':0,'c':0,'d':1/3,'tx':0,'ty':0}
