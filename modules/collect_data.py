''' SOILIE 3D
    v.24.07.05
    Written by Mike Cichonski
    With contributions from Tae Burque and Isil Sanusoglu
    for the Science of Imagination Laboratory
    Carleton University

    File: collect_data.py
	This file traverses the selected dataset(s) and calculates
	angles and distances between object triplets in each frame.
	Requires JSON label files in the json folder.'''

import ast
import os, json, random, sys, urllib.request, re, gc
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin

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
from modules.object_identity import (
    ObjectIdentityError,
    encode_collector_object_label,
    normalize_instance_id,
    normalize_object_class,
)
from modules.relation_manifest import (
    RELATION_MANIFEST_FILENAME,
    RelationManifestError,
    begin_relation_run,
    build_relation_frame_record,
    complete_relation_run,
    relation_row_count,
    sha256_file,
    validate_relation_location,
)
from modules.source_geometry import (
    EXACT_FRAME_ID,
    SourceGeometryError,
    WORLD_POINT_CLOUD_SCHEMA_VERSION,
    camera_origin_world,
    pair_rgb_depth_files,
    pinned_extrinsics_filename,
    pose_index,
    reshape_c2w_rows,
    reshape_intrinsics,
    resolve_source_profile,
    select_c2w_pose,
)
from modules.triplet_schema import TRIPLET_COLUMNS


def _angle_degrees(first, second, *, angle_name):
    '''Return a stable angle for two finite, non-zero 3D vectors.'''

    first = np.asarray(first, dtype=np.float64)
    second = np.asarray(second, dtype=np.float64)
    if first.shape != (3,) or second.shape != (3,):
        raise ValueError(f'{angle_name} requires two three-dimensional vectors')
    if not np.all(np.isfinite(first)) or not np.all(np.isfinite(second)):
        raise ValueError(f'{angle_name} requires finite vectors')

    first_norm = float(np.linalg.norm(first))
    second_norm = float(np.linalg.norm(second))
    if not np.isfinite(first_norm) or not np.isfinite(second_norm):
        raise ValueError(f'{angle_name} vector magnitude must be finite')
    if first_norm == 0.0 or second_norm == 0.0:
        raise ValueError(f'{angle_name} is undefined for a zero-length vector')

    cosine = float(np.dot(first, second) / (first_norm * second_norm))
    if not np.isfinite(cosine):
        raise ValueError(f'{angle_name} cosine must be finite')
    # Roundoff can put a mathematically valid cosine just outside the arccos
    # domain (for example, 1.0000000000000002 for near-parallel vectors).
    return float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))


def _progress_update(progress, total, suffix=''):
    '''Render collector progress even on Windows consoles lacking block glyphs.'''

    try:
        progress_bar.update(progress, total, suffix=suffix)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or 'ascii'
        safe_suffix = suffix.encode(encoding, errors='replace').decode(encoding)
        sys.stdout.write(f'{progress}/{total} {safe_suffix}\r')
        sys.stdout.flush()


def _relation_label_from_scene_key(value):
    '''Encode one internal ``[class, instance]`` key without delimiter loss.'''

    if not isinstance(value, str):
        raise ValueError('Scene object keys must be strings')
    try:
        identity = ast.literal_eval(value)
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f'Malformed scene object key {value!r}') from exc
    if (
        not isinstance(identity, (list, tuple))
        or len(identity) != 2
        or not all(isinstance(component, str) for component in identity)
    ):
        raise ValueError(
            f'Scene object key {value!r} must contain string class and instance IDs',
        )
    return encode_collector_object_label(identity[0], identity[1])


