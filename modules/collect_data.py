''' File: collect_data.py
	This file traverses the selected dataset(s) and calculates
	angles and distances between object triplets in each frame.
	Requires JSON label files in the json folder.'''

import os, json, random, sys, urllib.request, re, gc

from io import BytesIO
import fnmatch
import numpy as np
import pandas as pd
np.set_printoptions(threshold=sys.maxsize)
from mpl_toolkits.mplot3d import Axes3D
import matplotlib
matplotlib.use("Agg")
#matplotlib.use("QtAgg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import matplotlib.path as path
from PIL import Image, ImageDraw
from os import makedirs, listdir
from os.path import join, isfile, isdir, dirname, abspath, splitext, exists
from itertools import permutations

from modules import menu
from modules import import_tools as imp
from modules.timer import *
from modules import progress_bar


## declare objects ---------------------------------------------- ##

class Frame:
    '''represents a single frame'''

    def __init__(self, ID, width, height):
        '''creates a new frame'''
        self.ID = ID           # the frame number
        self.row = []	       # a list of y values
        self.col = []          # a list of x values
        self.data = []         # a list of data corresponding to x and y location
        self.width = width     # width of frame (y)
        self.height = height   # height of frame (x)
        self.loc = None        # location of recording
        self.objects = []      # objects in frame
        self.labels = {}       # {objectName : (x,y) centroid} pairs (2D)
        self.centroids = {}    # {objectName : (x,y,z) centroids in meters} (3D)
        self.objects3d = {}    # {objectName : [(x,y,z)...(x,y,z)] } all 3d points
        self.combos = []       # list of [labelA,labelB,labelC,angZAB,angZAC,angBAC,distAB]
        self.intrinsics = []   # camera intrinsics
        self.extrinsics = []   # camera extrinsics
        self.image = None      # image to export
        self.background = None # original image
        self.depthMap = None   # depth map
        self.xyzCamera = []    # xyz camera coords for entire frame
        self.xyz = []          # xyz world coords for entire frame
        self.valid = []        # valid world coords corresponding to each [x,y] location

    def addData(self,data,x,y):
        '''add data at specified x,y location'''
        self.row.append(y)
        self.col.append(x)
        self.data.append(data)

    def addObject(self,obj):
        self.objects.append(obj)

    def extract_pixel_colors(self,name,coords):
        # Create a mask image of the same size as the original image
        mask_image = Image.new('L', self.background.size, 0)
        ImageDraw.Draw(mask_image).polygon(coords, outline=1, fill=1)
        mask = mask_image.load()
        pixels = self.background.load()
        # Collect unique RGB values within the polygon and count their occurrences
        color_counts = {}
        for y in range(self.background.size[1]):
            for x in range(self.background.size[0]):
                if mask[x, y] == 1:
                    color = pixels[x, y][:3]
                    # exclude pixels that are close enough to fully black or white
                    if all(c<20 for c in color) or all(c>235 for c in color):
                        continue
                    elif color in color_counts:
                        color_counts[color] += 1
                    else:
                        color_counts[color] = 1
        # Drop color counts with less than 25 pixels (these are extreme outliers)
        color_counts = {col:c for col,c in color_counts.items() if c>25}
        data = {'object_name': [], 'R': [], 'G': [], 'B': [], 'count': []}
        for color, count in color_counts.items():
            data['object_name'].append('_'.join(eval(name)[0].split()))
            data['R'].append(color[0])
            data['G'].append(color[1])
            data['B'].append(color[2])
            data['count'].append(count)
        df = pd.DataFrame(data)
        obj_colors_filename = './data/object_colors.csv'
        if exists(obj_colors_filename):
            # combine counts from previous runs and re-save csv
            df_old = pd.read_csv(obj_colors_filename)
            df_new = pd.concat([df_old,df])
            df = df_new.groupby(['object_name', 'R', 'G', 'B'], as_index=False)['count'].sum().reset_index()
        if 'index' in df.columns:
            df.drop(columns=['index'],inplace=True)
        df.to_csv(obj_colors_filename,index=False)


    def update(self):
        '''update matrix and image'''
        dataNames = [x.ID for x in self.data]
        self.image = Image.new('RGBA',(self.width,self.height))
        pix = self.image.load()
        for i,pixel in enumerate(zip(self.col, self.row)):
            pix[pixel[0]-1,pixel[1]-1] = self.data[i].colour
        # camera coords
        sys.stdout.write("\tretrieving camera coords:"); sys.stdout.flush()
        t1 = startTimer() #time to retrieve camera coords
        cameraCoords = imp.depth2XYZcamera(self.intrinsics,self.depthMap)
        self.xyzCamera = imp.getCameraCoords(cameraCoords)
        sys.stdout.write("\t%s\n"%str(endTimerPretty(t1))); sys.stdout.flush()
        # world coords
        sys.stdout.write("\tconverting to world coords:"); sys.stdout.flush()
        t2 = startTimer() #time to change from camera to world coords
        (xyzWorld,self.valid) = imp.camera2XYZworld(cameraCoords,self.extrinsics)
        self.xyz = np.transpose(xyzWorld)
        sys.stdout.write("\t%s\n"%str(endTimerPretty(t2))); sys.stdout.flush()
        return self # to enable cascading

    def calculateCentroids(self,polygons):
        '''calculate per-frame centroids of polygons and add to centroids dictionary
        polygons:   a dictionary of object_name:[(x,y)..(x,y)] pairs'''
        sys.stdout.write("\tcalculating centroids:"); sys.stdout.flush()
        t4 = startTimer() #time to calculate the 2D and 3D centroids
        # Get 2D centroids (to place labels in correct place in image)
        for name,coords in polygons.items():
            centX = sum([x for (x,y) in coords])/len(coords)
            centY = sum([y for (x,y) in coords])/len(coords)
            self.labels[name] = (centX,centY)
        # Get 3D centroids (using only the points visible in current frame)
        rangeX = range(self.height)
        rangeY = range(self.width)
        template = [(y,x) for y in rangeY for x in rangeX]
        for name, allCoords in polygons.items():
            coords = [c for c in allCoords if c[0]<self.width \
		      and c[1]<self.height and c[0]>=0 and c[1]>=0]
            if not coords: continue
            vertices = path.Path(coords,closed=True)
            objectPts = vertices.contains_points(template)
            objectPts = np.reshape(objectPts,(self.width,self.height))
            count = 0
            pts3d = []
            for y,col in enumerate(objectPts):
                for x,row in enumerate(col):
                    if row and self.valid[x][y]:
                        #xyzCoords = self.xyz[count] # Uses xyz world coords
                        xyzCoords = self.xyzCamera[count] # Uses xyz camera coords
                        pts3d.append(xyzCoords)
                    if self.valid[x][y]:
                        count+=1
            X = [item[0] for item in pts3d]
            Y = [item[1] for item in pts3d]
            Z = [item[2] for item in pts3d]
            if len(X) == 0:
                continue
            X = sum(X)/len(X)
            Y = sum(Y)/len(Y)
            Z = sum(Z)/len(Z)
            self.objects3d[name] = pts3d
            self.centroids[name] = (X,Y,Z)

        sys.stdout.write("\t\t%s\n"%str(endTimerPretty(t4))); sys.stdout.flush()
        return self


    def drawPolygons(self, polygons, datasetName):
        '''draw a set of polygons filled with colour of corresponding objects
        polygons:   a dictionary of object_name:[(x,y)..(x,y)] pairs'''
        sys.stdout.write("\tdrawing polygons:"); sys.stdout.flush()
        t6 = startTimer() #time to draw polygons
        if self.image == None:
            print("ERROR: Can't draw to empty image! (update first)")
        else:
            draw = ImageDraw.Draw(self.image)
            for name,coord in polygons.items():
                colour = (255,255,255,140)
                for o in self.objects:
                    if str(o.getName()) == name:
                        colour = o.colour
                # extract pixel colors and store in memory as csv
                self.extract_pixel_colors(name,coord)
                # draw the colored polygon
                draw.polygon(coord,fill=colour)
            for name,coord in self.labels.items():
                if name not in self.centroids:
                    errLog = open(join('data',datasetName,self.loc,'error.log'),'a')
                    errLog.write(str(self.ID)+". "+name+"\tat "+self.loc+'\n')
                    errLog.close()
                    draw.text(coord,name+'\n(no xyz data)',fill="black")
                    continue
                x= "%.1f" %self.centroids[name][0]
                y= "%.1f" %self.centroids[name][1]
                z= "%.1f" %self.centroids[name][2]
                draw.text(coord,name+'\n('+x+','+y+','+z+')',fill="black")
            del draw
        sys.stdout.write("\t\t%s\n"%str(endTimerPretty(t6))); sys.stdout.flush()
        return self

    def process3dPoints(self,filePath,plot):
        '''write 3d points for each object to a file, and if option is enabled,
           plot the 3d points in an image with different colour for each object
        filePath:   the path to save the plots
        plot:       number of views for each frame'''
        sys.stdout.write("\tprocessing 3d data:"); sys.stdout.flush()
        t7 = startTimer() #time to process 3d data
        saveLoc = join(filePath,str(self.ID))
        plotFile = open(saveLoc+'.3d','w')
        patches = []
        #-----------------------------------------------#
        if plot:
            if not exists(saveLoc+'/'):
                makedirs(saveLoc+'/')
            fig = plt.figure()
            ax = fig.add_subplot(111,projection='3d')
            ax.scatter([0],[0],[0],c='r',marker='o')
            #fig.clf()
        #-----------------------------------------------#
        for name, coords in self.objects3d.items():
            plotFile.write(str(name))
            for i, coord in enumerate(coords):
                coord = "\n"+str(tuple(coord))
                plotFile.write(coord)
            plotFile.write("\n")
            #---------------------------------------------------------------#
            if not plot: continue
            # get colour
            colour = [1,1,1]
            for o in self.objects:
                if str(o.getName()) == name:
                    colour = [float(c)/255 for c in o.colour[:-1]]
                    patches.append(mpatches.Patch(color=colour,label=name))
            xyz = np.transpose(np.array(coords))
            if xyz.size > 0:
                ax.scatter([xyz[0]],[xyz[1]],[xyz[2]],color=colour,marker='.',alpha=0.003)
            if name in self.centroids:
                xyzCen = self.centroids[name] # add centroid
                ax.scatter([xyzCen[0]],[xyzCen[1]],[xyzCen[2]],color=[0,0,0],marker='x')
            #---------------------------------------------------------------#
        plotFile.close()
        sys.stdout.write("\t\t%s\n"%str(endTimerPretty(t7))); sys.stdout.flush()
        #-------------------------------------------------------------------#
        if plot:
            sys.stdout.write("\tplotting 3d points:"); sys.stdout.flush()
            t8 = startTimer()
            ax.set_xlabel('X-axis')
            ax.set_ylabel('Y-axis')
            ax.set_zlabel('Z-axis')
            ax.legend(handles=patches,loc=3,\
                  bbox_to_anchor=[float(-0.17),float(-0.15)],fontsize=7.5)
            curFig = plt.gcf()
            ax.azim=0
            for count in range(0,plot):
                curFig.savefig(saveLoc+'/'+str(self.ID)+'-'+'0'*(3-len(str(int(ax.azim))))+str(int(ax.azim))+'_3d.png', dpi=100)
                ax.azim+=int(360/plot)
            sys.stdout.write("\t\t%s\n"%str(endTimerPretty(t8))); sys.stdout.flush()
        plt.close()
        #------------------------------------------------------------------#
        return self


    def getAngleDistCombos(self,centroidFile):
        '''get a set of angles and distances between objects'''
        #sys.stdout.write("\tcalculating triplets:"); sys.stdout.flush()
        t5 = startTimer()

        frameObjects = list(self.centroids.keys())
        df_c = pd.read_csv(centroidFile)
        for i,row in df_c.iterrows():
            obj = row['object']
            if obj in frameObjects:
                self.centroids[obj] = [row['X'],row['Y'],row['Z']]

        names = []
        for name, _ in self.centroids.items():
            names.append(name)
        combos = []
        for combo in permutations(names,3):
            combos.append('---'.join(combo).split('---'))
        for combo in combos:
            A = np.array(self.centroids[combo[0]])
            B = np.array(self.centroids[combo[1]])
            C = np.array(self.centroids[combo[2]])
            O = np.array([0,0,0])
            BA = B - A
            CA = C - A
            OA = O - A
            # distance AB, AC, AO
            distAB = np.linalg.norm(A-B)
            distAC = np.linalg.norm(A-C)
            distAO = np.linalg.norm(A-O)
            # angle BAC
            cosBAC   = np.dot(BA, CA) / (np.linalg.norm(BA) * np.linalg.norm(CA))
            angleBAC = np.degrees(np.arccos(cosBAC))
            # angle OAB
            cosOAB   = np.dot(OA, BA) / (np.linalg.norm(OA) * np.linalg.norm(BA))
            angleOAB = np.degrees(np.arccos(cosOAB))
            # angle OAC
            cosOAC   = np.dot(OA, CA) / (np.linalg.norm(OA) * np.linalg.norm(CA))
            angleOAC = np.degrees(np.arccos(cosOAC))
            # add to self.combos and format label names
            nameA = combo[0].lstrip("[").rstrip("]").replace("'","").split(",")
            nameA = "_".join([nameA[0].replace(" ","_"),nameA[1].replace(" ","")])
            nameB = combo[1].lstrip("[").rstrip("]").replace("'","").split(",")
            nameB = "_".join([nameB[0].replace(" ","_"),nameB[1].replace(" ","")])
            nameC = combo[2].lstrip("[").rstrip("]").replace("'","").split(",")
            nameC = "_".join([nameC[0].replace(" ","_"),nameC[1].replace(" ","")])
            self.combos.append([nameA,nameB,nameC,str(distAB),str(distAC),str(distAO),\
                                str(angleBAC),str(angleOAB),str(angleOAC)])
        #sys.stdout.write("\t\t%s\n"%str(endTimerPretty(t5))); sys.stdout.flush()
        return self


    def export(self,filePath):
        '''export matrix and image files
        filePath:   the path to the file'''
        #sys.stdout.write("\texporting files:")
        t9 = startTimer() #time to draw polygons
	    #export per-frame centroid file
        # centroidFile = open (join(filePath,str(self.ID)+'.cen'),'w')
        # for objName,coords in self.centroids.items():
        #     item = objName+' ['+str(coords[0])+','+str(coords[1])+ \
        #                 ','+str(coords[2])+']\n'
        #     centroidFile.write(item)
        # centroidFile.close()
	    #export csv file
        comboDict = {'objectA':[],'objectB':[],'objectC':[],'distanceAB':[],'distanceAC':[],
                     'distanceAO':[],'angleOAB':[],'angleOAC':[],'angleBAC':[]}
        for combo in self.combos:
            comboDict['objectA'].append(combo[0])
            comboDict['objectB'].append(combo[1])
            comboDict['objectC'].append(combo[2])
            comboDict['distanceAB'].append(combo[3])
            comboDict['distanceAC'].append(combo[4])
            comboDict['distanceAO'].append(combo[5])
            comboDict['angleOAB'].append(combo[6])
            comboDict['angleOAC'].append(combo[7])
            comboDict['angleBAC'].append(combo[8])
        df = pd.DataFrame.from_dict(comboDict)
        df.to_csv(join(filePath,str(self.ID)+'.csv'), index=False)

        # export image file
        image = self.image
        if self.background:
            image = Image.alpha_composite(self.background,image).convert('RGB')
            image.save(join(filePath,str(self.ID)+'.jpg'))
            image.close()
        #sys.stdout.write("\t\t%s\n"%str(endTimerPretty(t9))); sys.stdout.flush()
        return self


