"""AST static scan of all src Python files. Does not certify third-party runtime internals."""
import ast,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BLOCK={'socket','ssl','http','urllib','requests','urllib3','aiohttp','httpx','ftplib','smtplib','telnetlib','xmlrpc','grpc','websocket','websockets','paramiko','asyncssh','pycurl','subprocess','ctypes','cffi','importlib'}
files=sorted((ROOT/'src').rglob('*.py'));hits=[]
for path in files:
    tree=ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
    for node in ast.walk(tree):
        names=[]
        if isinstance(node,ast.Import):names=[x.name for x in node.names]
        if isinstance(node,ast.ImportFrom) and node.level==0:names=[node.module or '']
        for name in names:
            if name.split('.')[0] in BLOCK:hits.append([str(path.relative_to(ROOT)),node.lineno,name])
        if isinstance(node,ast.Call):
            f=node.func
            if isinstance(f,ast.Name) and f.id in {'eval','exec','__import__'}:hits.append([str(path.relative_to(ROOT)),node.lineno,f.id])
            if isinstance(f,ast.Attribute) and isinstance(f.value,ast.Name) and f.value.id=='os' and (f.attr in {'system','popen','fork','forkpty'} or f.attr.startswith(('exec','spawn'))):hits.append([str(path.relative_to(ROOT)),node.lineno,'os.'+f.attr])
print(json.dumps({'files_scanned':len(files),'files':[str(p.relative_to(ROOT)) for p in files],'network_or_dynamic_execution_hits':hits,'pass':bool(files) and not hits,'scope':'Direct source AST imports and selected dynamic execution calls, not third-party runtime certification'},indent=2));sys.exit(0 if files and not hits else 1)
