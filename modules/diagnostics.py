import os
from os import listdir, mkdir
from os.path import join, exists
import plyfile
import matplotlib
matplotlib.use("Agg")
#matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from mpl_toolkits.mplot3d import Axes3D
import numpy as np
import random

from modules import progress_bar

def generate_3d_scene(ply_filename,plot=3,progress_so_far=1,progress_total=1):

    # Load the PLY file
    ply_data = plyfile.PlyData.read(ply_filename)

    # Load the label file
    label_filename = os.path.splitext(ply_filename)[0]+'.label'
    with open(label_filename, 'r') as label_file:
        labels = [int(line.strip()) for line in label_file.readlines()[1:]]

    # Create a random color mapping for labels
    label_colors = {label: (random.random(), random.random(), random.random()) for label in set(labels)}

    # Create a 3D plot
    fig = plt.figure()
    ax = fig.add_subplot(111, projection='3d')

    # Extract vertex coordinates from PLY data
    x = ply_data['vertex']['x']
    y = ply_data['vertex']['y']
    z = ply_data['vertex']['z']

    # Create arrays for coordinates and colors
    coordinates = np.array([x, y, z]).T
    colors = np.array([label_colors[label] for label in labels])
    patches = [mpatches.Patch(color=colors[i],label=label) for i, label in enumerate(labels)]
    patches = handles
    ax.legend(handles=patches,loc=3,bbox_to_anchor=[float(-0.17),float(-0.15)],fontsize=7.5)

    # Plot all points with a single call to ax.scatter
    ax.scatter(coordinates[:, 0], coordinates[:, 1], coordinates[:, 2], c=colors, marker='o', alpha=0.003)

    # Set labels for the axes
    ax.set_xlabel('X')
    ax.set_ylabel('Y')
    ax.set_zlabel('Z')
    ax.azim = 0

    pcname = os.path.splitext(ply_filename.split('/')[-1])[0]
    for count in range(0,plot):
        progress_bar.update(progress_so_far,progress_total,suffix=f"Plotting point cloud '{pcname}' #{count+1}...")
        fig.savefig(os.path.splitext(ply_filename)[0]+f'_{count+1}.png', dpi=100)
        ax.azim+=int(360/plot)
        progress_so_far += 1
    plt.close(fig)


def read_3d_frame_data(file_path):
    with open(file_path, 'r') as file:
        contents = file.read()
        contents = contents.replace(')[',')\n[').replace(') [',')\n[')
        lines = contents.splitlines()

    labels = []
    points = []

    for line in lines:
        if line.startswith('['):
            current_object = line.strip()
        else:
            labels.append(current_object)
            points.append(eval(line.strip()))

    return labels, np.array(points)


def generate_colors_per_object(unique_objs):
    patches = []
    for i, obj in enumerate(unique_objs):
        R,G,B = int(random.random() * 255),int(random.random() * 255),int(random.random() * 255)
        if R < 100 and G < 100 and B < 100:
            col = random.randrange(0,3)
            R += 100 if col == 0 else 0
            G += 100 if col == 1 else 0
            B += 100 if col == 2 else 0
        color = [float(R)/255,float(G)/255,float(B)/255]
        patches.append(mpatches.Patch(color=color,label=obj))
    return patches


def generate_colors_per_point(patches, labels):
    patch_labels = [p.get_label() for p in patches]
    colors = [patches[patch_labels.index(l)].get_facecolor()[:3] for l in labels]
    return colors


def generate_point_clouds(dataset='washington',plot=3):

    pcdir = f'data/{dataset}/pc'
    pcfiles = sorted([join(pcdir,f) for f in listdir(pcdir) if f.endswith('.ply')])
    progress_total = len(pcfiles)*plot
    for i,pcfile in enumerate(pcfiles):
        progress_so_far = i*plot
        generate_3d_scene(pcfile,plot,progress_so_far,progress_total)
    progress_bar.update(progress_so_far,progress_total,suffix=' '*32)

dataset='washington'
scene='scene_01'
cumulative=True
plot=3
def generate_per_frame_point_clouds(dataset='washington',scene='scene_01',cumulative=True,plot=3):
    scene_dir = f'data/{dataset}/{scene}/'
    files3D = sorted([join(scene_dir,f) for f in listdir(scene_dir) if f.endswith('.3d')])
    files3D.sort() # regular in-place sort first
    files3D.sort(key=len) # then sort by filename length, as this correctly sorts number strings alphabetically
    total = plot*len(files3D)

    unique_objs = []
    all_object_data = []
    print('Finding unique objects...')
    for i,f in enumerate(files3D):
        progress_bar.update(i+1,len(files3D))
        frame_num = f.split('/')[-1].split('.')[0]
        object_data = read_3d_frame_data(f)
        unique_objs.extend([x for x in set(object_data[0]) if x not in unique_objs])
        all_object_data.append(object_data)
    unique_objs.sort()
    patches = generate_colors_per_object(unique_objs)

    print('Plotting point clouds...')
    fig = plt.figure()
    ax = fig.add_subplot(111, projection='3d')
    ax.legend(handles=patches,loc=3,bbox_to_anchor=[float(-0.17),float(-0.15)],fontsize=6.5)
    progress = 0
    for i,f in enumerate(files3D):
        frame_num = f.split('/')[-1].split('.')[0]
        progress_bar.update(progress,total,suffix=f'Preparing frame {frame_num}'+' '*17)
        object_data = all_object_data[i]

        if not cumulative:
            fig = plt.figure() # if not cumulative, reset figure each time
            ax = fig.add_subplot(111, projection='3d')
            ax.legend(handles=patches,loc=3,bbox_to_anchor=[float(-0.17),float(-0.15)],fontsize=6.5)

        ax.set_xlabel('X')
        ax.set_ylabel('Y')
        ax.set_zlabel('Z')

        labels = object_data[0]
        points = object_data[1]
        x, y, z = zip(*points)

        colors = generate_colors_per_point(patches, labels)
        ax.scatter(x, y, z, color=colors, marker='o',alpha=0.003)

        for count in range(0,plot):
            progress = (i*plot)+(count+1)
            ax.azim = 0 + count*int(360/plot)
            progress_bar.update(progress,total,suffix=f'Plotting frame {frame_num}: {ax.azim} degrees'+' '*6)
            diag_dir = join(scene_dir,'diagnostics')
            if not exists(diag_dir):
                mkdir(diag_dir)
            if cumulative:
                diag_dir = join(diag_dir,'cumulative')
                if not exists(diag_dir):
                    mkdir(diag_dir)
            output_file = join(diag_dir,f"{ax.azim if ax.azim!=0 else '000'}_{frame_num}.png")
            plt.savefig(output_file, format='png', bbox_inches='tight', dpi=100)
        if not cumulative or progress==total:
            plt.close()
    progress_bar.update(progress,total,suffix=' '*22)

'''
Plan (Dec2023/Jan2024)
1. Check if diagnostics are good for washington
2. If not good, fix extrinsics issues using washington. Else, move to step 3
3. Try slightly earlier extrinsics files (not latest) for SUN3D.
4. Check if same issue exists in other SUN3D scenes
5. Determine if it is worth running the bundle adjustment code if it means better 3d scene reconsctruction
    - Will this give us better extrinsics as output?
6. If world coordinates turn out to be too difficult to consolidate across frames, explore option of using only camera coordinates
'''

#generate_per_frame_point_clouds()
#generate_per_frame_point_clouds(dataset='princeton',scene='hotel_umd/maryland_hotel3',cumulative=False,plot=3)