def _source_object_identity(source_id, source_name):
    '''Return the canonical class/instance pair for one annotation object.'''

    if type(source_id) is not int or source_id < 0:
        raise ObjectIdentityError('Source object ID must be a non-negative integer')
    if not isinstance(source_name, str):
        raise ObjectIdentityError('Source object name must be a string')
    object_class, separator, instance_id = source_name.partition(':')
    if not separator:
        # The annotation object-list position is the only stable identity when
        # the source supplies a class without an explicit instance component.
        instance_id = str(source_id)
    return [
        normalize_object_class(object_class),
        normalize_instance_id(instance_id),
    ]


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
        self.centroids = {}    # world-space object centroids in metres
        self.objects3d = {}    # world-space points grouped by object
        self.combos = []       # canonical named triplet relationship rows
        self.intrinsics = []   # camera intrinsics
        # C2W pose; identity keeps synthetic/manual Frame use well-defined.
        self.extrinsics = np.hstack((np.eye(3), np.zeros((3, 1))))
        self.pixel_origin = 0  # selected source's pixel-coordinate convention
        self.image = None      # image to export
        self.background = None # original image
        self.depthMap = None   # depth map
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
            data['object_name'].append('_'.join(ast.literal_eval(name)[0].split()))
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
            pix[pixel[0],pixel[1]] = self.data[i].colour
        # Project depth into camera coordinates; this temporary grid is then
        # transformed to the one world frame used by all collector outputs.
        sys.stdout.write("\tprojecting depth:"); sys.stdout.flush()
        t1 = startTimer()
        cameraCoords = imp.depth2XYZcamera(
            self.intrinsics,
            self.depthMap,
            pixel_origin=self.pixel_origin,
        )
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
        # Get world-space 3D points (using only the points visible in this
        # frame).  Scene aggregation is only meaningful when every frame uses
        # the same coordinate system.
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
                        xyzCoords = self.xyz[count]
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
        point_clouds = {}
        for name, coords in self.objects3d.items():
            coordinates = np.asarray(coords, dtype=np.float64)
            if (
                not isinstance(name, str)
                or not name
                or coordinates.ndim != 2
                or coordinates.shape[1:] != (3,)
                or not coordinates.size
                or not np.all(np.isfinite(coordinates))
            ):
                raise ValueError(
                    f'Object {name!r} must have a non-empty finite Nx3 world point cloud',
                )
            point_clouds[name] = coordinates
        if not point_clouds:
            raise ValueError(f'Frame {self.ID} has no finite world-coordinate object points')

        plotFile = open(saveLoc+'.3d','w', encoding='utf-8', newline='\n')
        plotFile.write(json.dumps({
            'schemaVersion': WORLD_POINT_CLOUD_SCHEMA_VERSION,
            'coordinateFrame': 'world',
        }, separators=(',', ':')) + '\n')
        patches = []
        #-----------------------------------------------#
        if plot:
            if not exists(saveLoc+'/'):
                makedirs(saveLoc+'/')
            fig = plt.figure()
            ax = fig.add_subplot(111,projection='3d')
            camera = camera_origin_world(self.extrinsics)
            ax.scatter([camera[0]],[camera[1]],[camera[2]],c='r',marker='o')
            #fig.clf()
        #-----------------------------------------------#
        for name, coordinates in point_clouds.items():
            plotFile.write(json.dumps({
                'object': name,
                'points': coordinates.tolist(),
            }, separators=(',', ':'), allow_nan=False) + '\n')
            #---------------------------------------------------------------#
            if not plot: continue
            # get colour
            colour = [1,1,1]
            for o in self.objects:
                if str(o.getName()) == name:
                    colour = [float(c)/255 for c in o.colour[:-1]]
                    patches.append(mpatches.Patch(color=colour,label=name))
            xyz = np.transpose(coordinates)
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
        '''Get canonical relations from world centroids and this frame's camera.'''
        #sys.stdout.write("\tcalculating triplets:"); sys.stdout.flush()
        t5 = startTimer()

        frameObjects = list(self.centroids.keys())
        df_c = pd.read_csv(centroidFile)
        for i,row in df_c.iterrows():
            obj = row['object']
            if obj in frameObjects:
                centroid = np.asarray([row['X'], row['Y'], row['Z']], dtype=np.float64)
                if not np.all(np.isfinite(centroid)):
                    raise ValueError(f'Non-finite scene centroid for {obj!r}')
                self.centroids[obj] = centroid

        names = list(self.centroids)
        combos = permutations(names, 3)
        camera_world = camera_origin_world(self.extrinsics)
        self.combos = []
        for combo in combos:
            A = np.array(self.centroids[combo[0]])
            B = np.array(self.centroids[combo[1]])
            C = np.array(self.centroids[combo[2]])
            # O is the per-frame camera centre.  C2W maps the camera-space
            # origin directly to the pose's translation column.
            O = camera_world
            BA = B - A
            CA = C - A
            OA = O - A
            # distance AB, AC, AO
            distAB = np.linalg.norm(A-B)
            distAC = np.linalg.norm(A-C)
            distAO = np.linalg.norm(A-O)
            # angle BAC
            angleBAC = _angle_degrees(BA, CA, angle_name='angleBAC')
            # angle OAB
            angleOAB = _angle_degrees(OA, BA, angle_name='angleOAB')
            # angle OAC
            angleOAC = _angle_degrees(OA, CA, angle_name='angleOAC')
            # Encode the two identity components as data.  Splitting their
            # repr on punctuation loses real commas and underscores.
            nameA = _relation_label_from_scene_key(combo[0])
            nameB = _relation_label_from_scene_key(combo[1])
            nameC = _relation_label_from_scene_key(combo[2])
            self.combos.append({
                'objectA': nameA,
                'objectB': nameB,
                'objectC': nameC,
                'distanceAB': str(distAB),
                'distanceAC': str(distAC),
                'distanceAO': str(distAO),
                'angleBAC': str(angleBAC),
                'angleOAB': str(angleOAB),
                'angleOAC': str(angleOAC),
            })
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
	    #export csv file using the canonical relationship contract
        df = pd.DataFrame.from_records(
            self.combos,
            columns=TRIPLET_COLUMNS,
        )
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
        return _source_object_identity(self.ID, self.name)

    # add Frame to frames dictionary
    def addFrame(self,location,f):
        if location not in self.frames:
            self.frames[location] = []
        if f not in self.frames[location]:
            self.frames[location].append(f)

