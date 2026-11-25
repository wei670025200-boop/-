"""Local POSIX transaction engine. One durable pending request; replay uses its seed.
The ledger is logically append-only: atomically replace with the verified old bytes
plus exactly one canonical record, then atomically publish the staged directory.
This requires a local filesystem supporting flock, fsync and same-filesystem rename.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import tempfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption, PublicFormat
from .balanced_coverage import balanced_tickets, exact_batch_coverage, UNIFORM_CLAIM
from .frozen_protocol import canonical_json, sha256

ARTIFACTS=('canonical_commitment.json','physical_slip_plan.json','exact_batch_coverage.json','draw_ev_context.json','transaction_receipt.json')
FALLBACK='DLT_V4_BALANCED_COVERAGE_FALLBACK'
GENESIS='0'*64

class DuplicateIssue(ValueError): pass
class CrashInjection(RuntimeError): pass

def _sync_dir(p):
    fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)

def _write(path,data,mode=0o600):
    fd,tmp=tempfile.mkstemp(prefix='.tmp-',dir=path.parent)
    try:
        os.fchmod(fd,mode)
        with os.fdopen(fd,'wb') as f: f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path);_sync_dir(path.parent)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def _load(path): return json.loads(path.read_bytes())

def _private_bytes(path):
    if path.is_symlink() or path.stat().st_mode&0o777!=0o600: raise ValueError('PRIVATE_FILE_PERMISSION')
    return path.read_bytes()

class DLTV4Engine:
    def __init__(self,root,schedule):
        self.root=Path(root);self.schedule=schedule
    def _init(self):
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        for name in ('pending','published','private'):
            (self.root/name).mkdir(mode=0o700,exist_ok=True)
        _sync_dir(self.root)
    @contextmanager
    def _lock(self):
        self._init()
        with open(self.root/'lock','a+b') as f:
            fcntl.flock(f,fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(f,fcntl.LOCK_UN)
    def _key(self,create=False):
        path=self.root/'private'/'ed25519.key'
        if not path.exists():
            if not create:raise ValueError('SIGNING_KEY_MISSING')
            if (self.root/'ledger.jsonl').exists() or any((self.root/'published').iterdir()):raise ValueError('SIGNING_KEY_LOST')
            key=Ed25519PrivateKey.generate()
            _write(path,key.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption()))
        return Ed25519PrivateKey.from_private_bytes(_private_bytes(path))
    def _ledger(self):
        path=self.root/'ledger.jsonl';raw=path.read_bytes() if path.exists() else b'';prev=GENESIS;seen=set();entries=[]
        if raw and not raw.endswith(b'\n'):raise ValueError('LEDGER_TRUNCATED')
        for line in raw.splitlines():
            e=json.loads(line);body=dict(e);digest=body.pop('entry_sha256',None)
            if canonical_json(e)!=line or body.get('prev_sha256')!=prev or digest!=sha256(canonical_json(body)) or body.get('issue') in seen:raise ValueError('LEDGER_CHAIN_INVALID')
            self.schedule.check(body['issue'],body['date'])
            if entries and self.schedule.next(entries[-1]['issue'],entries[-1]['date'])!=(body['issue'],body['date']):raise ValueError('LEDGER_ISSUE_ORDER')
            seen.add(body['issue']);entries.append(e);prev=digest
        return raw,entries
    def _check_published(self,entries,allow_pending=None):
        expected={e['issue'] for e in entries}
        actual={p.name for p in (self.root/'published').iterdir()}
        if actual-expected or expected-actual-({allow_pending} if allow_pending else set()):raise ValueError('PUBLISHED_LEDGER_MISMATCH')
        for e in entries:
            p=self.root/'published'/e['issue']
            if p.exists():
                self._verify(p,e)
                if e['issue']!=allow_pending:
                    seed=_private_bytes(self.root/'private'/(e['issue']+'.seed'))
                    if sha256(seed)!=_load(p/ARTIFACTS[0])['payload']['seed_sha256']:raise ValueError('ARCHIVED_SEED_MISMATCH')
    def _archive_seed(self,pending,issue):
        req,seed=self._read_stage(pending);p=self.root/'private'/(issue+'.seed')
        c=_load(self.root/'published'/issue/ARTIFACTS[0]);
        if sha256(seed)!=c['payload']['seed_sha256']:raise ValueError('ARCHIVED_SEED_MISMATCH')
        if p.exists():
            if _private_bytes(p)!=seed:raise ValueError('ARCHIVED_SEED_MISMATCH')
        else:_write(p,seed)
    def _request(self,issue,day,ticket_count,additional,route):
        self.schedule.check(issue,day)
        if type(ticket_count) is not int or ticket_count<1 or type(additional) is not bool or route not in ('DLT_V4_BALANCED_COVERAGE',FALLBACK):raise ValueError('INVALID_EXECUTION_REQUEST')
        return {'issue':issue,'date':day,'ticket_count':ticket_count,'additional':additional,'route':route}
    def _stage(self,req):
        pending=self.root/'pending';target=pending/req['issue']
        tmp=Path(tempfile.mkdtemp(prefix='.stage-',dir=pending))
        seed=os.urandom(32);key=self._key(create=True)
        binding={'request':req,'seed_sha256':sha256(seed)}
        _write(tmp/'request.json',canonical_json(req));_write(tmp/'seed.bin',seed);_write(tmp/'stage_signature.bin',key.sign(canonical_json(binding)))
        os.rename(tmp,target);_sync_dir(pending);return target
    def _read_stage(self,pending):
        req=_load(pending/'request.json');seed=_private_bytes(pending/'seed.bin')
        if pending.name!=req['issue']:raise ValueError('STAGED_ISSUE_MISMATCH')
        self._key().public_key().verify(_private_bytes(pending/'stage_signature.bin'),canonical_json({'request':req,'seed_sha256':sha256(seed)}))
        return req,seed
    def _discard_stage(self,pending):
        target=Path(tempfile.mkdtemp(prefix='.done-',dir=pending.parent));target.rmdir()
        os.rename(pending,target);_sync_dir(target.parent);shutil.rmtree(target)
    def _build(self,pending,prev):
        req,seed=self._read_stage(pending);self._request(req['issue'],req['date'],req['ticket_count'],req['additional'],req['route'])
        key=self._key();pub=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        tickets=balanced_tickets(req['ticket_count'],seed)
        payload={'schema':'DLT_COMMITMENT_V1',**req,'seed_sha256':sha256(seed),'uniform_marginal':UNIFORM_CLAIM,'tickets':[{'front':list(f),'back':list(b)} for f,b in tickets]}
        commitment={'payload':payload,'public_key_hex':pub.hex(),'signature_hex':key.sign(canonical_json(payload)).hex(),'algorithm':'Ed25519'}
        price=3 if req['additional'] else 2
        slip={'schema':'DLT_PHYSICAL_SLIP_PLAN_V1','issue':req['issue'],'tickets':[{**t,'is_additional_bet':req['additional'],'cost_rmb':price} for t in payload['tickets']],'total_rmb':price*len(tickets)}
        coverage=exact_batch_coverage(tickets)
        ev={'schema':'DLT_DRAW_EV_CONTEXT_V1','issue':req['issue'],'profit_guarantee':'NONE','expected_profit_rmb':None,'reason':'No reliable future payout and sales inputs; no EV guarantee.'}
        data={n:canonical_json(v) for n,v in zip(ARTIFACTS[:4],(commitment,slip,coverage,ev))}
        body={'schema':'DLT_LEDGER_V1','issue':req['issue'],'date':req['date'],'prev_sha256':prev,'artifact_sha256':{n:sha256(raw) for n,raw in data.items()}}
        entry={**body,'entry_sha256':sha256(canonical_json(body))}
        data[ARTIFACTS[4]]=canonical_json(entry)
        bundle=pending/'bundle'
        if bundle.exists():
            if set(x.name for x in bundle.iterdir())!=set(ARTIFACTS) or any((bundle/n).read_bytes()!=raw for n,raw in data.items()):raise ValueError('STAGED_HASH_MISMATCH')
        else:
            tmp=Path(tempfile.mkdtemp(prefix='.bundle-',dir=pending))
            for n,raw in data.items():_write(tmp/n,raw)
            os.rename(tmp,bundle);_sync_dir(pending)
        self._verify(bundle,entry)
        return entry,bundle
    def _verify(self,bundle,entry):
        if set(x.name for x in bundle.iterdir())!=set(ARTIFACTS) or _load(bundle/ARTIFACTS[4])!=entry:raise ValueError('ARTIFACT_SET_MISMATCH')
        if set(entry['artifact_sha256'])!=set(ARTIFACTS[:4]):raise ValueError('ARTIFACT_DIGEST_SET_MISMATCH')
        for name,digest in entry['artifact_sha256'].items():
            if sha256((bundle/name).read_bytes())!=digest:raise ValueError('ARTIFACT_HASH_MISMATCH')
        c=_load(bundle/ARTIFACTS[0]);key=self._key();pub=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        if c['public_key_hex']!=pub.hex() or c['algorithm']!='Ed25519':raise ValueError('SIGNER_MISMATCH')
        Ed25519PublicKey.from_public_bytes(pub).verify(bytes.fromhex(c['signature_hex']),canonical_json(c['payload']))
        p=c['payload'];slip=_load(bundle/ARTIFACTS[1]);price=3 if p['additional'] else 2
        if p['issue']!=entry['issue'] or p['date']!=entry['date'] or len(p['tickets'])!=p['ticket_count'] or slip['issue']!=p['issue'] or slip['tickets']!=[{**t,'is_additional_bet':p['additional'],'cost_rmb':price} for t in p['tickets']] or slip['total_rmb']!=price*p['ticket_count']:raise ValueError('PHYSICAL_SLIP_MISMATCH')
    def _finish(self,pending,crash_after=None):
        req,_=self._read_stage(pending);issue=req['issue'];raw,entries=self._ledger();found=next((e for e in entries if e['issue']==issue),None);published=self.root/'published'/issue
        self._check_published(entries,allow_pending=issue)
        if found is not None and published.exists():
            self._verify(published,found);self._archive_seed(pending,issue);self._discard_stage(pending);return {'status':'PUBLISHED','issue':issue,'entry':found,'path':str(published)}
        if found is None and entries and self.schedule.next(entries[-1]['issue'],entries[-1]['date'])!=(issue,req['date']):raise ValueError('ISSUE_NOT_NEXT')
        if found is not None and found!=entries[-1]:raise ValueError('PENDING_LEDGER_ORDER')
        prev=found['prev_sha256'] if found else entries[-1]['entry_sha256'] if entries else GENESIS
        entry,bundle=self._build(pending,prev)
        if crash_after=='HASH VERIFY':raise CrashInjection(crash_after)
        if found is not None and found!=entry:raise ValueError('RECOVERY_ENTRY_MISMATCH')
        if found is None:_write(self.root/'ledger.jsonl',raw+canonical_json(entry)+b'\n')
        if crash_after=='LEDGER COMMIT':raise CrashInjection(crash_after)
        if published.exists():raise ValueError('PUBLISH_COLLISION')
        os.rename(bundle,published);_sync_dir(published.parent);_sync_dir(pending)
        if crash_after=='ATOMIC PUBLISH':raise CrashInjection(crash_after)
        self._verify(published,entry);self._archive_seed(pending,issue);self._discard_stage(pending)
        return {'status':'PUBLISHED','issue':issue,'entry':entry,'path':str(published)}
    def _recover(self):
        paths=sorted(p for p in (self.root/'pending').iterdir() if not p.name.startswith('.'))
        if len(paths)>1:raise ValueError('MULTIPLE_PENDING_TRANSACTIONS')
        return self._finish(paths[0]) if paths else None
    def recover_staged_publish(self):
        with self._lock():return self._recover()
    def execute(self,issue,day,ticket_count,additional=False,route='DLT_V4_BALANCED_COVERAGE',dry_run=False,crash_after=None):
        req=self._request(issue,day,ticket_count,additional,route)
        if dry_run:
            if self.root.exists():
                with self._lock():
                    if any(not p.name.startswith('.') for p in (self.root/'pending').iterdir()):raise ValueError('PENDING_RECOVERY_REQUIRED')
                    _,entries=self._ledger();self._check_published(entries)
                    if any(e['issue']==issue for e in entries):raise DuplicateIssue('REFUSE_DUPLICATE_ISSUE')
                    if entries and self.schedule.next(entries[-1]['issue'],entries[-1]['date'])!=(issue,day):raise ValueError('ISSUE_NOT_NEXT')
            # An isolated namespace runs the identical transaction stages; no production writes.
            with tempfile.TemporaryDirectory(prefix='dlt-dry-') as tmp:
                result=DLTV4Engine(tmp,self.schedule).execute(issue,day,ticket_count,additional,route,False)
                artifacts={n:_load(Path(result['path'])/n) for n in ARTIFACTS}
                return {'status':'DRY_RUN','issue':issue,'artifacts':artifacts,'production_commit':False}
        with self._lock():
            self._recover();_,entries=self._ledger();self._check_published(entries)
            if any(e['issue']==issue for e in entries) or (self.root/'published'/issue).exists():raise DuplicateIssue('REFUSE_DUPLICATE_ISSUE')
            if entries and self.schedule.next(entries[-1]['issue'],entries[-1]['date'])!=(issue,day):raise ValueError('ISSUE_NOT_NEXT')
            pending=self._stage(req)
            if crash_after=='STAGE':raise CrashInjection(crash_after)
            return self._finish(pending,crash_after)
