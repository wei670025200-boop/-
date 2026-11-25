"""Offline DLT V5 production credential, gates, and Candidate 0 ticket factory."""
from datetime import date
from itertools import combinations
import json
import math
from pathlib import Path

import numpy as np

from .balanced_coverage import DomainDRBG
from .cusum_monitor import errors, evaluate
from .digital_twin import BackModel, Ridge, feature_history_frozen, omission_set
from .frozen_protocol import DIGEST_KEYS, calendar_pulse, canonical_json, sha256, verify_integrity
from .history_validation import validate_history
from .prequential_rank import dynamic_front_features, front_universe
from .prereg_core import POLICY_GRID, back_features
from .research_pipeline import spec_bytes
from .training_prefix import verify_prefix

ARTIFACT_SCHEMA='DLT_V5_PRODUCTION_ARTIFACT_V1'
ARTIFACT_PATH='data/dlt_v5_production_artifact.json'
EXPECTED_INTEGRITY='58a8273667123a5f9bdc0efb699d9d4c8ec1a64b584defb7d8404eecd78ad6d1'
SPEC_FILES=('docs/DLT_V5_PREREG_CLEANROOM_1.0.md','docs/DLT_V5_PREREG_CLEANROOM_1.0_ADDENDUM.md','docs/DLT_V5_PREREG_EXECUTION_CONVENTIONS.md')
BACK_PAIRS=tuple(combinations(range(1,13),2))

def _git_bytes(path):
    """Use repository LF bytes even when a Windows checkout applies autocrlf."""
    return Path(path).read_bytes().replace(bytes((13,10)),bytes((10,)))

def _ridge(value):
    return Ridge(float(value['intercept']),tuple(map(float,value['weights'])),float(value['winsor_low']),float(value['winsor_high']))

def _back(value):
    return BackModel(_ridge(value['ridge']),tuple(map(float,value['ball_bias'])))

def _prereg_bytes(root):
    return spec_bytes(*(_git_bytes(Path(root,name)) for name in SPEC_FILES))

def load_production_artifact(root):
    root=Path(root);art=json.loads((root/ARTIFACT_PATH).read_bytes())
    if art.get('schema')!=ARTIFACT_SCHEMA or art.get('phase4_status')!='UNLOCKED':raise ValueError('PHASE4_PRODUCTION_ARTIFACT_INVALID')
    auth=art.get('authorization',{});candidate=art.get('candidate',{});twin=art.get('accepted_twin',{});integrity=art.get('integrity',{})
    if auth.get('approved_run_id')!='45608321723' or auth.get('approved_run_conclusion')!='success' or auth.get('oos_rerun_forbidden') is not True or (auth.get('ticket_count_min'),auth.get('ticket_count_max'))!=(1,18):raise ValueError('PHASE4_AUTHORIZATION_INVALID')
    if candidate!={'candidate':0,'front_band':[.01,.05],'back_k':18}:raise ValueError('PHASE4_CANDIDATE_INVALID')
    if art.get('trained_through_issue')!='26134' or twin.get('trained_through_issue')!='26134' or twin.get('phase4_status')!='LOCKED' or twin.get('policy_reference_ticket_count')!=18:raise ValueError('PHASE4_TWIN_BINDING_INVALID')
    if twin.get('policy_selection_research_only',{}).get('candidate')!=0 or twin.get('policy_selection_research_only',{}).get('front_band')!=[.01,.05] or twin.get('policy_selection_research_only',{}).get('back_k')!=18:raise ValueError('PHASE4_POLICY_BINDING_INVALID')
    if art.get('cusum_baseline')!=twin.get('drift_reference') or len(art.get('accepted_residual_tail',()))!=20:raise ValueError('PHASE4_CUSUM_BINDING_INVALID')
    if set(art.get('accepted_gate_statuses',{}))!={'DATA_GATE','OOS_GATE','DRIFT_GATE','POLICY_GATE'} or set(art['accepted_gate_statuses'].values())!={'PASS'}:raise ValueError('PHASE4_GATE_ACCEPTANCE_INVALID')
    actual={
        'history_prefix_sha256':art['training_prefix']['prefix_sha256'],
        'twin_artifact_sha256':sha256(canonical_json(twin)),
        'statutory_rule_sha256':sha256(_git_bytes(root/'data/dlt_statutory_rule.json')),
        'prereg_spec_sha256':sha256(_prereg_bytes(root)),
        'candidate_grid_sha256':sha256(canonical_json(POLICY_GRID)),
        'oos_trace_sha256':integrity.get('oos_trace_sha256'),
    }
    verify_integrity(integrity,actual)
    if integrity['integrity_summary_sha256']!=EXPECTED_INTEGRITY or integrity['digital_signature'] is not False:raise ValueError('PHASE4_INTEGRITY_INVALID')
    claim=json.loads((root/'data/dlt_real_oos_once_claim.json').read_bytes())
    expected_claim={'schema':'DLT_REAL_OOS_ONCE_CLAIM_V1','run_id':auth['approved_run_id'],'run_attempt':'1','csv_sha256':art['training_prefix']['prefix_sha256'],'prereg_spec_sha256':integrity['prereg_spec_sha256'],'source_commit_sha':auth['approved_run_head_sha']}
    if claim!=expected_claim:raise ValueError('OOS_ONCE_CLAIM_INVALID')
    return art

