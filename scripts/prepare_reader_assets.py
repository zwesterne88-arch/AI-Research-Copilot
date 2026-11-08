"""Fetch only public Mozilla PDF.js package assets, never paper data."""
import hashlib,json,tarfile,io
from pathlib import Path
from urllib.request import urlopen
ROOT=Path(__file__).resolve().parents[1]
def main():
    meta=json.load(urlopen('https://registry.npmjs.org/pdfjs-dist/latest',timeout=30))
    raw=urlopen(meta['dist']['tarball'],timeout=60).read()
    out=ROOT/'web/vendor';out.mkdir(parents=True,exist_ok=True)
    names=['build/pdf.mjs','build/pdf.worker.mjs','web/pdf_viewer.mjs','web/pdf_viewer.css','LICENSE']
    hashes={}
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
        for name in names:
            data=archive.extractfile('package/'+name).read();p=out/Path(name).name;p.write_bytes(data);hashes[p.name]=hashlib.sha256(data).hexdigest()
        for member in archive.getmembers():
            rel=member.name.removeprefix('package/')
            if rel.startswith(('cmaps/','standard_fonts/','wasm/')) and member.isfile() and '..' not in Path(rel).parts:
                p=out/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(archive.extractfile(member).read())
    (out/'manifest.json').write_text(json.dumps({'name':'pdfjs-dist','version':meta['version'],'source':meta['dist']['tarball'],'sha256_archive':hashlib.sha256(raw).hexdigest(),'assets':hashes},indent=2),encoding='utf-8')
    print({'version':meta['version'],'local_assets':len(list(out.rglob('*'))),'paper_uploads':0})
if __name__=='__main__':main()
