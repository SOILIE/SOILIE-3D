import os
import pandas as pd

path = './3d/'
df = pd.read_csv(path+'asset_rotations.csv')

good_assets = df['asset_name'].values
obj_files = sorted([f for f in os.listdir(path) if f.lower().endswith('.obj')])

good = []
bad = []
for name in obj_files:
    if name in good_assets:
        good.append(name)
    else:
        bad.append(name)

len(good)
len(bad)

for name in bad:
    os.rename(path+name,path+'bad_assets/'+name)