class SceneObject:
    '''represents an object in the scene'''

    def __init__(self, ID, name):
        self.ID = ID
        self.name = name
        self.colour = self.setRandomColour()
        self.frames = {}
        self.oldIDs = []

    def updateID(self, ID):
        if ID != self.ID:
            self.oldIDs.append(self.ID)
        self.ID = ID

    def setRandomColour(self):
        R = int(random.random() * 255)
        G = int(random.random() * 255)
        B = int(random.random() * 255)

        if R < 100 and G < 100 and B < 100:
            col = random.randrange(0,3)
            if col == 0:
                R+=100
            elif col == 1:
                G+=100
            else:
                B+=100
        return (R,G,B,140) # return in RGBA format (transparency hard-coded at 140)

    # get object name in format: [object,identifier]
    def getName(self):
        splitName = self.name.split(':')
        n = 0
        while n < len(splitName):
            splitName[n] = str(splitName[n].strip())
            n+=1
        if len(splitName) == 1:
            splitName.append(None)
        return splitName

    # add Frame to frames dictionary
    def addFrame(self,location,f):
        if location not in self.frames:
            self.frames[location] = []
        if f not in self.frames[location]:
            self.frames[location].append(f)

#pathTo3dFiles = 'data/princeton/hotel_umd/maryland_hotel3/'
## -------------------------------------------------------------- ##
def calculateCentroidsForScene(pathTo3dFiles,allObjects):
    ''' Calculate all object centroids for an entire scene.
    This function is meant to be run after the .3d files have been generated
    for all frames of a scene.
    pathTo3dFiles: The path to the .3d files for all frames of a scene.
    '''
    print('\n\nCalculating object centroids for entire scene...')
    plot = 3
    files3D = []
    for root, dirnames, filenames in os.walk(pathTo3dFiles):
        for filename in fnmatch.filter(filenames,'*.3d'):
            files3D.append(os.path.join(root,filename))

    files3D.sort() # regular in-place sort first
    files3D.sort(key=len) # then sort by filename length, as this correctly sorts number strings alphabetically

    pts3d = {}
    progress_total = len(files3D) + len(pts3d.keys()) + len(pts3d.keys())*plot + plot # total progress needed to achieve 100% completion
    progress_so_far = 0 # keep track of progress so far
    for i, filename in enumerate(files3D):
        progress_bar.update(progress_so_far,progress_total,suffix='Obtaining 3D points: ' + filename)
        theFile = open(filename,'r')
        contents = theFile.read()
        contents = contents.replace(')[',')\n[').replace(') [',')\n[')
        lines = contents.splitlines()
        for line in lines:
            vals3d = list(json.loads(line.replace("'","\"").replace('(','[').replace(')',']').replace('None','null')))
            if len(vals3d)!=3:
                name = repr(vals3d).rstrip('\n')
                if name not in pts3d:
                    pts3d[name] = []
                continue
            pts3d[name].append(vals3d)
        progress_total = len(files3D) + len(pts3d.keys()) + len(pts3d.keys())*plot + plot # adjust progress_total as pts3d accumulates more data
        progress_so_far += 1

    maxFilenameLength = max([len(f) for f in files3D])
    maxNameLength = max([len(o) for o in pts3d.keys()])

    centroids = {}
    df_dict = {'object':[],'X':[],'Y':[],'Z':[]}
    for i, obj in enumerate(pts3d.keys()):
        progress_bar.update(progress_so_far,progress_total,suffix='Calculating centroids for ' + obj + ' '*(maxFilenameLength-len(obj)-4))
        centroids[obj] = np.mean(pts3d[obj],axis=0)
        centroid = centroids[obj]
        x,y,z = centroid[0],centroid[1],centroid[2]
        df_dict['object'].append(obj)
        df_dict['X'].append(x)
        df_dict['Y'].append(y)
        df_dict['Z'].append(z)
        progress_so_far += 1

    df = pd.DataFrame.from_dict(df_dict)
    df.to_csv(join(pathTo3dFiles,'centroids.csv'),index=False)

    if not exists(join(pathTo3dFiles,'objects')):
        os.mkdir(join(pathTo3dFiles,'objects'))

    ############### save point cloud(s)
    fig = plt.figure()
    ax = fig.add_subplot(projection='3d')

    # load colors
    patches = []
    for i, obj in enumerate(pts3d.keys()):
        R,G,B = int(random.random() * 255),int(random.random() * 255),int(random.random() * 255)
        if R < 100 and G < 100 and B < 100:
            col = random.randrange(0,3)
            R += 100 if col == 0 else 0
            G += 100 if col == 1 else 0
            B += 100 if col == 2 else 0
        colour = [float(R)/255,float(G)/255,float(B)/255]
        #colour = [float(c)/255 for c in [o for o in allObjects if o.name==name][0].colour]
        patches.append(mpatches.Patch(color=colour,label=obj))

    ax.set_xlabel('X-Axis')
    ax.set_ylabel('Y-Axis')
    ax.set_zlabel('Z-Axis')
    ax.legend(handles=patches,loc=3,bbox_to_anchor=[float(-0.17),float(-0.15)],fontsize=7.5)
    curFig = plt.gcf()
    ax.azim=0

    for count in range(0,plot):
        for i, obj in enumerate(pts3d.keys()):
            # Save per-object point clouds
            progress_bar.update(progress_so_far,progress_total,suffix=f'Plotting {obj} point cloud {count+1}...'+' '*(maxNameLength-len(obj)+4))
            pts = np.array(pts3d[obj])
            centroid = centroids[obj]
            x,y,z = centroid[0],centroid[1],centroid[2]
            colour = patches[i].get_facecolor()[:3]
            ax.scatter(pts[:,0],pts[:,1],pts[:,2],color=colour,marker='o',alpha=0.003) # add xyz points to scatter plot with all objects
            ax.scatter(x,y,z,color=[0,0,0],marker='x') # add centroid to scatter plot with all objects
            ### per-object plot
            obj_fig = plt.figure()
            obj_ax = obj_fig.add_subplot(projection='3d')
            obj_ax.set_xlabel('X-Axis')
            obj_ax.set_ylabel('Y-Axis')
            obj_ax.set_zlabel('Z-Axis')
            obj_ax.scatter(pts[:,0],pts[:,1],pts[:,2],color=colour,marker='o',alpha=0.003) # add xyz points to per-object scatter plot
            obj_ax.scatter(x,y,z,color=[0,0,0],marker='x') # add centroid to per-object scatter plot
            obj_ax.azim = 0 + count*int(360/plot)
            obj_name = obj.replace('[','').replace(']','').replace(', ','_').replace(',','_').replace(" '",'').replace("'",'').replace(' ','_')
            obj_fig.savefig(join(pathTo3dFiles,'objects',f'{obj_name}_pc_{count+1}.png'), dpi=100) # save per-object plot
            plt.close(obj_fig)
            progress_so_far += 1
        # Save full point cloud
        progress_bar.update(progress_so_far,progress_total,suffix=f'Plotting full point cloud {count+1}...'+' '*(maxNameLength-4))
        curFig.savefig(join(pathTo3dFiles,f'point_cloud_{count+1}.png'), dpi=100) # save plot with all objects
        ax.azim+=int(360/plot)
        progress_so_far += 1
    #plt.show()
    plt.close(curFig)
    #################
    progress_bar.update(100,100,suffix=' '*(30+maxNameLength))


