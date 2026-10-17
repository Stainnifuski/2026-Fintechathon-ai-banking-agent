"""Install the pinned public offline release; no DB, env, network or Git writes."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tempfile
import zipfile
import shutil
import re

PACKAGE_NAME = 'wealth-compact-900-v1.zip'
PACKAGE_SHA = '03c9ba386834e5536fae6356b99140b78a073e67cccefe7dbb8752225a1cae4c'
DATA_VERSION = '85f35db0a1398dbffbe17f1c6feb6707310d1d009deb357dda709cce33cd6cfa'


def install_release(repo, archive):
    repo,archive=Path(repo).resolve(),Path(archive).resolve()
    if not (repo/'scripts/wealth_portfolio_package.py').is_file():
        raise ValueError('请选择已克隆的银行项目目录，目录中应有scripts/wealth_portfolio_package.py')
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=PACKAGE_SHA:
        raise ValueError('资料包SHA256不匹配：请重新取得指定版本，未写入任何资料')
    binding=json.loads((repo/'config/wealth/portfolio_content_binding_v2.json').read_bytes())
    if binding['data_version']!=DATA_VERSION:
        raise ValueError('源码资料版本与安装包不匹配，请先使用交接文档指定的源码版本')
    sys.path.insert(0,str(repo))
    from scripts.wealth_portfolio_package import install
    with tempfile.TemporaryDirectory(prefix='wealth-offline-') as tmp:
        target=Path(tmp)
        with zipfile.ZipFile(archive) as source:
            names=set()
            for member in source.infolist():
                path=PurePosixPath(member.filename)
                if (path.is_absolute() or '..' in path.parts or '\\' in member.filename
                    or member.filename in names or not (path.parts[:2]==('config','wealth') or path.parts[:1]==('package',))):
                    raise ValueError('资料包路径不合法')
                names.add(member.filename)
                if member.is_dir():continue
                dest=target/Path(*path.parts);dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(source.read(member))
        # Existing verifier checks public config hashes and complete snapshot
        # schema before save. Public config is compared, NEVER overwritten.
        manifest=target/'package'/f'{DATA_VERSION}.package.json'
        result=install(repo,manifest)
    # The active directory contains only the current compact snapshot. Retired
    # immutable inputs are isolated rollback backups, never runtime candidates.
    directory=repo/'.deps/wealth_portfolio_data/snapshots'
    retired=repo/'.deps/upgrade_backups/retired_material'
    for path in directory.iterdir():
        if path.is_file() and re.fullmatch(r'[0-9a-f]{64}(?:\.index)?\.json',path.name) and path.name not in {DATA_VERSION+'.json',DATA_VERSION+'.index.json'}:
            retired.mkdir(parents=True,exist_ok=True)
            dest=retired/path.name
            if dest.exists():
                if hashlib.sha256(dest.read_bytes()).digest()!=hashlib.sha256(path.read_bytes()).digest():
                    raise ValueError('旧资料备份冲突，拒绝覆盖')
                path.unlink()
            else:shutil.move(str(path),str(dest))
    return {'ok':True,'data_version':DATA_VERSION,'snapshot_sha256':result['sha256'],
        'destination':str(repo/'.deps/wealth_portfolio_data/snapshots'),
        'database_modified':False,'configuration_overwritten':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root',required=True,type=Path)
    parser.add_argument('--package',required=True,type=Path)
    args=parser.parse_args()
    try:
        print('正在核验资料包、源码配置和快照，请等待……',flush=True)
        result=install_release(args.project_root,args.package)
        print('固定离线资料安装成功。重复运行会核验并复用同一版本。')
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except ModuleNotFoundError as error:
        print('安装未完成：当前Python缺少项目依赖，请先在该Python环境安装backend/requirements.txt和requirements-market.txt。缺少模块：'+error.name,file=sys.stderr)
        return 1
    except (OSError,ValueError,KeyError,zipfile.BadZipFile) as error:
        print('安装未完成：'+str(error),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
