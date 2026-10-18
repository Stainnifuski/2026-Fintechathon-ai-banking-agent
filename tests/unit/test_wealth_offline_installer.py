"""Installer refusal checks; no database or large private cache required."""
import hashlib
import json
from pathlib import Path
import pytest
from scripts import install_wealth_offline as installer


def project(tmp_path):
    (tmp_path/'scripts').mkdir()
    (tmp_path/'scripts/wealth_portfolio_package.py').write_text('# expected source marker')
    return tmp_path


def test_wrong_archive_is_rejected_before_any_snapshot_write(tmp_path):
    root=project(tmp_path);archive=tmp_path/'wrong.zip';archive.write_bytes(b'not the pinned release')
    with pytest.raises(ValueError,match='SHA256'):installer.install_release(root,archive)
    assert not (root/'.deps').exists()


def test_project_root_must_contain_existing_package_installer(tmp_path):
    with pytest.raises(ValueError,match='请选择'):installer.install_release(tmp_path,tmp_path/'missing.zip')
    assert not (tmp_path/'.deps').exists()


def test_different_source_version_refuses_before_extraction(tmp_path,monkeypatch):
    root=project(tmp_path);archive=tmp_path/'fixture.zip';archive.write_bytes(b'version gate only')
    monkeypatch.setattr(installer,'PACKAGE_SHA',hashlib.sha256(archive.read_bytes()).hexdigest())
    path=root/'config/wealth';path.mkdir(parents=True)
    (path/'portfolio_content_binding_v2.json').write_text(json.dumps({'data_version':'a'*64}))
    with pytest.raises(ValueError,match='源码资料版本'):installer.install_release(root,archive)
    assert not (root/'.deps').exists()


def test_pinned_source_manifest_matches_installer():
    root=Path(__file__).resolve().parents[2]
    binding=json.loads((root/'config/wealth/portfolio_content_binding_v2.json').read_bytes())
    assert binding['data_version']==installer.DATA_VERSION


def test_shipped_compact_package_installs_twice_and_has_exact_selected_codes(tmp_path):
    import shutil,zipfile
    root=Path(__file__).resolve().parents[2]
    target=tmp_path/'deployment'
    target.mkdir();project(target)
    shutil.copyfile(root/'scripts/wealth_portfolio_package.py',target/'scripts/wealth_portfolio_package.py')
    archive=root/'data/wealth'/installer.PACKAGE_NAME
    with zipfile.ZipFile(archive) as source:
        for name in source.namelist():
            if name.startswith('config/wealth/'):
                dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(source.read(name))
    old=target/'.deps/wealth_portfolio_data/snapshots';old.mkdir(parents=True)
    (old/('a'*64+'.json')).write_text('retired snapshot')
    first=installer.install_release(target,archive);again=installer.install_release(target,archive)
    assert first['snapshot_sha256']==again['snapshot_sha256']
    assert (target/'.deps/upgrade_backups/retired_material'/('a'*64+'.json')).exists()
    snapshot=json.loads((old/(installer.DATA_VERSION+'.json')).read_bytes())
    selected=json.loads((root/'config/wealth/compact_pool.json').read_bytes())
    codes={p['product_id'] for p in snapshot['products'] if p['source_kind']=='real'}
    assert codes==set(selected['codes']) and len(codes)==selected['count']
    assert len(list(old.glob('*.json')))==3
