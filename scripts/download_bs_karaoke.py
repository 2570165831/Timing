"""Pinned public BS-Karaoke assets, explicit setup downloads only."""
import json,os,subprocess,sys,tempfile
from pathlib import Path
from huggingface_hub import hf_hub_download

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'scripts'))
from download_support import atomic_write_json,error_tail,file_matches,sha256_file
FOLDER=ROOT/'models/bs-karaoke'
REVISION='f7849ae934209184dc288d1018cd8a76a7fc8b3c'
RUNTIME='6fdad13686bb7e84d029ec66f9dbe76e65c93ebf'
FILES={'bs_roformer_karaoke_frazer_becruily.ckpt':'eb90ee24c1154d83fbcfd27e96182f19e061557cc6e4746953125e08c29389f9',
       'config_karaoke_frazer_becruily.yaml':'1d3b58b473025183d0d3c91e7a444cb5f1418d9f34e8f46b8d2fb10d2cc8ab34'}


def _manifest_ok():
    path=FOLDER/'assets.json'
    if not path.is_file():return False
    try:data=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return False
    if data.get('revision')!=REVISION or data.get('runtime_revision')!=RUNTIME:return False
    assets=data.get('assets')
    if not isinstance(assets,dict):return False
    if any(not file_matches(FOLDER/name,sha256=sha) for name,sha in FILES.items()):return False
    for name,asset in assets.items():
        if not isinstance(asset,dict):return False
        if not isinstance(asset.get('sha256'),str) or len(asset['sha256'])!=64:return False
        if not file_matches(FOLDER/name,sha256=asset.get('sha256'),size=asset.get('bytes')):
            return False
    return all((FOLDER/name).is_file() for name in (
        *FILES,'runtime/models/bs_roformer/bs_roformer.py','runtime/models/bs_roformer/attend.py'))


def _load_config(path):
    import yaml
    return yaml.load(Path(path).read_text(encoding='utf-8'),Loader=yaml.FullLoader)


def _package_ok():
    code=("import sys; from pathlib import Path; "
          "sys.path[:0]=[sys.argv[2],str(Path(sys.argv[1])/'runtime')]; "
          "from models.bs_roformer.bs_roformer import BSRoformer; "
          "from download_bs_karaoke import _load_config; "
          "_load_config(Path(sys.argv[1],sys.argv[3]))")
    env=os.environ.copy();env.update(PYTHONUTF8='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    result=subprocess.run([sys.executable,'-X','utf8','-c',code,str(FOLDER),
                           str(ROOT/'scripts'),'config_karaoke_frazer_becruily.yaml'],capture_output=True,text=True,
                          encoding='utf-8',errors='replace',timeout=120,env=env)
    return result.returncode==0,result.stderr[-800:]


def main():
    if not _manifest_ok():
        FOLDER.mkdir(parents=True,exist_ok=True)
        repair={name for name,sha in FILES.items()
                if (FOLDER/name).is_file() and not file_matches(FOLDER/name,sha256=sha)}
        for name,sha in FILES.items():
            # Only a present, known-corrupt file is forced. Missing files use
            # Hugging Face's official resumable incomplete-cache handling.
            path=Path(hf_hub_download('becruily/bs-roformer-karaoke',name,revision=REVISION,
                                      local_dir=FOLDER,force_download=name in repair))
            if not file_matches(path,sha256=sha):raise ValueError('BS 模型 SHA-256 校验失败：'+name)
        with tempfile.TemporaryDirectory(prefix='timing-bs-') as temporary:
            subprocess.run(['git','init',temporary],check=True,capture_output=True)
            subprocess.run(['git','-C',temporary,'fetch','--depth','1','https://github.com/ZFTurbo/Music-Source-Separation-Training.git',RUNTIME],check=True)
            for name in ['models/bs_roformer/bs_roformer.py','models/bs_roformer/attend.py','LICENSE']:
                content=subprocess.run(['git','-C',temporary,'show',f'FETCH_HEAD:{name}'],check=True,capture_output=True).stdout
                target=FOLDER/'runtime'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
        for name in ['models/__init__.py','models/bs_roformer/__init__.py']:
            (FOLDER/'runtime'/name).write_text('')
        paths=[FOLDER/name for name in FILES]+[p for p in (FOLDER/'runtime').rglob('*') if p.is_file()]
        assets={str(p.relative_to(FOLDER)).replace('\\','/'):{'sha256':sha256_file(p),'bytes':p.stat().st_size} for p in paths}
        atomic_write_json(FOLDER/'assets.json',{'repository':'becruily/bs-roformer-karaoke','revision':REVISION,
            'runtime_repository':'ZFTurbo/Music-Source-Separation-Training','runtime_revision':RUNTIME,'assets':assets})
    compatible,detail=_package_ok()
    if not compatible:
        raise RuntimeError('BS-Karaoke 运行库依赖导入失败：'+error_tail(detail))
    if not _manifest_ok():
        raise RuntimeError('BS-Karaoke 文件完整性校验失败，请重新运行 scripts/download_bs_karaoke.py。')
    print('BS-Karaoke 文件与运行依赖校验通过。',flush=True)


if __name__=='__main__':main()
