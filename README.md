##  VISUO 3D
#### v.17.07.17  
###### Written by Mike Cichonski
###### For the Science of Imagination Laboratory
###### at Carleton University

##### Required packages
* MATLAB engine for Python2
* Numpy & Scipy
* matplotlib
* PIL
* Blender (tested in 2.78c)
   * Numpy for Blender

##### Usage Instructions (Ubuntu)
1. In terminal, run `python collect_data.py` to import SUN3D data 
2. Run command `python prepare_data.py` to calculate object
   triplets and estimate object sizes
3. Run the run.py script using Blender in the terminal:
   `blender --background suggested_setup.blend --python run.py`

##### Scripts
###### collect_data.py

**INPUT:**
JSON files from SUN3D database (note: Only a fraction of all the
data has been manually labelled; this data is in the JSON files)
<br>
**OUTPUT:**
SUN3D folder structure with .3d, .cen, .csv, ,.jpg for each frame

###### prepare_data.py

**INPUT:**
The output from collect_data.py
<br>
**OUTPUT:** 
* triplets.csv/triplets.pickle - calculated angles and distances
between permutations of triplets of objects
* object-sizes.csv - calculated object sizes

###### run.py

**INPUT:**
3DS files in the 3d folder that match the object names obtained
from the SUN3D database and the output from prepare_data.py
<br>
**OUTPUT:**
3d PNG scene image(s) in the images folder

##### Notes
* The 'local' option in collect_data.py prompts for the filepath
to the directory containing the data downloaded from the SUN3D 
database. This data is expected to be in the same folder structure
as the online database and must contain the intrinsics, extrinsics,
and images which correspond with the data in the json folder. 
Typically, the 'web' option should suffice and will find the 
correct data automatically.
* Due to a memory bug in the MATLAB engine, collect_data.py may 
occasionally crash. If this occurs, remove the json files that
have already been processed from the json folder (the filenames 
correspond to the folder structure of the data) and rerun the
script on the remaining files until they are all processed before
running prepare_data.py.
