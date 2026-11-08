"""Build an allowlisted source distribution without local credentials or research data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
PUBLIC_FILES=['README.md','README.zh-CN.md','LICENSE','THIRD_PARTY_NOTICES.md','CONTRIBUTING.md','SECURITY.md',
              '.gitignore','.env.example','requirements.txt','requirements-dev.txt','requirements-translation.txt','pytest.ini','app.py',
              '.streamlit/config.toml','.github/workflows/tests.yml',
              'docs/INSTALLATION.md','docs/PROVIDERS.md','docs/ARCHITECTURE.md','docs/PUBLISHING.md',
              'config/translation_providers.env.example','config/baidu_translation.env.example','config/google_translation.env.example',
              'scripts/build_public_release.py','scripts/reading_mock.py','scripts/prepare_reader_assets.py','scripts/install_local_translation.py']
PUBLIC_TESTS=['test_accounts_api.py','test_aliyun_network.py','test_aliyun_reading.py','test_aliyun_translation.py',
              'test_baidu_translation.py','test_chinese_reading.py','test_comparison.py','test_end_to_end_safety.py',
              'test_google_translation.py','test_import_and_retrieval.py','test_llm_transport.py','test_local_chinese_reading.py',
              'test_qa.py','test_qa_quality.py','test_reading.py','test_reading_integration.py','test_research_structure.py',
              'test_source_audit.py','test_source_reading.py','test_translation_providers.py','test_workbench.py',
              'test_public_release.py','conftest.py']
FORBIDDEN_PARTS={'data','input_papers','private','.git','.venv','.venv-translation','__pycache__','.pytest_cache','verification','research','poc','prototype'}
FORBIDDEN_SUFFIXES={'.pdf','.sqlite','.sqlite3','.db','.enc','.pem','.key','.p12','.pfx','.log','.pyc','.zip'}
SECRET_NAME=re.compile(r'(?:API.?KEY|ACCESS.?KEY|SECRET|PASSWORD|TOKEN)',re.I)
NON_SECRET_FIELDS={'RESEARCH_COPILOT_LLM_API_KEY_ENV','RESEARCH_COPILOT_LLM_TOKEN_LIMIT_FIELD'}
PATTERNS=[('private-key',re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')),
          ('provider-key',re.compile(rb'\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{32,}|LTAI[A-Za-z0-9]{16,}|AKIA[A-Z0-9]{16})\b'))]


def selected_files():
    selected=set(PUBLIC_FILES)
    for folder,suffixes in [('research_copilot',{'.py','.sql'}),('evaluation',{'.py'})]:
        selected.update(p.relative_to(ROOT).as_posix() for p in (ROOT/folder).glob('*') if p.is_file() and p.suffix in suffixes)
    selected.update('tests/'+name for name in PUBLIC_TESTS)
    selected.update(p.relative_to(ROOT).as_posix() for p in (ROOT/'tests').glob('*.test.mjs'))
    selected.update(p.relative_to(ROOT).as_posix() for p in (ROOT/'web').rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    selected.update('evaluation/'+name for name in ('annotation_schema.json','case_schema.json'))
    selected.update(p.relative_to(ROOT).as_posix() for p in (ROOT/'docs').glob('reading_*contract*.json'))
    selected.add('docs/reading_card_schema_v1.json')
    return sorted(selected)


def local_secrets():
    """Return values only in memory; never print or write matching secret bytes."""
    found=set()
    for path in ROOT.glob('.env*'):
        if path.name.endswith('.example') or not path.is_file():continue
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            key,sep,value=line.partition('=')
            value=value.strip().strip('\"\'')
            # Environment-variable references and request-field names are not keys.
            if sep and not key.lstrip().startswith('#') and key.strip() not in NON_SECRET_FIELDS and SECRET_NAME.search(key) and len(value)>=8:
                found.add(value.encode())
    for key,value in os.environ.items():
        if key not in NON_SECRET_FIELDS and SECRET_NAME.search(key) and len(value)>=12:found.add(value.encode())
    configs=list((ROOT/'data').glob('api_settings.enc'))+list((ROOT/'data/users').glob('*/api_settings.enc'))
    if configs:
        sys.path.insert(0,str(ROOT))
        from research_copilot.api_settings import APISettings,SECRETS
        for path in configs:
            if not (path.parent/'.api_settings.key').is_file():
                raise ValueError('Encrypted settings have no matching local key; restore the complete backup before auditing')
            data=APISettings(path.parent).read()
            for kind in ('llm','translation'):
                for profile in data.get(kind,{}).values():
                    for key in SECRETS:
                        value=profile.get(key)
                        if isinstance(value,str) and len(value)>=8:found.add(value.encode())
    return found


def audit(folder,secrets=()):
    issues=[];files=[]
    for path in sorted(folder.rglob('*')):
        if path.is_symlink():issues.append({'path':str(path.relative_to(folder)),'reason':'symlink'});continue
        if not path.is_file():continue
        rel=path.relative_to(folder).as_posix();parts=set(Path(rel).parts)
        if parts&FORBIDDEN_PARTS or path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name.startswith('.env') and path.name!='.env.example' or path.name in ('accounts.sqlite3','api_settings.enc','.api_settings.key'):
            issues.append({'path':rel,'reason':'private-file'})
        raw=path.read_bytes()
        if any(value in raw for value in secrets):issues.append({'path':rel,'reason':'matches-local-credential'})
        for label,pattern in PATTERNS:
            if pattern.search(raw):issues.append({'path':rel,'reason':label})
        if re.search(rb'(?:[A-Z]:[\\/](?:Users|AI-Research-Copilot)[\\/])',raw,re.I):issues.append({'path':rel,'reason':'machine-specific-absolute-path'})
        files.append({'path':rel,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    return {'file_count':len(files),'bytes':sum(f['bytes'] for f in files),'issues':issues,'files':files}


def build(output):
    output=output.resolve()
    release_root=(ROOT/'release').resolve()
    if not output.is_relative_to(release_root) or output==release_root:
        raise ValueError('Output must be a NEW subdirectory of the project release directory')
    if output.exists():raise ValueError('Output already exists; select a new directory instead of overwriting')
    secret_values=local_secrets()
    selected=selected_files()
    for relative in selected:
        source=(ROOT/relative).resolve()
        if not source.is_relative_to(ROOT) or not source.is_file():raise ValueError('Missing or unsafe public source: '+relative)
    # Scan source bytes before any release artifact is created.
    for relative in selected:
        if any(value in (ROOT/relative).read_bytes() for value in secret_values):raise ValueError('Credential match in '+relative+'; value redacted')
    output.mkdir(parents=True)
    for relative in selected:
        target=output/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/relative,target)
    result=audit(output,secret_values)
    if result['issues']:
        print(json.dumps({'issues':result['issues']},ensure_ascii=False))
        raise ValueError('Release audit failed; no ZIP was produced')
    (output/'RELEASE_MANIFEST.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    archive=output.with_suffix('.zip')
    if archive.exists():raise ValueError('Archive already exists; refusing overwrite')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as zip:
        for file in sorted(output.rglob('*')):
            if file.is_file():zip.write(file,Path(output.name)/file.relative_to(output))
    print(json.dumps({'directory':str(output),'archive':str(archive),'file_count':result['file_count']+1,
                      'bytes':result['bytes'],'credential_matches':0,'sha256_zip':hashlib.sha256(archive.read_bytes()).hexdigest()},ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'release'/'AI-Research-Copilot')
    parser.add_argument('--audit',type=Path,help='Audit an existing clean source release without creating an archive')
    args=parser.parse_args()
    if args.audit:
        report=audit(args.audit.resolve(),local_secrets())
        print(json.dumps({k:v for k,v in report.items() if k!='files'},ensure_ascii=False))
        return int(bool(report['issues']))
    build(args.output);return 0


if __name__=='__main__':raise SystemExit(main())
