"""Optional loopback Argos/CTranslate2 service, isolated from the app environment."""
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import argparse,json,os,subprocess,threading,time
from urllib.request import build_opener,ProxyHandler
from .prose import reading_prose,VERSION

ROOT=Path(__file__).resolve().parents[1]

def profile(root=None):
    root=Path(root or ROOT/'data').resolve();file=root/'local_translation/runtime.json'
    try:
        value=json.loads(file.read_text(encoding='utf-8'))
        model=(ROOT/value['model_path']).resolve();python=(ROOT/value['python_path']).resolve()
        if not value.get('enabled') or not model.is_relative_to((root/'local_translation').resolve()) or not python.is_relative_to(ROOT) or value['port']!=8521 or not (model/'model/model.bin').is_file() or not python.is_file():return None
        return value|{'model_absolute':str(model),'python_absolute':str(python)}
    except (OSError,ValueError,KeyError,TypeError):return None

def health(port=8521):
    try:
        with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port}/health',timeout=0.4) as response:return json.load(response)
    except (OSError,ValueError):return None

def ensure_local_translation(root):
    p=profile(root)
    if not p:return False
    status=health(p['port'])
    if status:return status.get('model_sha256')==p['model_sha256']
    logs=Path(root)/'local_translation';options={'cwd':str(ROOT),'stdin':subprocess.DEVNULL}
    if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
    with (logs/'server.log').open('ab') as out,(logs/'server-error.log').open('ab') as err:
        subprocess.Popen([p['python_absolute'],'-m','research_copilot.local_translation','--data-dir',str(Path(root).resolve())],stdout=out,stderr=err,**options)
    for _ in range(40):
        status=health(p['port'])
        if status:return status.get('model_sha256')==p['model_sha256']
        time.sleep(0.1)
    return False

class Engine:
    def __init__(self,p):
        import ctranslate2,sentencepiece,pysbd
        model=Path(p['model_absolute']);self.meta=json.loads((model/'metadata.json').read_text(encoding='utf-8'))
        self.tokenizer=sentencepiece.SentencePieceProcessor(model_file=str(model/'sentencepiece.model'))
        self.translator=ctranslate2.Translator(str(model/'model'),device='cpu',compute_type='int8',inter_threads=1,intra_threads=2)
        self.segmenter=pysbd.Segmenter(language='en',clean=False);self.lock=threading.Lock()
    def translate(self,text):
        text=reading_prose(text);outputs=[]
        with self.lock:
            for paragraph in text.split('\n\n'):
                sentences=self.segmenter.segment(paragraph);tokens=[]
                for sentence in sentences:
                    pieces=self.tokenizer.encode(sentence,out_type=str)
                    # Every token is sent; no model-side input truncation.
                    tokens.extend(pieces[a:a+150] for a in range(0,len(pieces),150))
                prefix=self.meta.get('target_prefix','')
                result=self.translator.translate_batch(tokens,target_prefix=[[prefix]]*len(tokens) if prefix else None,
                    replace_unknowns=True,beam_size=4,length_penalty=0.2,max_input_length=0,max_decoding_length=2048)
                translated=[]
                for r in result:
                    if len(r.hypotheses[0])>=2048:raise ValueError('translation output limit reached')
                    value=self.tokenizer.decode(r.hypotheses[0]).replace('\u2581',' ')
                    if prefix and value.startswith(prefix):value=value[len(prefix):]
                    translated.append(value.strip())
                outputs.append(''.join(translated))
        return '\n\n'.join(outputs)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',type=Path,default=ROOT/'data');args=parser.parse_args()
    p=profile(args.data_dir)
    if not p:raise SystemExit('Local model profile invalid; run scripts.install_local_translation')
    engine=Engine(p)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,obj,status=200):
            raw=json.dumps(obj,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def allowed(self):return self.headers.get('Host') in {f'127.0.0.1:{p["port"]}',f'localhost:{p["port"]}'} and not self.headers.get('Origin')
        def do_GET(self):
            if not self.allowed() or self.path!='/health':return self.reply({'error':'rejected'},403)
            self.reply({'ready':True,'engine':p['engine'],'model_sha256':p['model_sha256'],'normalizer':VERSION})
        def do_POST(self):
            if not self.allowed() or self.path!='/translate':return self.reply({'error':'rejected'},403)
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=100000:raise ValueError('size')
                request=json.loads(self.rfile.read(size));text=request['q']
                if request.get('source')!='en' or request.get('target')!='zh' or not isinstance(text,str) or not 0<len(text)<=12000:raise ValueError('input')
                self.reply({'translatedText':engine.translate(text)})
            except (ValueError,KeyError,TypeError):self.reply({'error':'invalid translation input or output'},400)
    ThreadingHTTPServer(('127.0.0.1',p['port']),Handler).serve_forever()

if __name__=='__main__':main()
