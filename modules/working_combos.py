''' VISUO 3D
    v.17.07.17
    Written by Mike Cichonski
    for the Science of Imagination Laboratory
    Carleton University

    File: working_combos.py
    This module is written for python3 and the functions are
    meant to be called from inside the run.py script.'''

import sys
import pickle
#import linecache
from itertools import combinations
import progress_bar
from timer import *


def determine():
    '''determine all combinations of objects that can
    be used as input into VISUO3D (run.py script) given
    the data gathered into the data/triplets.pickle file 
    output: data/working-combos.csv'''
    
    print ("Determining working combos...")
    dictionary = pickle.load(open("data/triplets.pickle",'rb'))
    combos = triplets = list(dictionary.keys())
    pairs = [','.join(key.split(',')[:-1]) for key in triplets]
    maxIter = 3 # max number of items per combination
    x = list({p:min(pairs.count(p)-1,maxIter-1) \
                for p in pairs if pairs.count(p)>1}.values())
    sumIter = sum(x)    
    pairs = list(set(pairs))

    # set up progress bar
    progress = 0
    longest = max(len(p) for p in pairs) # longest string in pairs    
    progress_bar.update (0,sumIter,prefix='Progress:')
    
    cmb = {}
    time = startTimer()
    for pair in pairs:
        objs = [t.split(',')[-1] for t in triplets if t.startswith(pair)]
        if len(objs)<2: continue
        for i in range(2, min(maxIter+1,len(objs)+1)):
        # iterate up to combinations of no more than maxIter objects for efficiency          
            currKey = ','.join(sorted(objs))            
            if currKey in cmb:
                if i in cmb[currKey]:
                    currP = cmb[currKey][i]
                else:            
                    currP = combinations(objs,i) # expensive operation
                    cmb[currKey][i] = currP
            else:
                currP = combinations(objs,i) # expensive operation
                cmb[currKey] = {}
                cmb[currKey][i] = currP
            current = [','.join([pair]+list(o)) for o in currP]
            del currP
            combos.extend(current)
            progress_bar.update(progress,sumIter,prefix='Progress:',suffix=pair+ \
                                '... + '+str(int(i))+' '*(longest-len(pair)+8))
            progress+=1

    endMessage = str(progress)+'/'+str(sumIter)+' Done!'+'('+str(time)+' sec)'
    endMessage = endMessage+' '*(longest-len(endMessage))
    progress_bar.update(progress,sumIter,prefix='Progress:',suffix=endMessage)

    wcFile = open("data/working-combos.csv",'w')
    for combo in combos:
        wcFile.write(combo+'\n')
    wcFile.close()

def load():
    '''load data/working-combos.csv and return
    a list containing this data'''
    wcFile = open("data/working-combos.csv",'r')
    lines  = wcFile.readlines()
    wcFile.close()
    wc = []    
    for line in lines:
        wc.append(line.split(','))
    return wc
