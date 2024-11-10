''' VISUO 3D
    v.24.07.05
    Written by Mike Cichonski
    for the Science of Imagination Laboratory
    Carleton University

    File: working_combos.py
    This module is written for python3 and the functions are
    meant to be called from inside the run.py script.'''

import sys
import pickle
import random
import pandas as pd
from itertools import combinations
from modules import progress_bar
from modules.timer import *

def determine():
    '''determine all combinations of objects that can
    be used as input into SOILIE3D (imagine.py script) given
    the data gathered into the data/triplets.csv file
    output: data/working-combos.csv'''

    print ("Determining working combos...")
    #pickleData = pickle.load(open("data/triplets.pickle",'rb'))

    df = pd.read_csv('./data/triplets.csv')
    df = df.drop_duplicates(subset=['objectA','objectB','objectC'],keep='first')
    dictionary = {}
    for i, row in df.iterrows():
        objA = row['objectA']
        objB = row['objectB']
        objC = row['objectC']
        comboData = [row['distanceAB'],row['distanceAC'],row['distanceAO'],
                     row['angleBAC'],row['angleOAB'],row['angleOAC'],1]
        dictionary[f'{objA},{objB},{objC}'] = comboData

    combos = triplets = list(dictionary.keys()) # combos will be a buffer
    pairs = [','.join(key.split(',')[:-1]) for key in triplets]
    maxIter = 4 # max number of items per combination
    sumIter = sum(list({p:min(pairs.count(p)-1,maxIter-1) \
                  for p in pairs if pairs.count(p)>1}.values()))
    extraIter = int(sumIter/10) if int(sumIter/10)%2==0 else int(sumIter/10+1)
    pairs = list(set(pairs))

    # set up progress bar
    progress = 0
    longest = max(len(p) for p in pairs) # longest string in pairs
    progress_bar.update(0,sumIter+extraIter,prefix='Progress:')

    # open/create file
    wcFile = open("data/working-combos.csv",'w')
    wcFile.write('obj0,obj1,obj2,obj3,obj4,obj5\n')
    [wcFile.write(combo+'\n') for combo in combos] # write triplets set
    combos = [] # clear buffer

    time = startTimer()
    for pair in pairs:
        objs = [t.split(',')[-1] for t in triplets if t.startswith(pair+',')]
        if len(objs)<2: continue
        for i in range(2, min(maxIter+1,len(objs)+1)):
            current = [','.join([pair]+list(o)) for o in combinations(objs,i)]
            combos.extend(list(set(current)))
            progress_bar.update(progress,sumIter+extraIter,prefix='Progress:',suffix=pair+'... + '+str(int(i))+' '*(longest-len(pair)+8))
            progress+=1
        [wcFile.write(combo+'\n') for combo in combos] # write to file
        combos = [] # clear buffer
    wcFile.close()

    # add refined working combos file
    df = pd.read_csv('./data/working-combos.csv')
    keep = ['bathtub','bed','bin','blinds','book','bookcase','boots','bowl','cabinet','cap','cereal_box','chair','clock',
            'closet','coffee_machine','coffee_mug','coffee_table','computer_bag','cupboard','curtain','desk','dining_table',
            'fridge','helmet','lamp','laptop','microwave_oven','night_stand','office_chair','pillow','plant','printer',
            'projector','sink','soda_can','sofa','sofa_chair','speaker','stand','suitcase','table','telephone','toilet',
            'towel','trash_can','trash_bin','TV','tv_stand','window','']
    df1 = df[(df['obj0'].isin(keep))&(df['obj1'].isin(keep))&(df['obj2'].isin(keep))&
             (df['obj3'].isin(keep))&(df['obj4'].isin(keep))&(df['obj5'].isin(keep))]
    df1.to_csv('./data/working-combos-refined.csv',index=False)
    progress+=extraIter/5
    progress_bar.update(progress,sumIter+extraIter,prefix='Progress:',suffix='Refined set...'+' '*(longest+3))

    # bedroom combos
    keep_br = ['bed','bin','blinds','book','bookcase','boots','bowl','cabinet','cap','chair','clock','closet','coffee_mug',
               'computer_bag','cupboard','curtain','desk','helmet','lamp','laptop','night_stand','office_chair','pillow',
               'plant','printer','projector','soda_can','sofa_chair','speaker','stand','suitcase','table','telephone',
               'trash_bin','TV','tv_stand','window','']
    df2 = df[(df['obj0'].isin(keep_br))&(df['obj1'].isin(keep_br))&(df['obj2'].isin(keep_br))&
             (df['obj3'].isin(keep_br))&(df['obj4'].isin(keep_br))&(df['obj5'].isin(keep_br))]
    df2.to_csv('./data/working-combos-bedroom.csv',index=False)
    progress+=extraIter/5
    progress_bar.update(progress,sumIter+extraIter,prefix='Progress:',suffix='Bedroom set...'+' '*(longest+3))

    # living room combos
    keep_lr = ['bin','blinds','book','bookcase','boots','bowl','cabinet','cap','cereal_box','chair','clock',
               'coffee_mug','coffee_table','computer_bag','cupboard','curtain','desk','dining_table','helmet',
               'lamp','laptop','office_chair','plant','printer','projector','soda_can','sofa','sofa_chair',
               'speaker','stand','suitcase','table','telephone','trash_bin','TV','tv_stand','window','']
    df3 = df[(df['obj0'].isin(keep_lr))&(df['obj1'].isin(keep_lr))&(df['obj2'].isin(keep_lr))&
             (df['obj3'].isin(keep_lr))&(df['obj4'].isin(keep_lr))&(df['obj5'].isin(keep_lr))]
    df3.to_csv('./data/working-combos-livingroom.csv',index=False)
    progress+=extraIter/5
    progress_bar.update(progress,sumIter+extraIter,prefix='Progress:',suffix='Living room set...'+' '*(longest-1))

    # bathroom combos
    keep_ba = ['bathtub','bin','cabinet','lamp','sink','toilet','towel','trash_bin','']
    df4 = df[(df['obj0'].isin(keep_ba))&(df['obj1'].isin(keep_ba))&(df['obj2'].isin(keep_ba))&
             (df['obj3'].isin(keep_ba))&(df['obj4'].isin(keep_ba))&(df['obj5'].isin(keep_ba))]
    df4.to_csv('./data/working-combos-bathroom.csv',index=False)
    progress+=extraIter/5
    progress_bar.update(progress,sumIter+extraIter,prefix='Progress:',suffix='Bathroom set...'+' '*(longest+2))

    # kitchen combos
    keep_ki = ['bin','blinds','cabinet','cap','cereal_box','chair','clock','coffee_machine','coffee_mug',
               'coffee_table','cupboard','curtain','dining_table','fridge','lamp','microwave_oven','plant',
               'sink','soda_can','table','telephone','towel','trash_can','trash_bin','window','']
    df5 = df[(df['obj0'].isin(keep_ki))&(df['obj1'].isin(keep_ki))&(df['obj2'].isin(keep_ki))&
             (df['obj3'].isin(keep_ki))&(df['obj4'].isin(keep_ki))&(df['obj5'].isin(keep_ki))]
    df5.to_csv('./data/working-combos-kitchen.csv',index=False)
    progress+=extraIter/5
    progress_bar.update(progress,sumIter+extraIter,prefix='Progress:',suffix='Kitchen set...'+' '*(longest+3))

    endMessage = str(progress)+'/'+str(sumIter)+' Done! '+'('+str(endTimerPretty(time))+')'
    endMessage = endMessage+' '*(longest-min(longest,len(endMessage)))
    progress_bar.update(1,1,prefix='Progress:',suffix=endMessage)


