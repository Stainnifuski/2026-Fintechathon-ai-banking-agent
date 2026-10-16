"""Install the pinned public offline release; no DB, env, network or Git writes."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tempfile
import zipfile

PACKAGE_NAME = 'wealth-final-offline-20261015-disclosure-v2.zip'
PACKAGE_SHA = '63094dcbefbc4e92745859db095458fa6e29a10019844b17fe4bc41bc8655184'
DATA_VERSION = 'd59ecfcc2ecb09e6573278d5b5f38524407f2ac72ac0051ebf7e7b63f53ff3e1'


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