def processJSON(data, allObjects, currentPath, datasetName, local, plot):
    '''process each json file
    data:           the data contained in the json file
    currentPath:    the root path of the dataset
    datasetName:    the name of the database, which should correspond with the
                    name of the subfolder within the data and json folders
    local:          True if database is located locally, False if on web
    plot:           number of different views plotted per frame'''

    # start json timer
    jsonTimer = startTimer()

    # parse json data into variables
    name = join(datasetName,data['name']) if local else data['name']
    date = data['date']
    frames = data['frames']
    objects = data['objects']

    # create data folder for current scene if it does not exist
    prev_folder='./'
    for folder in [x for x in join('data',datasetName,data['name']).split('/') if x!='' and x!='.']:
        new_folder = join(prev_folder,folder)
        if not os.path.exists(new_folder):
            os.mkdir(new_folder)
        prev_folder = new_folder

    # store indices of the empty frames & objects
    emptyFrames = []
    emptyObjects = []

    for i, f in enumerate(frames):
        if not f or not f['polygon']:
            emptyFrames.append(i)

    for i,o in enumerate(objects):
        if not o:
            emptyObjects.append(i)

    # get camera intrinsics
    K = np.transpose(np.reshape(imp.readValuesFromTxt(join(currentPath,'data',name,'intrinsics.txt'),local),(3,3)))

    # get camera extrinsics
    if local:
        exFile = listdir(join('data',name,'extrinsics'))[-1]
    else:
        exFile = re.compile(r'[0-9]*\.txt').findall(urllib.request.urlopen(join(currentPath,'data',name,'extrinsics')).read().decode('utf-8'))[-1]

    extrinsicsC2W = np.reshape(imp.readValuesFromTxt(join(currentPath,'data',name,'extrinsics',exFile),local),(-1,3,4))
    extrinsics_incl_cur_frames_only = extrinsicsC2W.shape[0]==len(frames)

    # print file stats
    print("-- processing data.....", name)
    print("  -- DATE:", date)
    print("  -- # FRAMES:", len(frames))
    print("    -- actual:", len(frames)-len(emptyFrames))
    print("    -- undefined:", len(emptyFrames))
    print("  -- # OBJECTS:", len(objects))
    print("    -- actual:", len(objects)-len(emptyObjects))
    print("    -- undefined:", len(emptyObjects))

    # get background image and depth data ----------------#
    imagePath = join(currentPath,"data",name,"image")
    depthPath = join(currentPath,"data",name,"depth")

    imageList = data['fileList']
    prefixes = [im.split('-')[0] for im in imageList]

    if local:
        depthList = listdir(depthPath)
    else:
        depthList = sorted(list(set(re.compile(r'[0-9]*\-[0-9]*\.png').findall(urllib.request.urlopen(depthPath).read().decode('utf-8')))))

    depthList = [d for d in depthList if d.split('-')[0] in prefixes]

    if len(imageList)!=len(depthList)!=len(frames):
        print('ERROR: Cannot process JSON. Frame or depth image files missing.')
        return allObjects

    allFrames = []
    #for i, f in enumerate(frames[:14]):
    for i, f in enumerate(frames):
        if i in emptyFrames:
            continue
        frameTimer = startTimer()

        image = join(imagePath,imageList[i])
        depth = join(depthPath,depthList[i])
        frameNum = str(int(image.split('/')[-1].split('-')[0]))

        sys.stdout.write("\nCalculating Frame "+frameNum+" coordinates...\n")
        sys.stdout.write("\tgathering frame data:"); sys.stdout.flush()

        if local:
            background = Image.open(image,'r').convert('RGBA')
        else:
            background = Image.open(BytesIO(urllib.request.urlopen(image).read())).convert('RGBA')

        (width,height) = background.size

        # ---------------------------------------------------- #
        # create frame and fill with data

        currentFrame = Frame(frameNum,width,height)
        currentFrame.loc = data['name']
        currentFrame.background = background
        currentFrame.depthMap = imp.depthRead(depth,local)
        currentFrame.intrinsics = K
        currentFrame.extrinsics = extrinsicsC2W[i] if extrinsics_incl_cur_frames_only else extrinsicsC2W[int(frameNum)-1]
        exceptions   = []
        conflicts    = []
        polygons     = {}
        for polygon in f['polygon']:
            ID = polygon['object']
            exists = False
            for o in allObjects:
                if objects[ID]['name'] == o.name:
                    currentObject = o
                    currentObject.updateID(ID)
                    exists = True
                    break
            if not exists: # new object
                currentObject = SceneObject(ID,objects[ID]['name'])
                allObjects.append(currentObject)
            polygons[str(currentObject.getName())] = []
            currentObject.addFrame(name,currentFrame)
            for j, x in enumerate(polygon['x']):
                x = int(round(x))
                y = int(round(polygon['y'][j]))
                polygons[str(currentObject.getName())].append((x,y))
                if 0 < x <= width and 0 < y <= height:
                    if (y,x) in zip(currentFrame.row,currentFrame.col):
                        conflicts.append(str([(x,y),currentObject.getName()]))
                    else:
                        currentFrame.addData(currentObject,x,y)
                else:
                    exceptions.append(str([(x,y),currentObject.getName()]))
            currentFrame.addObject(currentObject)

        if local:
            filePath = join('data',name)
        else:
            filePath = join('data',datasetName,name)
        try:
            makedirs(filePath)
        except OSError:
            if not isdir(filePath):
                raise
        sys.stdout.write("\t\t%s\n"%str(endTimerPretty(frameTimer)));
        sys.stdout.flush()
        currentFrame = (currentFrame.update()
                               .calculateCentroids(polygons)
                               .drawPolygons(polygons,datasetName)
                               .process3dPoints(filePath,plot))

        allFrames.append(currentFrame)
        # uncomment to save exceptions and conflicts to files
        #np.savetxt(join(filePath,str(i)+'.ex'),exceptions,fmt="%s")
        #np.savetxt(join(filePath,str(i)+'.co'),conflicts,fmt="%s")
        sys.stdout.write("\ttotal time for frame %s:"%str(currentFrame.ID))
        sys.stdout.write(" \t%s"%str(endTimerPretty(frameTimer))); sys.stdout.flush()

    ## SET TO TRUE TO CALCULATE CENTROIDS ON A SCENE LEVEL (WORLD COORDS)
    if False:
        pathTo3dFiles = join('data',datasetName,data['name'])
        calculateCentroidsForScene(pathTo3dFiles,allObjects)

        print('Calculating angle and distance combinations...')
        for i, currentFrame in enumerate(allFrames):
            progress_bar.update(i+1,len(allFrames))
            centroidFile = join(pathTo3dFiles,'centroids.csv')
            currentFrame = currentFrame.getAngleDistCombos(centroidFile).export(filePath)
            currentFrame = None

    return allObjects