def weighted_random_selection(df,n):
    # Flatten the DataFrame to get a list of all items
    all_items = df.values.flatten()

    # Calculate the frequency of each item across all columns
    item_frequencies = pd.Series(all_items).value_counts().to_dict()

    # Find the min and max frequencies
    min_freq = min(item_frequencies.values())
    max_freq = max(item_frequencies.values())

    # Calculate scale factor to make the most common row slightly (1.2x) more frequent than least common
    scale_factor = 1.2 / min_freq

    # Calculate the weight for each row based on the scaled frequencies of its items
    weights = []
    for _, row in df.iterrows():
        row_weight = 1
        max_num = len(row)
        row = [x for x in row if x!=''] # exclude counting blanks
        for item in row:
            scaled_frequency = item_frequencies[item] * scale_factor * (max_num/len(row))
            row_weight *= 1 / item_frequencies[item]
            row_weight *= scaled_frequency
        weights.append(row_weight)

    # Normalize the weights so they sum to 1
    total_weight = sum(weights)
    normalized_weights = [weight / total_weight for weight in weights]

    # Select a row based on the weights
    selected_row = df.iloc[random.choices(df.index, weights=normalized_weights, k=1)[0]]
    return selected_row.values[:n].tolist()


def load(n,filepath="./data/working-combos-refined.csv",sampling_method='weighted'):
    '''load data/working-combos.csv and return a random
    entry containing n objects
    n: number of objects'''
    df = pd.read_csv(filepath)
    if sampling_method=='random':
        sample = df.sample().values[0,:n].tolist()
    elif sampling_method=='weighted':
        sample = weighted_random_selection(df,n)
    return sample
