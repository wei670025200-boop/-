"""Validate an existing local ledger; never downloads or invents rows."""
import argparse
import json
from pathlib import Path
from dlt_v5.local_schedule import LocalSchedule
from dlt_v5.history_validation import validate_history

def main():
    p=argparse.ArgumentParser();p.add_argument('--csv',default='data/dlt_history.csv');p.add_argument('--schedule',default='data/dlt_local_schedule_manifest.json');a=p.parse_args()
    try:
        _,report=validate_history(Path(a.csv).read_bytes(),LocalSchedule.load(a.schedule))
    except (ValueError,OSError,UnicodeError) as exc:
        print(json.dumps({'data_gate':'FAIL','error':str(exc)}));return 1
    print(json.dumps(report,indent=2));return 0
if __name__=='__main__': raise SystemExit(main())
