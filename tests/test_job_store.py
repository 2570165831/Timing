import json
import os

import pytest

from timing import job_store

IDENTIFIER = '0123456789abcdef0123456789abcdef'


def setup_folder(tmp_path):
    folder = tmp_path / IDENTIFIER
    folder.mkdir()
    original = {'id':IDENTIFIER, 'state':'running', 'log':['old']}
    (folder/'job.json').write_text(json.dumps(original), encoding='utf-8')
    return folder, original


def test_short_lock_is_retried_without_damaging_primary(tmp_path, monkeypatch):
    folder, job = setup_folder(tmp_path)
    replace = job_store.os.replace
    attempts = []
    def locked(source, target):
        attempts.append(str(source))
        if len(attempts) < 3:
            assert json.loads((folder/'job.json').read_text())['log'] == ['old']
            raise PermissionError('temporary lock')
        replace(source, target)
    monkeypatch.setattr(job_store.os, 'replace', locked)
    monkeypatch.setattr(job_store.time, 'sleep', lambda _:None)
    job['state'] = 'complete'
    assert job_store.save_job(folder, job) == 'primary'
    # The real replace can also meet a brief Windows scanner lock after our
    # two injected failures; keep the bounded-retry and same-snapshot checks.
    assert 3 <= len(attempts) <= len(job_store.RETRY_DELAYS)
    assert len(set(attempts)) == 1
    assert job_store.load_job(folder)['state'] == 'complete'
    assert not list(folder.glob('*.tmp'))


def test_persistent_primary_lock_uses_new_checkpoint_then_recovers(tmp_path, monkeypatch):
    folder, job = setup_folder(tmp_path)
    replace = job_store.os.replace
    def locked(source, target):
        if target.name == 'job.json':
            raise PermissionError('held by another reader')
        replace(source, target)
    monkeypatch.setattr(job_store.os, 'replace', locked)
    monkeypatch.setattr(job_store.time, 'sleep', lambda _:None)
    job['state'] = 'complete'
    assert job_store.save_job(folder, job) == 'checkpoint'
    assert json.loads((folder/'job.json').read_text())['state'] == 'running'
    assert job_store.load_job(folder)['state'] == 'complete'
    (folder/'job.state.corrupt.json').write_text('{partial', encoding='utf-8')
    assert job_store.load_job(folder)['state'] == 'complete'
    monkeypatch.setattr(job_store.os, 'replace', replace)
    assert job_store.save_job(folder, job) == 'primary'
    assert not list(folder.glob('job.state.*.json'))


def test_progress_save_failure_cannot_fail_an_otherwise_successful_job(tmp_path, monkeypatch):
    from timing import server
    folder, job = setup_folder(tmp_path)
    monkeypatch.setattr(server, 'DATA', tmp_path)
    monkeypatch.setattr(server, 'jobs', {IDENTIFIER:job})
    def unavailable(*args):
        raise PermissionError('unavailable filesystem')
    monkeypatch.setattr(server, 'save_job', unavailable)
    def run(*args, **kwargs):
        args[6]('calculation completed')
        return {'status':'complete', 'units':7}
    monkeypatch.setattr(server, 'run', run)
    server.process(IDENTIFIER, folder/'a.wav', 'hi', 'en', True, None, False, False)
    assert server.get_job(IDENTIFIER)['state'] == 'complete'
    assert server.get_job(IDENTIFIER)['result']['units'] == 7
    assert 'persistence_notice' in server.get_job(IDENTIFIER)


def test_restart_reads_terminal_checkpoint_instead_of_old_running_state(tmp_path, monkeypatch):
    from timing import server
    folder, job = setup_folder(tmp_path)
    job.update(state='complete', _state_revision=2)
    (folder/'job.state.saved.json').write_text(json.dumps(job), encoding='utf-8')
    monkeypatch.setattr(server, 'DATA', tmp_path)
    monkeypatch.setattr(server, 'jobs', {})
    assert server.get_job(IDENTIFIER)['state'] == 'complete'


@pytest.mark.skipif(os.name != 'nt', reason='Windows file sharing semantics')
def test_actual_windows_exclusive_reader_does_not_lose_completed_state(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes
    folder, job = setup_folder(tmp_path)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
                                  wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(folder/'job.json'), 0x80000000, 0, None, 3, 0x80, None)
    assert handle != wintypes.HANDLE(-1).value
    monkeypatch.setattr(job_store.time, 'sleep', lambda _:None)
    try:
        job['state'] = 'complete'
        assert job_store.save_job(folder, job) == 'checkpoint'
        assert job_store.load_job(folder)['state'] == 'complete'
    finally:
        kernel.CloseHandle(handle)
    assert job_store.save_job(folder, job) == 'primary'
    assert job_store.load_job(folder)['state'] == 'complete'
