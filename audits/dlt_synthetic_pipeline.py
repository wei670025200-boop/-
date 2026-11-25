"""650 synthetic rows, never persisted as a real ledger; full research integration."""
from pathlib import Path
import csv,io,json
import numpy as np
from dlt_v5.local_schedule import LocalSchedule
from dlt_v5.history_contract import HISTORY_FIELDS
from dlt_v5.history_validation import validate_history
from dlt_v5.prereg_core import SPACE,COUNTS
from dlt_v5.research_pipeline import run_research
from dlt_v5.runner_v5 import research_validators,evaluate_validators
from run_step2_real_oos_once import load_spec
ROOT=Path(__file__).resolve().parents[1]
s=LocalSchedule.load(ROOT/'data/dlt_local_schedule_manifest.json')
rows=[r for y in range(2022,2027) for r in s.year(y)][:650]
rng=np.random.default_rng(87031);stream=io.StringIO(newline='');w=csv.writer(stream,lineterminator='\n');w.writerow(HISTORY_FIELDS)
for i,(issue,day) in enumerate(rows):
    f=sorted(rng.choice(np.arange(1,36),5,replace=False).tolist());b=sorted(rng.choice(np.arange(1,13),2,replace=False).tolist());sales=80000000
    z=float(rng.normal(0,.08));counts=[max(1,int(sales/2*c/SPACE*np.exp(z+.1*sum(v%5==0 for v in f)))) for c in COUNTS]
    w.writerow([issue,day,*f,*b,sales,100000000,counts[0],1,counts[1],1,*counts[2:],'7TIER_2026' if day>='2026-02-02' else '9TIER_2019',0])
raw=stream.getvalue().encode();ledger,summary=validate_history(raw,s);stat=(ROOT/'data/dlt_statutory_rule.json').read_bytes();spec=load_spec();r=run_research(ledger,raw,stat,spec,'a'*40)
actual,detail=evaluate_validators(research_validators(raw,s,r,stat,spec,r['integrity'],2));assert [list(x) for x in actual]==r['gates'],(actual,r['gates'],detail)
assert len(r['front_weights'])==12 and len(r['back_weights'])==9 and tuple(map(len,r['rankings'].values()))==(12,10,10)
assert r['phase4_status']=='LOCKED' and len(r['trace']['ranks'])==449
print(json.dumps({'synthetic_only':True,'rows':summary['total_rows'],'oos_rows':len(r['trace']['ranks']),'gates':actual,'diagnostics':r['diagnostics'],'policy_summary':{k:v for k,v in r['policy'].items() if k!='trace'},'integration_assertions':'PASS','integrity':r['integrity']},indent=2))
