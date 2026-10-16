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