#pathTo3dFiles = 'data/princeton/hotel_umd/maryland_hotel3/'
## -------------------------------------------------------------- ##
def _resolve_scene_point_cloud_files(pathTo3dFiles, point_cloud_files):
    '''Resolve an explicit current-run manifest to direct scene children.'''

    scene_root = os.path.realpath(os.path.abspath(os.fspath(pathTo3dFiles)))
    if isinstance(point_cloud_files, (str, bytes, os.PathLike)):
        raise TypeError('point_cloud_files must be an iterable of .3d paths')
    try:
        supplied_files = tuple(point_cloud_files)
    except TypeError as exc:
        raise TypeError('point_cloud_files must be an iterable of .3d paths') from exc
    if not supplied_files:
        raise ValueError('point_cloud_files must identify at least one current-run .3d file')

    resolved_files = []
    seen = set()
    for supplied in supplied_files:
        if not isinstance(supplied, (str, bytes, os.PathLike)):
            raise TypeError('Every point_cloud_files entry must be a filesystem path')
        supplied_path = os.fspath(supplied)
        if isinstance(supplied_path, bytes):
            supplied_path = os.fsdecode(supplied_path)
        if not supplied_path:
            raise ValueError('point_cloud_files entries cannot be empty')

        candidate = supplied_path
        if not os.path.isabs(candidate):
            candidate = os.path.join(scene_root, candidate)
        candidate = os.path.realpath(os.path.abspath(candidate))
        try:
            inside_scene = os.path.commonpath((scene_root, candidate)) == scene_root
        except ValueError:
            inside_scene = False
        if not inside_scene or os.path.dirname(candidate) != scene_root:
            raise ValueError(
                f'Current-run .3d files must be direct children of {scene_root!r}: '
                f'{supplied_path!r}',
            )
        if not candidate.lower().endswith('.3d'):
            raise ValueError(f'Current-run point-cloud file must end in .3d: {supplied_path!r}')
        key = os.path.normcase(candidate)
        if key in seen:
            raise ValueError(f'Duplicate current-run point-cloud file: {supplied_path!r}')
        if not os.path.isfile(candidate):
            raise FileNotFoundError(f'Current-run point-cloud file does not exist: {candidate}')
        seen.add(key)
        resolved_files.append(candidate)

    return resolved_files


