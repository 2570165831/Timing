"""Explicit pinned MMS-300M setup download; never called during inference."""
import json,os,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from download_support import atomic_write_json,error_tail,file_matches,sha256_file
from timing.config import MODELS
REPO='MahmoudAshraf/mms-300m-1130-forced-aligner'
REVISION='49402e9577b1158620820667c218cd494cc44486'
FOLDER=MODELS/'ctc'
CHECKPOINT_SHA='9aa5229a1af4d7714285cfa14bcaab6af2a39a9e810b475722b24975d5cb1dab'
REQUIRED=('config.json','model.safetensors','preprocessor_config.json','vocab.json','manifest.json')


def _manifest_ok():
    if not all((FOLDER/name).is_file() for name in REQUIRED):return False
    try:manifest=json.loads((FOLDER/'manifest.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):return False
    if manifest.get('repo')!=REPO or manifest.get('revision')!=REVISION:return False
    files=manifest.get('files')
    if not isinstance(files,dict):return False
    for name,asset in files.items():
        if not isinstance(asset,dict) or not file_matches(FOLDER/name,sha256=asset.get('sha256'),size=asset.get('bytes')):
            return False
    return files.get('model.safetensors',{}).get('sha256')==CHECKPOINT_SHA


def _package_ok():
    code=("import sys; from transformers import AutoConfig, AutoFeatureExtractor; "
          "AutoConfig.from_pretrained(sys.argv[1],local_files_only=True); "
          "AutoFeatureExtractor.from_pretrained(sys.argv[1],local_files_only=True)")
    env=os.environ.copy();env.update(PYTHONUTF8='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    result=subprocess.run([sys.executable,'-X','utf8','-c',code,str(FOLDER)],capture_output=True,
                          text=True,encoding='utf-8',errors='replace',timeout=120,env=env)
    return result.returncode==0,result.stderr[-800:]

def main():
    if not _manifest_ok():
        from huggingface_hub import snapshot_download
        # Repair only files that are present but known corrupt. Hugging Face
        # otherwise reuses its verified local cache and resumes incomplete ones.
        repair=set()
        if (FOLDER/'model.safetensors').is_file() and not file_matches(FOLDER/'model.safetensors',sha256=CHECKPOINT_SHA):
            repair.add('model.safetensors')
        manifest_path=FOLDER/'manifest.json'
        if manifest_path.is_file():
            try:
                stale=json.loads(manifest_path.read_text(encoding='utf-8')).get('files',{})
            except (OSError,ValueError):
                stale={}
            for name,asset in stale.items() if isinstance(stale,dict) else ():
                if isinstance(asset,dict) and (FOLDER/name).is_file() and not file_matches(
                        FOLDER/name,sha256=asset.get('sha256'),size=asset.get('bytes')):
                    repair.add(name)
        if repair:
            print('MMS 本地缓存中有损坏文件，仅强制修复：'+'、'.join(sorted(repair)),flush=True)
            snapshot_download(REPO,revision=REVISION,local_dir=FOLDER,
                              allow_patterns=sorted(repair),force_download=True)
        snapshot_download(REPO,revision=REVISION,local_dir=FOLDER,
                          allow_patterns=['*.json','model.safetensors','README.md'])
        files={}
        for path in FOLDER.iterdir():
            if path.is_file() and path.name != 'manifest.json':
                files[path.name]={'sha256':sha256_file(path),'bytes':path.stat().st_size}
        if files.get('model.safetensors',{}).get('sha256') != CHECKPOINT_SHA:
            raise RuntimeError('MMS 权重 SHA-256 校验失败，请重新运行 scripts/download_ctc.py。')
        atomic_write_json(FOLDER/'manifest.json',{'repo':REPO,'revision':REVISION,'files':files})
    compatible,detail=_package_ok()
    if not compatible:
        raise RuntimeError('MMS 本地配置与当前 Transformers 不兼容：'+error_tail(detail))
    if not _manifest_ok():
        raise RuntimeError('MMS 文件完整性校验失败，请重新运行 scripts/download_ctc.py。')
    print('MMS-300M 文件与本地配置校验通过。',flush=True)
    print(json.dumps(json.loads((FOLDER/'manifest.json').read_text(encoding='utf-8'))['files'],indent=2),flush=True)

if __name__=='__main__':main()
