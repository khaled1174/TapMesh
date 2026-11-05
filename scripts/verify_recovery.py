#!/usr/bin/env python3
"""Rehearse failed owned deployment rollback; briefly interrupts TapMesh only."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import manage


def main():
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'checks': []}
    with manage.operation_lock():
        previous = manage.current_container()
        if not previous or not previous['State']['Running'] or previous['State'].get('Health', {}).get('Status') != 'healthy':
            raise RuntimeError('A verified healthy owned release is required before recovery qualification')
        original = manage.remote_command
        injected = False

        def fault(argv, **kwargs):
            nonlocal injected
            if argv[:2] == ['docker', 'build']:
                # Reuse the exact known-good image; inject no new source/image.
                tag = argv[argv.index('-t') + 1]
                return original(['docker', 'tag', previous['Image'], tag], **kwargs)
            if argv[:2] == ['docker', 'run']:
                argv = [*argv[:-1], '--entrypoint', '/bin/false', argv[-1]]
                injected = True
            return original(argv, **kwargs)

        started = time.monotonic()
        manage.remote_command = fault
        failed = False
        try:
            manage.deploy()
        except RuntimeError as error:
            if str(error) != 'Deployment failed; previous release restored when present.':
                raise
            failed = True
        finally:
            manage.remote_command = original
        if not injected or not failed:
            raise RuntimeError('The deliberate failed-candidate scenario was not exercised')
        restored = manage.current_container()
        if not restored or restored['Id'] != previous['Id'] or restored['Image'] != previous['Image']:
            raise RuntimeError('The exact previous owned release was not restored')
        manage.wait_container_healthy(previous['Id'])
        report['checks'].append({'name': 'actual failed candidate restores exact prior container and image', 'status': 'passed', 'elapsed_seconds': round(time.monotonic() - started, 2)})
        report['status'] = 'passed'
    path = Path(__file__).resolve().parents[1] / 'docs/RECOVERY_QUALIFICATION.json'
    path.write_text(json.dumps(report, indent=2) + '\n')
    print('Report:', path)


if __name__ == '__main__':
    main()