def calculateCentroidsForScene(pathTo3dFiles, plot=0, *, point_cloud_files=None):
    '''Aggregate marked world-coordinate JSONL point clouds for one scene.

    Legacy/unmarked ``.3d`` files are rejected so camera-frame points can
    never be silently combined with the corrected world-frame output.  A
    current collector run supplies its exact file manifest so valid but stale
    files from an earlier run cannot enter the aggregate.  Omitting the
    manifest preserves the standalone legacy directory-scan API.
    '''
    if type(plot) is not int or plot < 0:
        raise ValueError('plot must be a non-negative integer view count')
    print('\n\nCalculating object centroids for entire scene...')
    if point_cloud_files is None:
        files3D = []
        for root, dirnames, filenames in os.walk(pathTo3dFiles):
            for filename in fnmatch.filter(filenames,'*.3d'):
                files3D.append(os.path.join(root,filename))
    else:
        files3D = _resolve_scene_point_cloud_files(
            pathTo3dFiles,
            point_cloud_files,
        )

    files3D.sort() # regular in-place sort first
    files3D.sort(key=len) # then sort by filename length, as this correctly sorts number strings alphabetically
    if not files3D:
        raise ValueError(f'No per-frame .3d files found in {pathTo3dFiles!r}')

    pts3d = {}
    progress_so_far = 0 # keep track of progress so far
    for i, filename in enumerate(files3D):
        _progress_update(
            progress_so_far,
            max(1, len(files3D)),
            suffix='Obtaining world-space 3D points: ' + filename,
        )
        with open(filename, 'r', encoding='utf-8') as theFile:
            first_line = theFile.readline()
            try:
                marker = json.loads(first_line)
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError(
                    f'{filename} is legacy or unmarked .3d data; remove it and '
                    'rebuild this scene before world-coordinate aggregation',
                ) from exc
            expected_marker = {
                'schemaVersion': WORLD_POINT_CLOUD_SCHEMA_VERSION,
                'coordinateFrame': 'world',
            }
            if marker != expected_marker:
                raise ValueError(
                    f'{filename} is not schema-v{WORLD_POINT_CLOUD_SCHEMA_VERSION} '
                    'world-coordinate .3d data; remove it and rebuild this scene',
                )

            for line_number, line in enumerate(theFile, start=2):
                if not line.strip():
                    raise ValueError(
                        f'Blank .3d JSONL record at {filename}:{line_number}',
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f'Malformed .3d record at {filename}:{line_number}',
                    ) from exc
                if not isinstance(record, dict) or set(record) != {'object', 'points'}:
                    raise ValueError(
                        f'Invalid .3d object record at {filename}:{line_number}',
                    )
                name = record['object']
                points = np.asarray(record['points'], dtype=np.float64)
                if not isinstance(name, str) or not name:
                    raise ValueError(
                        f'Invalid .3d object name at {filename}:{line_number}',
                    )
                if points.ndim != 2 or points.shape[1:] != (3,) or not points.size:
                    raise ValueError(
                        f'Object points must be a non-empty Nx3 array at '
                        f'{filename}:{line_number}',
                    )
                if not np.all(np.isfinite(points)):
                    raise ValueError(
                        f'Non-finite 3D point at {filename}:{line_number}',
                    )
                pts3d.setdefault(name, []).extend(points)
        progress_so_far += 1

    pts3d = {name: points for name, points in pts3d.items() if points}
    if not pts3d:
        raise ValueError(f'No finite object points found in {pathTo3dFiles!r}')

    progress_total = len(files3D) + len(pts3d) + (len(pts3d) + 1) * plot

    maxFilenameLength = max([len(f) for f in files3D])
    maxNameLength = max([len(o) for o in pts3d.keys()])

    centroids = {}
    df_dict = {'object':[],'X':[],'Y':[],'Z':[]}
    for i, obj in enumerate(pts3d.keys()):
        _progress_update(progress_so_far,progress_total,suffix='Calculating centroids for ' + obj + ' '*(maxFilenameLength-len(obj)-4))
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

    if not plot:
        _progress_update(100,100)
        return centroids

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
            _progress_update(progress_so_far,progress_total,suffix=f'Plotting {obj} point cloud {count+1}...'+' '*(maxNameLength-len(obj)+4))
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
        _progress_update(progress_so_far,progress_total,suffix=f'Plotting full point cloud {count+1}...'+' '*(maxNameLength-4))
        curFig.savefig(join(pathTo3dFiles,f'point_cloud_{count+1}.png'), dpi=100) # save plot with all objects
        ax.azim+=int(360/plot)
        progress_so_far += 1
    #plt.show()
    plt.close(curFig)
    #################
    _progress_update(100,100,suffix=' '*(30+maxNameLength))
    return centroids


