"""Public releases must operate without private history or credentials."""
import json
from pathlib import Path
from scripts.build_public_release import audit


def test_release_audit_detects_private_files_and_never_prints_secret_values(tmp_path):
    secret=b'release-test-credential-unique'
    (tmp_path/'.env').write_bytes(b'API_KEY='+secret)
    (tmp_path/'settings.json').write_bytes(secret)
    (tmp_path/'library.sqlite3').write_bytes(b'private database')
    report=audit(tmp_path,[secret])
    assert any(i['reason']=='private-file' for i in report['issues'])
    assert any(i['reason']=='matches-local-credential' for i in report['issues'])
    assert secret.decode() not in json.dumps(report)


def test_release_audit_accepts_empty_template_and_sources(tmp_path):
    (tmp_path/'.env.example').write_text('API_KEY=\n')
    (tmp_path/'app.py').write_text('print("source")\n')
    assert audit(tmp_path)['issues']==[]


def test_credential_discovery_distinguishes_keys_from_transport_field_names(tmp_path,monkeypatch):
    import scripts.build_public_release as release
    monkeypatch.setattr(release,'ROOT',tmp_path)
    (tmp_path/'.env').write_text('RESEARCH_COPILOT_LLM_API_KEY_ENV=DEEPSEEK_API_KEY\n'
                               'RESEARCH_COPILOT_LLM_TOKEN_LIMIT_FIELD=max_tokens\n'
                               'DEEPSEEK_API_KEY=synthetic-release-secret\n')
    secrets=release.local_secrets()
    assert b'synthetic-release-secret' in secrets
    assert b'DEEPSEEK_API_KEY' not in secrets and b'max_tokens' not in secrets


def test_clean_install_has_empty_optional_annotation_tasks(service,monkeypatch,tmp_path):
    import research_copilot.web as web
    monkeypatch.setattr(web,'ROOT',tmp_path/'public-source')
    app=web.Workbench(service.root)
    result=app.get('/api/tasks',{})
    assert result['tasks']==[] and result['batch']['items']==[]


def test_release_audit_does_not_create_key_for_incomplete_backup(tmp_path,monkeypatch):
    import pytest
    import scripts.build_public_release as release
    monkeypatch.setattr(release,'ROOT',tmp_path)
    data=tmp_path/'data';data.mkdir()
    encrypted=data/'api_settings.enc';encrypted.write_bytes(b'synthetic-encrypted-settings')
    with pytest.raises(ValueError,match='no matching local key'):
        release.local_secrets()
    assert encrypted.read_bytes()==b'synthetic-encrypted-settings'
    assert not (data/'.api_settings.key').exists()


def test_fresh_install_has_no_author_keys_or_papers(tmp_path,monkeypatch):
    from research_copilot.web import Workbench
    from research_copilot.api_settings import APISettings
    root=tmp_path/'clean'/'data';app=Workbench(root)
    monkeypatch.setattr(APISettings,'legacy_llm',lambda self:None)
    monkeypatch.setattr(APISettings,'legacy_translation',lambda self,provider:{})
    assert app.get('/api/library',{})['documents']==[]
    profile=app.get('/api/settings',{})
    assert not any(p['configured'] for kind in ('llm','translation') for p in profile[kind])
    assert app.get('/api/usage',{})['totals']['requests']==0
