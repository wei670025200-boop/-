"""Offline issue/date mapping from an explicit schedule manifest; no network I/O."""
import json
from datetime import date,timedelta
from pathlib import Path
from functools import lru_cache

class LocalSchedule:
    def __init__(self,manifest):
        if manifest.get('schema')!='DLT_LOCAL_SCHEDULE_V1' or manifest.get('timezone')!='Asia/Shanghai' or manifest.get('draw_weekdays_python')!=[0,2,5] or manifest.get('unregistered_year')!='FAIL_CLOSED':
            raise ValueError('INVALID_SCHEDULE_MANIFEST')
        self._years={}
        for year,spec in manifest['years'].items():
            if not isinstance(year,str) or len(year)!=4 or not year.isdecimal() or not spec.get('source'):
                raise ValueError('INVALID_SCHEDULE_YEAR')
            y=int(year); dates=[]; intervals=[]
            for start,end in spec['closures']:
                a=date.fromisoformat(start); b=date.fromisoformat(end)
                if a.year!=y or b.year!=y or a>b: raise ValueError('INVALID_CLOSURE_INTERVAL')
                intervals.append((a,b))
            if any(a<=d and c<=b for i,(a,b) in enumerate(intervals) for c,d in intervals[i+1:]): raise ValueError('OVERLAPPING_CLOSURES')
            d=date(y,1,1)
            while d.year==y:
                if d.weekday() in (0,2,5) and not any(a<=d<=b for a,b in intervals): dates.append(d)
                d+=timedelta(days=1)
            if not dates: raise ValueError('EMPTY_SCHEDULE_YEAR')
            self._years[y]=tuple((f'{y%100:02d}{i:03d}',d.isoformat()) for i,d in enumerate(dates,1))
    @classmethod
    def load(cls,path): return cls(json.loads(Path(path).read_text(encoding='utf-8')))
    def year(self,year):
        if year not in self._years: raise ValueError('SCHEDULE_YEAR_UNREGISTERED')
        return self._years[year]
    def check(self,issue,day):
        d=date.fromisoformat(day); rows=self.year(d.year)
        if (issue,day) not in rows: raise ValueError('ISSUE_DATE_SCHEDULE_CONFLICT')
        return (issue,day)
    def next(self,issue,day):
        self.check(issue,day); y=date.fromisoformat(day).year; rows=self.year(y); i=rows.index((issue,day))
        return rows[i+1] if i+1<len(rows) else self.year(y+1)[0]
