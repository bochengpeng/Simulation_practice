import json,csv,time
from pathlib import Path
class RunLogger:
    def __init__(self,directory,run_id): self.directory=Path(directory); self.directory.mkdir(parents=True,exist_ok=True); self.path=self.directory/f'{run_id}.jsonl'; self.rows=[]
    def event(self,kind,payload):
        row={'timestamp':time.time(),'kind':kind,**payload}; self.rows.append(row); self.path.open('a',encoding='utf-8').write(json.dumps(row,default=str)+'\n')
    def write_csv(self):
        if not self.rows:return
        path=self.path.with_suffix('.csv'); keys=sorted({k for r in self.rows for k in r})
        with path.open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=keys); w.writeheader(); w.writerows(self.rows)

    def write_summary(self, summary):
        path = self.directory / 'trial_summaries.csv'
        fields = [
            'timestamp_utc', 'task_id', 'success', 'failure_code',
            'failure_reason', 'completed_parts', 'requested_parts',
            'action_count', 'replan_count', 'planner_mode', 'replanning_mode',
            'allow_local_safe_plan', 'model', 'ai_calls', 'ai_time_s',
            'elapsed_s',
        ]
        exists = path.exists()
        if exists:
            # A column added since the file was created: rewrite it with the
            # new header (old rows keep their values, new column left blank)
            # so appended rows never misalign with an old header.
            with path.open(newline='', encoding='utf-8') as handle:
                reader = csv.DictReader(handle)
                old_header = reader.fieldnames or []
                old_rows = list(reader)
            if any(name not in old_header for name in fields):
                fields = fields + [name for name in old_header if name not in fields]
                with path.open('w', newline='', encoding='utf-8') as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
                    writer.writeheader()
                    writer.writerows(old_rows)
            else:
                fields = old_header
        with path.open('a', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
            if not exists:
                writer.writeheader()
            writer.writerow({field: summary.get(field, '') for field in fields})