def _source_join(root, *parts, local):
    if local:
        return join(root, *parts)
    relative = '/'.join(str(part).strip('/\\').replace('\\', '/') for part in parts)
    return urljoin(str(root).rstrip('/') + '/', relative)


def _remote_directory_files(directory_url, suffix):
    '''Return leaf filenames advertised by a simple HTTP directory listing.'''

    with urllib.request.urlopen(str(directory_url).rstrip('/') + '/') as response:
        listing = response.read().decode('utf-8')
    candidates = re.findall(r'href=["\']?([^"\' >]+)', listing, flags=re.IGNORECASE)
    names = []
    for candidate in candidates:
        leaf = candidate.rstrip('/').rsplit('/', 1)[-1]
        if leaf.lower().endswith(suffix.lower()):
            names.append(leaf)
    return sorted(set(names))


def _require_unique_relation_frame_ids(frame_pairs, empty_frame_indices=()):
    '''Reject frame IDs that would alias the collector's ``<ID>.csv`` path.'''

    empty_frame_indices = set(empty_frame_indices)
    emitted_frame_ids = [
        pair.image_frame_id
        for json_index, pair in enumerate(frame_pairs)
        if json_index not in empty_frame_indices
    ]
    duplicate_frame_ids = sorted(
        frame_id
        for frame_id, count in Counter(emitted_frame_ids).items()
        if count > 1
    )
    if duplicate_frame_ids:
        raise SourceGeometryError(
            'Multiple annotated frames would overwrite the same relation file: '
            f'image frame IDs {duplicate_frame_ids}',
        )


