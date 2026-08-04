#!/usr/bin/env python3
"""Explain PostgreSQL execution plan for HCP candidate search."""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
from sqlalchemy import text
PROJECT_ROOT=Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path: sys.path.insert(0,str(PROJECT_ROOT))
from app.models.query import HumanitarianQuery
from app.storage.postgres_store import PostgresRecordStorage, humanitarian_records_table
from sqlalchemy import select

def load_env():
 p=PROJECT_ROOT/'.env'
 if not p.exists(): return
 for line in p.read_text().splitlines():
  line=line.strip()
  if not line or line.startswith('#') or '=' not in line: continue
  k,v=line.split('=',1); os.environ.setdefault(k.strip(),v.strip().strip('"').strip("'"))

def build_stmt(q,limit):
 st=select(humanitarian_records_table.c.id,humanitarian_records_table.c.record_payload).where(humanitarian_records_table.c.subject_type==q.subject.type)
 dl=q.declared_location()
 if dl:
  cc=getattr(dl,'country_code',None)
  a1=getattr(dl,'admin_level_1',None)
  if cc: st=st.where(humanitarian_records_table.c.country_code==cc.upper())
  if a1:
   norm=" ".join(a1.lower().split())
   st=st.where(humanitarian_records_table.c.admin_level_1_normalized==norm)
 st=st.order_by(humanitarian_records_table.c.observed_at.desc(),humanitarian_records_table.c.id.asc()).limit(limit)
 return st

def main():
 load_env()
 ap=argparse.ArgumentParser()
 ap.add_argument('--database-url',default=os.getenv('DATABASE_URL'))
 ap.add_argument('--limit',type=int,default=100)
 ap.add_argument('--format',choices=['text','json'],default='text')
 a=ap.parse_args()
 if not a.database_url: raise SystemExit('DATABASE_URL required')
 storage=PostgresRecordStorage(database_url=a.database_url)
 q=HumanitarianQuery.model_validate({
 "query_id":"00000000-0000-0000-0000-000000000001",
 "subject":{"type":"human","reported_label":"Maria","estimated_age":34},
 "observation":{"declared_location":{"country_code":"XZ","admin_level_1":"HCP Benchmark"},"searched_at":"2026-08-04T12:00:00Z"}})
 stmt=build_stmt(q,a.limit)
 compiled=stmt.compile(storage.engine,dialect=storage.engine.dialect,compile_kwargs={"literal_binds":True})
 explain="EXPLAIN (ANALYZE, BUFFERS, VERBOSE, FORMAT JSON) "+str(compiled)
 with storage.engine.connect() as c:
  plan=c.execute(text(explain)).scalar()
 storage.close()
 print("\nGenerated SQL\n"+"="*72)
 print(compiled)
 print("\nExecution Plan\n"+"="*72)
 if a.format=='json':
  print(json.dumps(plan,indent=2))
 else:
  node=plan[0]["Plan"]
  print(f"Node Type          : {node.get('Node Type')}")
  print(f"Relation           : {node.get('Relation Name')}")
  print(f"Index              : {node.get('Index Name')}")
  print(f"Startup Cost       : {node.get('Startup Cost')}")
  print(f"Total Cost         : {node.get('Total Cost')}")
  print(f"Plan Rows          : {node.get('Plan Rows')}")
  print(f"Actual Rows        : {node.get('Actual Rows')}")
  print(f"Execution Time ms  : {plan[0].get('Execution Time')}")
  print(f"Planning Time ms   : {plan[0].get('Planning Time')}")
if __name__=='__main__': main()
