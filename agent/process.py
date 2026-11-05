"""Bounded argv execution; no host shell, retries or unowned process termination."""
import asyncio
import os
import signal
import sys
import time
from dataclasses import dataclass
from .safety import BridgeError


def minimal_environment(extra=None):
    result={k:os.environ[k] for k in ('HOME','PATH','TMPDIR','DEVELOPER_DIR','LANG') if k in os.environ}
    bundled=os.environ.get('TAPMESH_BUNDLED_RUNTIME')=='1'
    result['PATH']='/usr/bin:/bin:/usr/sbin:/sbin' if bundled else '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'
    if bundled: result['PYTHONDONTWRITEBYTECODE']='1'
    result.update(extra or {})
    return result


@dataclass
class Output:
    stdout: bytes
    stderr: bytes
    returncode: int


class Runner:
    def __init__(self, output_limit=2*1024*1024):
        self.processes=set(); self.output_limit=output_limit
        self._groups={}; self._terminations={}
    def own(self, proc):
        """Register only a freshly spawned session/group leader owned by us."""
        try:
            if os.getpgid(proc.pid)!=proc.pid or os.getsid(proc.pid)!=proc.pid:
                raise BridgeError('backend_failure','Owned process must have a private session and process group')
        except ProcessLookupError:
            # A fast child may already have exited. create_subprocess_exec with
            # start_new_session is the caller's ownership attestation here.
            if proc.returncode is None: raise
        self._groups[proc]=proc.pid
        return proc
    @staticmethod
    async def _darwin_group_has_live_members(group):
        # XNU can return EPERM for an existing group containing only zombies.
        # Inspect numeric metadata only; never infer absence from EPERM alone.
        probe=await asyncio.create_subprocess_exec('/bin/ps','-axo','pid=,pgid=,uid=,stat=',
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
            stdin=asyncio.subprocess.DEVNULL)
        try:
            output,_=await asyncio.wait_for(probe.communicate(),2)
            if probe.returncode or len(output)>1024*1024:
                raise BridgeError('backend_failure','Could not verify owned process-group cleanup')
            for line in output.decode('ascii','strict').splitlines():
                fields=line.split()
                if len(fields)!=4 or not all(x.isdecimal() for x in fields[:3]):
                    raise BridgeError('backend_failure','Invalid process-group inspection metadata')
                if int(fields[1])==group and not fields[3].startswith('Z'):
                    return True
            return False
        finally:
            if probe.returncode is None:
                probe.kill()
                await probe.wait()
    @classmethod
    async def _group_exists(cls,group):
        try: os.killpg(group,0); return True
        except ProcessLookupError: return False
        except PermissionError:
            if sys.platform=='darwin' and not await cls._darwin_group_has_live_members(group):
                return False
            raise
    async def _signal_owned_group(self,group,sig):
        try: os.killpg(group,sig)
        except ProcessLookupError: pass
        except PermissionError:
            if await self._group_exists(group): raise
    async def _terminate_owned(self, proc, group, sig):
        # Never derive a group from a PID supplied to terminate(). Only groups
        # explicitly registered at our start_new_session spawn seam are eligible.
        try:
            await self._signal_owned_group(group,sig)
            until=time.monotonic()+3
            while await self._group_exists(group) and time.monotonic()<until:
                await asyncio.sleep(.02)
            if await self._group_exists(group):
                await self._signal_owned_group(group,signal.SIGKILL)
            await asyncio.wait_for(proc.wait(),3)
        finally: self._groups.pop(proc,None)
    async def terminate(self, proc, sig=signal.SIGTERM):
        task=self._terminations.get(proc)
        if task is None:
            group=self._groups.get(proc)
            if group is None: return
            task=asyncio.create_task(self._terminate_owned(proc,group,sig))
            self._terminations[proc]=task
            task.add_done_callback(lambda completed: self._terminations.pop(proc,None))
        try: await asyncio.shield(task)
        finally:
            if task.done(): self._terminations.pop(proc,None)
    async def run(self, argv, *, timeout=30, input=None, env=None, mutation=False, check=True, cwd=None):
        try:
            proc=await asyncio.create_subprocess_exec(*map(str,argv), stdin=asyncio.subprocess.PIPE if input is not None else asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True,env=minimal_environment(env),cwd=cwd)
        except FileNotFoundError as exc: raise BridgeError('prerequisite_required','Required backend executable is unavailable') from exc
        self.own(proc); self.processes.add(proc)
        async def read(stream):
            output=bytearray()
            while chunk:=await stream.read(65536):
                output.extend(chunk)
                if len(output)>self.output_limit: raise BridgeError('backend_failure','Backend output exceeded the configured bound', side_effect_state='unknown' if mutation else 'not_sent')
            return bytes(output)
        reads=[asyncio.create_task(read(proc.stdout)),asyncio.create_task(read(proc.stderr))]
        async def communicate():
            if input is not None:
                try: proc.stdin.write(input); await proc.stdin.drain()
                except (BrokenPipeError,ConnectionResetError): pass
                finally: proc.stdin.close()
            out,err=await asyncio.gather(*reads); await proc.wait()
            return Output(out,err,proc.returncode)
        try:
            result=await asyncio.wait_for(communicate(),timeout)
            if check and result.returncode:
                raise BridgeError('delivery_outcome_unknown' if mutation else 'backend_failure','Backend command did not confirm completion; inspect device state before retrying' if mutation else 'Backend command failed; check prerequisites',side_effect_state='unknown' if mutation else 'not_sent',details={'exit_code':result.returncode,'executable':os.path.basename(str(argv[0]))})
            return result
        except TimeoutError as exc:
            raise BridgeError('delivery_outcome_unknown' if mutation else 'deadline_exceeded','Backend deadline exceeded',side_effect_state='unknown' if mutation else 'not_sent') from exc
        finally:
            async def cleanup():
                try: await self.terminate(proc)
                finally:
                    for task in reads:
                        if not task.done(): task.cancel()
                    await asyncio.gather(*reads,return_exceptions=True)
                    self.processes.discard(proc)
            cleanup_task=asyncio.create_task(cleanup()); cancelled=False
            while True:
                try: await asyncio.shield(cleanup_task); break
                except asyncio.CancelledError: cancelled=True
            if cancelled: raise asyncio.CancelledError
    async def close(self):
        # Independent groups must settle together within the app's shutdown
        # budget; sequential TERM grace periods can orphan later groups.
        outcomes=await asyncio.gather(*(self.terminate(proc) for proc in list(self._groups)),return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome,BaseException): raise outcome
