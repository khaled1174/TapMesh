import json
from pathlib import Path
from Contracts import __version__
from Contracts.catalog import public_catalog


def test_generated_catalog_cannot_drift_from_http_mcp_and_native_schema():
    path = Path(__file__).resolve().parents[1] / 'Contracts/catalog.json'
    assert json.loads(path.read_text()) == {'version': __version__, 'tools': public_catalog()}
