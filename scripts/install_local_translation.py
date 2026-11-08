"""Download the official Argos English->Chinese data model, never paper text."""
from pathlib import Path
import hashlib,json,shutil,urllib.request,zipfile

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'data/local_translation'
URL='https://data.argosopentech.com/argospm/v1/translate-en_zh-1_9.argosmodel'

def main():
    DEST.mkdir(parents=True,exist_ok=True)
    archive=DEST/'translate-en_zh-1_9.argosmodel'
    if not archive.is_file():
        part=archive.with_suffix('.part')
        with urllib.request.urlopen(URL,timeout=60) as response,part.open('wb') as target:
            size=0
            while chunk:=response.read(1024*1024):
                size+=len(chunk)
                if size>200_000_000:raise ValueError('unexpected model size')
                target.write(chunk)
        part.replace(archive)
    unpack=DEST/'models';unpack.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            resolved=(unpack/entry.filename).resolve()
            if not resolved.is_relative_to(unpack.resolve()):raise ValueError('unsafe model archive')
        package.extractall(unpack)
    model=next(p.parent for p in unpack.rglob('metadata.json') if (p.parent/'model/model.bin').is_file())
    metadata=json.loads((model/'metadata.json').read_text(encoding='utf-8'))
    if metadata['from_code']!='en' or metadata['to_code']!='zh':raise ValueError('unexpected model language')
    profile={'enabled':True,'model_path':str(model.relative_to(ROOT)),
             'model_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
             'model_version':metadata.get('package_version'),'source_url':URL,
             'python_path':'.venv-translation/Scripts/python.exe','port':8521,
             'engine':'Argos English-Chinese / CTranslate2 CPU','auto_translate':True}
    (DEST/'runtime.json').write_text(json.dumps(profile,indent=2),encoding='utf-8')
    print(json.dumps({'installed':True,'bytes':archive.stat().st_size,'model':profile},ensure_ascii=True))

if __name__=='__main__':main()