def main():

    options = menu.optionMenu()
    if options == 'menu' or options == 'back':
        return # back to main menu

    for datasetName,o in options.items():
        currentPath = o[0]
        local =       o[1]
        plot  =       o[2]
        nFrame_samples = o[3]
        jsonDir = f'./json/{datasetName}'
        run_pre_process = True if datasetName=='washington' else False
        datasetFullPath = f'./data/{datasetName}'
        if not os.path.exists(datasetFullPath):
            os.makedirs(datasetFullPath)
        if run_pre_process:
            imp.pre_process_input_files(dataDir=datasetFullPath,pcDir=f'./data/{datasetName}/pc',jsonDir=jsonDir,nFrame_samples=nFrame_samples)
        # load json files
        jsonFiles = sorted([f for f in listdir(jsonDir) if isfile(join(jsonDir,f)) and f.lower().endswith('json')])
        startA = startTimer()
        allObjects  = [] # list to store all objects from all json files in this db
        #for jNum,jFile in enumerate(jsonFiles):
        for jNum,jFile in enumerate(jsonFiles):#[0:1]):
            startB = startTimer()
            with open(join(jsonDir,jFile)) as jData:
                data = json.load(jData)
                allObjects = processJSON(data,allObjects,currentPath,datasetName,local,plot) # process each json file
            print("\n** File processed in %s."% str(endTimerPretty(startB)))
            print("** Total: "+str(jNum+1)+ " files processed in %s." % str(endTimerPretty(startA)))
            print("**",len(allObjects),"total objects in %s JSON files.\n" % str(jNum+1))
        # create log file for all the objects in all frames in all locations
        logFile = open(join("data",datasetName,"objects.log"),"w")
        logFile.write("---- All Objects ----\n")
        logFile.write("# - ID - name - RGB - Locations - Old IDs\n")
        for i, o in enumerate(allObjects):
            oldIDs = "N/A"
            if o.oldIDs:
                oldIDs = " "+str(o.oldIDs)
            line = str(i+1)+" - "+str(o.ID)+" - "+str(o.getName())+" - "+ \
                   str(o.colour[:3])+" - "+str(len(o.frames))+" - "+oldIDs+"\n"
            logFile.write(line)
        logFile.close()
