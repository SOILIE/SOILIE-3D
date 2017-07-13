-------------------------------------------\n
VISUO 3D\n
v.17.07.18\n
-------------------------------------------\n
Written by Mike Cichonski\n
For the Science of Imagination Laboratory\n
at Carleton University\n
\n
<Required packages>\n
1. MATLAB engine for python\n
2. Numpy & Scipy\n
3. PIL\n
<Required Blender packages>\n
1. Numpy\n
\n
<Linux Instructions>\n
1. In terminal, run 'python getSUN3Ddata.py' to import SUn3D data\n 
2. Run command 'python avgSUN3Dobjects.py' to calculate object\n
   triplets and estimate object sizes\n
3. Run the run.py script with Blender in terminal:\n
   blender --background suggested_setup.blend --python run.py\n
\n
<Output>\n
The data folder will include output from scripts 1 and 2:\n
    1. SUN3D folder structure with .3d, .cen, .csv, ,jpg for each frame\n
    2. triplets.csv, triplets.pickle, object-sizes.csv\n
The images folder will include output from script 3:\n
    3. 3d scene image(s) in the images folder