def processJSON(
    data,
    allObjects,
    currentPath,
    datasetName,
    local,
    plot,
    *,
    export_scene_relations=True,
    return_relation_records=False,
    annotation_path=None,
    annotation_sha256=None,
):
    '''process each json file
    data:           the data contained in the json file
    currentPath:    the root path of the dataset
    datasetName:    the name of the database, which should correspond with the
                    name of the subfolder within the data and json folders
    local:          True if database is located locally, False if on web
    plot:           number of different views plotted per frame

    By default this retains the historical ``allObjects`` return value used by
    manual callers.  The top-level collector opts into the two-value result so
    it can publish provenance records only after the entire run succeeds.'''

    if type(export_scene_relations) is not bool:
        raise TypeError('export_scene_relations must be a boolean')
    if type(return_relation_records) is not bool:
        raise TypeError('return_relation_records must be a boolean')
    if annotation_path is not None:
        annotation_path = Path(annotation_path)
    if annotation_sha256 is not None and (
        not isinstance(annotation_sha256, str)
        or re.fullmatch(r'[0-9a-f]{64}', annotation_sha256) is None
    ):
        raise TypeError('annotation_sha256 must be a lowercase SHA-256 digest')
    if export_scene_relations and (annotation_path is None or annotation_sha256 is None):
        raise SourceGeometryError(
            'Relation export requires the exact annotation_path and '
            'annotation_sha256 for the run manifest',
        )
    if annotation_path is not None:
        observed_annotation_sha256 = sha256_file(annotation_path)
        if observed_annotation_sha256 != annotation_sha256:
            raise SourceGeometryError(
                'annotation_sha256 does not match the exact annotation file bytes',
            )

    # start json timer
    jsonTimer = startTimer()

    # Parse JSON data into variables and bind it to a named source contract.
    # Known datasets cannot silently override their scientific conventions;
    # a new dataset must declare a complete sourceGeometry object.
    profile = resolve_source_profile(datasetName, data.get('sourceGeometry'))
    try:
        dataset_identity, scene_identity = validate_relation_location(
            datasetName,
            data['name'],
        )
    except RelationManifestError as exc:
        raise SourceGeometryError(str(exc)) from exc
    name = join(dataset_identity,scene_identity) if local else scene_identity
    date = data['date']
    frames = data['frames']
    objects = data['objects']

    # Build source identities before processing any pixels.  Class-only labels
    # use their annotation object index, so two distinct ``chair`` entries stay
    # distinct; duplicate explicit class/instance identities are ambiguous and
    # must be repaired in the source annotation instead of silently collapsing.
    source_object_identities = {}
    identity_owners = {}
    for source_id, source_object in enumerate(objects):
        if not source_object:
            continue
        try:
            source_name = source_object['name']
            identity = tuple(_source_object_identity(source_id, source_name))
        except (KeyError, ObjectIdentityError) as exc:
            raise SourceGeometryError(
                f'Object {source_id} has an invalid source identity: {exc}',
            ) from exc
        if identity in identity_owners:
            raise SourceGeometryError(
                f'Objects {identity_owners[identity]} and {source_id} declare '
                f'the same source identity {identity!r}',
            )
        identity_owners[identity] = source_id
        source_object_identities[source_id] = identity

    # Collector output is local even when source images are fetched remotely.
    outputScenePath = join('data', dataset_identity, scene_identity)
    makedirs(outputScenePath, exist_ok=True)

    # store indices of the empty frames & objects
    emptyFrames = []
    emptyObjects = []

    for i, f in enumerate(frames):
        if not f or not f['polygon']:
            emptyFrames.append(i)

    for i,o in enumerate(objects):
        if not o:
            emptyObjects.append(i)

    scene_source_path = _source_join(currentPath, 'data', name, local=local)

    # get camera intrinsics
    intrinsics_path = _source_join(scene_source_path, 'intrinsics.txt', local=local)
    K = reshape_intrinsics(imp.readValuesFromTxt(intrinsics_path, local))

    # Use the exact file reviewed and pinned in the source JSON.  Selecting the
    # last directory entry made camera poses depend on filesystem/listing order.
    exFile = pinned_extrinsics_filename(data.get('extrinsics'))
    extrinsics_path = _source_join(
        scene_source_path,
        'extrinsics',
        exFile,
        local=local,
    )
    extrinsicsC2W = reshape_c2w_rows(imp.readValuesFromTxt(extrinsics_path, local))

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
    imagePath = _source_join(scene_source_path, 'image', local=local)
    depthPath = _source_join(scene_source_path, 'depth', local=local)

    imageList = data['fileList']
    if not isinstance(imageList, list) or len(imageList) != len(frames):
        raise SourceGeometryError(
            'Source JSON fileList must contain exactly one image filename per frame',
        )

    if local:
        depthList = sorted(
            filename
            for filename in listdir(depthPath)
            if isfile(join(depthPath, filename)) and filename.lower().endswith('.png')
        )
    else:
        depthList = _remote_directory_files(depthPath, '.png')

    if profile.pairing_strategy == EXACT_FRAME_ID:
        # Washington JSON intentionally samples frames from a larger scene.
        # Restrict the directory inventory to that declared sample before
        # enforcing one-to-one image/depth equality within the sample.
        annotated_frame_ids = {
            filename.split('-', 1)[0].casefold()
            for filename in imageList
        }
        depthList = [
            filename
            for filename in depthList
            if filename.split('-', 1)[0].casefold() in annotated_frame_ids
        ]

    framePairs = pair_rgb_depth_files(
        imageList,
        depthList,
        profile.pairing_strategy,
    )
    # Resolve every pose before processing any frame so a bad mapping cannot
    # leave a partially exported scene.
    framePoses = tuple(
        select_c2w_pose(
            extrinsicsC2W,
            pair,
            json_index,
            profile.pose_index_strategy,
        )
        for json_index, pair in enumerate(framePairs)
    )
    framePoseIndices = tuple(
        pose_index(pair, json_index, profile.pose_index_strategy)
        for json_index, pair in enumerate(framePairs)
    )

    # Relation filenames use the image frame ID.  Reject aliases before any
    # per-frame export so a repeated ID cannot silently overwrite evidence.
    _require_unique_relation_frame_ids(framePairs, emptyFrames)

    allFrames = []
    allFrameContexts = []
    current_point_cloud_files = []
    #for i, f in enumerate(frames[:14]):
    for i, f in enumerate(frames):
        if i in emptyFrames:
            continue
        frameTimer = startTimer()

        pair = framePairs[i]
        image = _source_join(imagePath, pair.image_name, local=local)
        depth = _source_join(depthPath, pair.depth_name, local=local)
        frameNum = str(pair.image_frame_id)

        sys.stdout.write("\nCalculating Frame "+frameNum+" coordinates...\n")
        sys.stdout.write("\tgathering frame data:"); sys.stdout.flush()

        if local:
            with Image.open(image,'r') as source_image:
                background = source_image.convert('RGBA')
        else:
            with urllib.request.urlopen(image) as response:
                image_bytes = response.read()
            with Image.open(BytesIO(image_bytes)) as source_image:
                background = source_image.convert('RGBA')

        (width,height) = background.size

        # ---------------------------------------------------- #
        # create frame and fill with data

        currentFrame = Frame(frameNum,width,height)
        currentFrame.loc = data['name']
        currentFrame.background = background
        currentFrame.depthMap = imp.depthRead(depth, local, profile.depth_encoding)
        if currentFrame.depthMap.shape != (height, width):
            raise SourceGeometryError(
                f'RGB/depth dimensions differ for frame {frameNum}: '
                f'{(width, height)} RGB vs {currentFrame.depthMap.shape[::-1]} depth',
            )
        currentFrame.intrinsics = K
        currentFrame.extrinsics = framePoses[i]
        currentFrame.pixel_origin = profile.pixel_origin
        exceptions   = []
        conflicts    = []
        polygons     = {}
        for polygon in f['polygon']:
            ID = polygon['object']
            if type(ID) is not int or ID < 0 or ID >= len(objects) or not objects[ID]:
                raise SourceGeometryError(
                    f'Frame {frameNum} references invalid object index {ID!r}',
                )
            exists = False
            for o in allObjects:
                if tuple(o.getName()) == source_object_identities[ID]:
                    currentObject = o
                    currentObject.updateID(ID)
                    exists = True
                    break
            if not exists: # new object
                currentObject = SceneObject(ID,objects[ID]['name'])
                allObjects.append(currentObject)
            polygons[str(currentObject.getName())] = []
            currentObject.addFrame(name,currentFrame)
            if len(polygon['x']) != len(polygon['y']):
                raise SourceGeometryError(
                    f'Frame {frameNum} object {ID} has mismatched polygon coordinates',
                )
            for j, x in enumerate(polygon['x']):
                source_x = int(round(x))
                source_y = int(round(polygon['y'][j]))
                x = source_x - profile.pixel_origin
                y = source_y - profile.pixel_origin
                polygons[str(currentObject.getName())].append((x,y))
                if 0 <= x < width and 0 <= y < height:
                    if (y,x) in zip(currentFrame.row,currentFrame.col):
                        conflicts.append(str([(source_x,source_y),currentObject.getName()]))
                    else:
                        currentFrame.addData(currentObject,x,y)
                else:
                    exceptions.append(str([(source_x,source_y),currentObject.getName()]))
            currentFrame.addObject(currentObject)

        sys.stdout.write("\t\t%s\n"%str(endTimerPretty(frameTimer)));
        sys.stdout.flush()
        currentFrame = (currentFrame.update()
                               .calculateCentroids(polygons)
                               .drawPolygons(polygons,datasetName)
                               .process3dPoints(outputScenePath,plot))

        allFrames.append(currentFrame)
        allFrameContexts.append((i, pair, framePoseIndices[i], framePoses[i]))
        current_point_cloud_files.append(f'{currentFrame.ID}.3d')
        # uncomment to save exceptions and conflicts to files
        #np.savetxt(join(filePath,str(i)+'.ex'),exceptions,fmt="%s")
        #np.savetxt(join(filePath,str(i)+'.co'),conflicts,fmt="%s")
        sys.stdout.write("\ttotal time for frame %s:"%str(currentFrame.ID))
        sys.stdout.write(" \t%s"%str(endTimerPretty(frameTimer))); sys.stdout.flush()

    relation_records = []
    if export_scene_relations and allFrames:
        calculateCentroidsForScene(
            outputScenePath,
            plot=plot,
            point_cloud_files=current_point_cloud_files,
        )

        print('Calculating angle and distance combinations...')
        for i, (currentFrame, context) in enumerate(zip(allFrames, allFrameContexts)):
            _progress_update(i+1,len(allFrames))
            centroidFile = join(outputScenePath,'centroids.csv')
            currentFrame = currentFrame.getAngleDistCombos(centroidFile).export(outputScenePath)
            json_index, pair, selected_pose_index, selected_pose = context
            relation_file = Path(outputScenePath, f'{currentFrame.ID}.csv')
            repository_root = Path.cwd().resolve()
            data_root = (repository_root / 'data').resolve()
            try:
                annotation_relative = annotation_path.resolve().relative_to(
                    repository_root,
                ).as_posix()
                relation_relative = relation_file.resolve().relative_to(
                    data_root,
                ).as_posix()
            except ValueError as exc:
                raise SourceGeometryError(
                    'Collector annotations and relation outputs must remain inside '
                    'the current repository',
                ) from exc
            relation_records.append(build_relation_frame_record(
                dataset=dataset_identity,
                scene=scene_identity,
                annotation_path=annotation_relative,
                annotation_sha256=annotation_sha256,
                source_geometry={
                    'depthEncoding': profile.depth_encoding,
                    'pairingStrategy': profile.pairing_strategy,
                    'poseIndexStrategy': profile.pose_index_strategy,
                    'pixelOrigin': profile.pixel_origin,
                },
                json_frame_index=json_index,
                image={
                    'name': pair.image_name,
                    'frameId': pair.image_frame_id,
                    'timestamp': pair.image_timestamp,
                },
                depth={
                    'name': pair.depth_name,
                    'frameId': pair.depth_frame_id,
                    'timestamp': pair.depth_timestamp,
                },
                extrinsics_file=exFile,
                pose_index=selected_pose_index,
                camera_to_world=selected_pose,
                relation_path=relation_relative,
                relation_sha256=sha256_file(relation_file),
                relation_row_count_value=relation_row_count(relation_file),
            ))
            currentFrame = None

    if return_relation_records:
        return allObjects, relation_records
    return allObjects


def main():

    options = menu.optionMenu()
    if options == 'menu' or options == 'back':
        return # back to main menu

    manifest_path = Path('data') / RELATION_MANIFEST_FILENAME
    collection_id = begin_relation_run(manifest_path)
    relation_records = []

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
            annotation_path = Path(jsonDir, jFile)
            annotation_digest = sha256_file(annotation_path)
            with annotation_path.open(encoding='utf-8') as jData:
                data = json.load(jData)
                allObjects, scene_relation_records = processJSON(
                    data,
                    allObjects,
                    currentPath,
                    datasetName,
                    local,
                    plot,
                    return_relation_records=True,
                    annotation_path=annotation_path,
                    annotation_sha256=annotation_digest,
                )
                relation_records.extend(scene_relation_records)
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

    if not relation_records:
        raise RelationManifestError(
            'Collector produced no relation frame records; the run manifest '
            'remains INCOMPLETE and cannot be gathered',
        )

    complete_relation_run(
        manifest_path,
        collection_id=collection_id,
        frames=relation_records,
        data_root=Path('data'),
    )
