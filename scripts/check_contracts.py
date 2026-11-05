#!/usr/bin/env python3
"""Verify the generated public catalog against the single schema authority."""
from pathlib import Path
import argparse
import json
import sys
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from Contracts import __version__
from Contracts.catalog import public_catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    tools = public_catalog()
    if len(tools) != 50 or len({tool['name'] for tool in tools}) != 50:
        raise SystemExit('Public tool count or uniqueness drifted from the 50-tool contract')
    for tool in tools:
        Draft202012Validator.check_schema(tool['inputSchema'])
        if tool['inputSchema'].get('additionalProperties') is not False:
            raise SystemExit('Tool input objects must use closed schemas: ' + tool['name'])
    expected = {'version': __version__, 'tools': tools}
    path = ROOT / 'Contracts/catalog.json'
    if args.write:
        path.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + '\n')
    if json.loads(path.read_text()) != expected:
        raise SystemExit('Generated catalog drift: run scripts/check_contracts.py --write and review the change')
    print('50 tool schemas validated; generated catalog matches registration authority.')


if __name__ == '__main__':
    main()
