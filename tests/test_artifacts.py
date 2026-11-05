import hashlib
import pytest


def test_artifact_finalizes_hash_and_rejects_untrusted_id(tmp_path):
    from agent.artifacts import Artifacts
    from agent.safety import BridgeError
    a=Artifacts(tmp_path)
    artifact=a.write(b'hello','screenshot','.png','sim:1',2)
    assert artifact['sha256']=='2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'
    assert a.path(artifact['artifact_id']).read_bytes()==b'hello'
    with pytest.raises(BridgeError): a.path('../outside')
    assert Artifacts(tmp_path).path(artifact['artifact_id']).read_bytes()==b'hello'
