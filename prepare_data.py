''' VISUO 3D
    v.17.03.12
    Written by Mike Cichonski
    for the Science of Imagination Laboratory
    Carleton University

    File: prepare_data.py
	This file processes all the csv files output by collect_data.py and
	averages/fuzzifies the values of the object triplets.
	It also estimates relative object sizes using the object's
	largest measured diameter (unable to find true volume due 
	to incomplete 3d data). Requires output from collect_data.py.'''

import fnmatch
import os
import sys
import ast
import pickle
from math import sqrt

from modules.timer import *
from modules import progress_bar

if __name__=="__main__":

    # average or fuzzify triplet values =======================###
    sys.stdout.write("Averaging triplets..."); sys.stdout.flush()
    timer = startTimer()
    files = []
    for root, dirnames, filenames in os.walk('data'):
        for filename in fnmatch.filter(filenames, '*.csv'):
            files.append(os.path.join(root, filename))

    averages = {}
    for filename in files:
        if filename=='data/object_sizes.csv' or filename=='data/triplets.csv':
            continue # skip these because they are the output of the current script
        csv = open(filename,'r')
        lines = csv.readlines()

        for line in lines[1:]:
            values = line.split(',')
            objA = values[0].rsplit('_',1)[0]
            objB = values[1].rsplit('_',1)[0]
            objC = values[2].rsplit('_',1)[0]
            triplet = ','.join([objA,objB,objC])
            distAB = float(values[3])
            distAC = float(values[4])
            distAO = float(values[5])
            angBAC = float(values[6])
            angOAB = float(values[7])
            angOAC = float(values[8])
            if triplet not in averages:
                averages[triplet] = [[distAB],[distAC],[distAO],[angBAC],[angOAB],[angOAC],1]
                continue
            averages[triplet][0].append(distAB)
            averages[triplet][1].append(distAC)
            averages[triplet][2].append(distAO)
            averages[triplet][3].append(angBAC)
            averages[triplet][4].append(angOAB)
            averages[triplet][5].append(angOAC)
            averages[triplet][6]+=1

    triplets = {}
    tripletsFile = open('data/triplets.csv','w')
    tripletsFile.write("objectA,objectB,objectC,distanceAB,distanceAC,"+\
                       "distanceAO,angleBAC,angleOAB,angleOAC,count\n")
    for triplet, values in averages.items():
        triplets[triplet] = [sum(values[0])/values[6],\
                             sum(values[1])/values[6],\
                             sum(values[2])/values[6],\
                             sum(values[3])/values[6],\
                             sum(values[4])/values[6],\
                             sum(values[5])/values[6],values[6]]
        text = ','.join([str(t) for t in triplets[triplet]])
        tripletsFile.write(triplet+','+text+'\n')
    tripletsFile.close()

    with open ('data/triplets.pickle','wb') as handle:
        pickle.dump(triplets,handle,protocol=pickle.HIGHEST_PROTOCOL)
    handle.close()
    sys.stdout.write("Done! (%s sec.)\n"%str(endTimer(timer))); sys.stdout.flush()

    # approximate object sizes =============================###

    sys.stdout.write("Approximating object diameters...\n"); sys.stdout.flush()
    timer = startTimer()
    files3D = []
    cenFiles = []
    count = 0
    for root, dirnames, filenames in os.walk('data'):
        for filename in fnmatch.filter(filenames,'*.3d'):
            files3D.append(os.path.join(root,filename))
        for filename in fnmatch.filter(filenames,'*.cen'):
            cenFiles.append(os.path.join(root,filename)) 

    files3D.sort()
    cenFiles.sort()
    
    progress_bar.update (0,len(cenFiles),prefix='Progress:')
    sizes = {}
    for index, cenFile in enumerate(cenFiles):
        theFile = open(cenFile,'r')
        lines = theFile.readlines()
        centroids = {}
        for line in lines:
            splitline = line.split(' [')
            centroids[splitline[0]] = ast.literal_eval('['+splitline[1])
        theFile.close()
        theFile = open(files3D[index],'r')
        lines = theFile.readlines()
        distances = {} # of each object's 3d points from centroid
        name = lines[0].rstrip('\n')
        for line in lines:
            if line=='':
                continue
            elem = ast.literal_eval(line)
            if len(elem)!=3:
                name = repr(elem).rstrip('\n')
            else:
                x1,y1,z1 = [centroids[name][0],centroids[name][1],\
                            centroids[name][2]]
                x2,y2,z2 = [elem[0],elem[1],elem[2]]
                if name not in distances:
                    distances[name] = [sqrt((x2-x1)**2 + (y2-y1)**2 +\
                                            (z2-z1)**2)]
                    continue
                distances[name].append(sqrt((x2-x1)**2 + (y2-y1)**2 +\
                                            (z2-z1)**2))

        for obj,dist in distances.items():
            distances[obj] = max(dist)
        
        for obj,dist in distances.items():
            newName = ast.literal_eval(obj)[0].replace(' ','_')
            if newName not in sizes:
                sizes[newName] = [dist]
                continue
            sizes[newName].append(dist)    
        theFile.close()
        progress_bar.update(index,len(cenFiles),\
                         prefix='Progress:',suffix=str(index)+'/'+str(len(cenFiles)))

    progress_bar.update(len(cenFiles),len(cenFiles),\
                         prefix='Progress:',suffix=str(len(cenFiles))+'/'+str(len(cenFiles)))

    for obj,dist in sizes.items():
        numObjects = len(dist)
        #multiply by 2 below to change radius to diameter
        sizes[obj] = str(max(dist)*2)+','+str(numObjects)
    
    outFile = open("data/object_sizes.csv",'w')
    outFile.write("object,diameter,count\n")
    for obj,dist in sizes.items():
        outFile.write(obj+','+str(dist)+'\n')
    outFile.close()
    sys.stdout.write("Processed in %s sec.\n"%str(endTimer(timer)));
    sys.stdout.flush()
