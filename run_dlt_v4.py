import argparse,json
from dlt_v5.engine_v4 import DLTV4Engine
from dlt_v5.local_schedule import LocalSchedule

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--issue');p.add_argument('--date');p.add_argument('--tickets',type=int);p.add_argument('--additional',action='store_true');p.add_argument('--dry-run',action='store_true');p.add_argument('--recover',action='store_true');p.add_argument('--schedule',default='data/dlt_local_schedule_manifest.json');a=p.parse_args()
    engine=DLTV4Engine(a.root,LocalSchedule.load(a.schedule));result=engine.recover_staged_publish() if a.recover else engine.execute(a.issue,a.date,a.tickets,a.additional,dry_run=a.dry_run)
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
