-------------------------------------------
VISUO 3D
v.17.07.18
-------------------------------------------
Written by Mike Cichonski
For the Science of Imagination Laboratory
at Carleton University

<Required packages>
1. MATLAB engine for python
2. Numpy & Scipy
3. PIL
<Required Blender packages>
1. Numpy

<Linux Instructions>
1. In terminal, run 'python getSUN3Ddata.py' to import SUn3D data 
2. Run command 'python avgSUN3Dobjects.py' to calculate object
   triplets and estimate object sizes
3. Run the run.py script with Blender in terminal:
   blender --background suggested_setup.blend --python run.py

<Output>
The data folder will include output from scripts 1 and 2:
    1. SUN3D folder structure with .3d, .cen, .csv, ,jpg for each frame
    2. triplets.csv, triplets.pickle, object-sizes.csv
The images folder will include output from script 3:
    3. 3d scene image(s) in the images folder
