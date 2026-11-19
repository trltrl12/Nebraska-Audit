#!/usr/bin/env python3
"""Append applied cross-year page-check category changes to output/json/_meta/corrections_log.csv (idempotent)."""
import csv,glob,re
p='output/json/_meta/corrections_log.csv'
R=list(csv.DictReader(open(p)));F=list(R[0])
have={(r['stem'],r['container'],r['printed_label'],r['old_amount'],r['source_page'],r['new_category']) for r in R if r['stage']=='xyear page check'}
n=0
for f in sorted(glob.glob('output/json/_meta/xyear_decisions/*.csv')):
    for r in csv.DictReader(open(f)):
        if r['verdict'] in('same','differs') and r['old_category']!=r['category']:
            k=(r['stem'],r['where'],r['label'],str(r['amount']),str(r['source_page']),r['category'])
            if k in have:continue
            R.append({'stem':r['stem'],'fiscal_year':(re.search(r'FY(20\d\d)',r['stem']) or re.search(r'through_\w+?_\d+_(20\d\d)',r['stem'])).group(1),'stage':'xyear page check','type':'category_change','container':r['where'],'printed_label':r['label'],'old_amount':r['amount'],'new_amount':r['amount'],'old_category':r['old_category'],'new_category':r['category'],'source_page':r['source_page'],'evidence':r['evidence'],'verified_by':'cross-year side-by-side page read (Opus agent), manager spot-check','date':'2026-11-17'});n+=1
w=csv.DictWriter(open(p,'w',newline=''),fieldnames=F);w.writeheader();w.writerows(R);print('logged',n)
