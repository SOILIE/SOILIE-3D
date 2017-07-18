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
import random
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
    combos = triplets = list(dictionary.keys()) # combos will be a buffer
    pairs = [','.join(key.split(',')[:-1]) for key in triplets]
    maxIter = 4 # max number of items per combination
    sumIter = sum(list({p:min(pairs.count(p)-1,maxIter-1) \
                  for p in pairs if pairs.count(p)>1}.values()))    
    pairs = list(set(pairs))

    # set up progress bar
    progress = 0
    longest = max(len(p) for p in pairs) # longest string in pairs    
    progress_bar.update (0,sumIter,prefix='Progress:')
    
    # open/create file
    wcFile = open("data/working-combos.csv",'w')
    [wcFile.write(combo+'\n') for combo in combos] # write triplets set
    combos = [] # clear buffer
    
    time = startTimer()
    for pair in pairs:
        objs = [t.split(',')[-1] for t in triplets if t.startswith(pair+',')]
        if len(objs)<2: continue
        for i in range(2, min(maxIter+1,len(objs)+1)):
            current = [','.join([pair]+list(o)) for o in combinations(objs,i)]
            combos.extend(list(set(current)))            
            progress_bar.update(progress,sumIter,prefix='Progress:',suffix=pair+ \
                                '... + '+str(int(i))+' '*(longest-len(pair)+8)) 
            progress+=1     
        [wcFile.write(combo+'\n') for combo in combos] # write to file
        combos = [] # clear buffer
    wcFile.close()

    endMessage = str(progress)+'/'+str(sumIter)+' Done! '+'('+str(endTimer(time))+' sec)'
    endMessage = endMessage+' '*(longest-min(longest,len(endMessage)))
    progress_bar.update(sumIter,sumIter,prefix='Progress:',suffix=endMessage)

def load(n):
    '''load data/working-combos.csv and return a random
    entry containing n objects
    n: number of objects'''
    line = ''
    while len(line.split(','))!=n:
        wcFile = open("data/working-combos.csv",'r')
        line = next(wcFile)
        for i, l in enumerate(wcFile):
            if random.randrange(i+2): continue
            line = l.rstrip('\n')
            break
        wcFile.close()
    return line.split(',')