def production_validators(root,schedule,artifact,csv_raw,ticket_count):
    root=Path(root);csv_raw=csv_raw.replace(bytes((13,10)),bytes((10,)));accepted=artifact['accepted_gate_statuses'];twin=artifact['accepted_twin']
    cache={}
    def data():
        rows,_=validate_history(csv_raw,schedule);verify_prefix(csv_raw,artifact['training_prefix']);cache['rows']=rows
        return accepted['DATA_GATE']
    def oos():
        if artifact['integrity']['integrity_summary_sha256']!=EXPECTED_INTEGRITY:raise ValueError('OOS_ACCEPTANCE_MISMATCH')
        return accepted['OOS_GATE']
    def drift():
        rows=cache.get('rows')
        if rows is None:rows,_=validate_history(csv_raw,schedule)
        features=feature_history_frozen(rows);cut=int(artifact['trained_through_issue']);idx=[i for i,issue in enumerate(features['issues']) if int(issue)>cut]
        later=[]
        if idx:
            fm=_ridge(twin['front']);bm=_back(twin['back']);xf=np.asarray(features['xf'])[idx];xb=np.asarray(features['xb'])[idx];yf=np.asarray(features['yf'])[idx];yb=np.asarray(features['yb'])[idx];pairs=[features['pairs'][i] for i in idx]
            later=errors(yf,fm.predict(xf),yb,bm.predict(xb,pairs)).tolist()
        status=evaluate([*artifact['accepted_residual_tail'],*later],artifact['cusum_baseline'])['drift_gate']
        return status if accepted['DRIFT_GATE']=='PASS' else 'FAIL'
    def policy():
        if type(ticket_count) is not int or not 1<=ticket_count<=18:raise ValueError('POLICY_TICKET_DOMAIN_UNVALIDATED')
        if artifact['candidate']!={'candidate':0,'front_band':[.01,.05],'back_k':18}:raise ValueError('POLICY_CANDIDATE_MISMATCH')
        return accepted['POLICY_GATE']
    return {'DATA_GATE':data,'OOS_GATE':oos,'DRIFT_GATE':drift,'POLICY_GATE':policy}

class V5TicketFactory:
    def __init__(self,rows,artifact):
        self.rows=tuple(rows);self.artifact=artifact
        self.front_history=tuple(tuple(int(row['f'+str(i)]) for i in range(1,6)) for row in self.rows)
        self.back_history=tuple(tuple(int(row['b'+str(i)]) for i in range(1,3)) for row in self.rows)
        if not self.front_history:raise ValueError('EMPTY_HISTORY')
    @staticmethod
    def _sample(pool,n,drbg):
        values=list(pool)
        for i in range(n):
            j=i+drbg.randbelow(len(values)-i);values[i],values[j]=values[j],values[i]
        return values[:n]
    def __call__(self,n,seed,day):
        if type(n) is not int or not 1<=n<=18:raise ValueError('POLICY_TICKET_DOMAIN_UNVALIDATED')
        draw_day=date.fromisoformat(day);twin=self.artifact['accepted_twin'];candidate=self.artifact['candidate']
        a,static,excluded=front_universe();fm=_ridge(twin['front']);dynamic=dynamic_front_features(a,self.front_history[-1],calendar_pulse(draw_day),omission_set(self.front_history,35,12,20));w=np.asarray(fm.weights);scores=static@w+dynamic@w[6:10]+fm.intercept
        eligible=np.flatnonzero(~excluded);order=eligible[np.lexsort((eligible,scores[eligible]))];lo,hi=candidate['front_band'];start=math.ceil(lo*len(order));stop=math.floor(hi*len(order));front_pool=order[start-1:stop]
        front_ix=self._sample(front_pool,n,DomainDRBG(seed,b'DLT_V5_CANDIDATE_0_FRONT'))
        bm=_back(twin['back']);omit=omission_set(self.back_history,12,12);xb=[back_features(pair,previous=self.back_history[-1],omit_chase_numbers=omit) for pair in BACK_PAIRS];back_order=sorted(range(66),key=lambda i:(float(bm.predict([xb[i]],[BACK_PAIRS[i]])[0]),BACK_PAIRS[i]));back_pool=[BACK_PAIRS[i] for i in back_order[:candidate['back_k']]]
        backs=self._sample(back_pool,n,DomainDRBG(seed,b'DLT_V5_CANDIDATE_0_BACK'))
        return tuple((tuple(map(int,a[i])),tuple(map(int,backs[j]))) for j,i in enumerate(front_ix))

def load_runtime(root,schedule,ticket_count):
    root=Path(root);artifact=load_production_artifact(root);raw=_git_bytes(root/'data/dlt_history.csv');rows,_=validate_history(raw,schedule);factory=V5TicketFactory(rows,artifact);validators=production_validators(root,schedule,artifact,raw,ticket_count)
    return artifact,factory,validators
