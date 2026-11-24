import glob, pandas as pd, sys
files = sorted(glob.glob('data/raw/20*_%e5%b0%8f*.ods'))
dfs=[]
for f in files:
    x = pd.read_excel(f, engine='odf', sheet_name=None)
    for sh, d in x.items():
        d['__file']=f.split('/')[-1][:17]; d['__sheet']=sh; dfs.append(d)
    print(f.split('/')[-1][:17], {k:v.shape for k,v in x.items()})
df = pd.concat(dfs, ignore_index=True)
print(df.columns.tolist()); print(df.head(5).to_string()); print(df.dtypes)
import os; os.makedirs('data/interim', exist_ok=True); df.to_pickle('data/interim/aq_all.pkl')
