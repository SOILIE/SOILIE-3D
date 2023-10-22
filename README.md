##  SOILIE 3D
#### v.23.10.31
#### CONTAINERIZED DOCKER FORMAT IN PROGRESS [HERE](https://github.com/MichaelOVertolli/smart-storage/tree/master)
###### Written by Mike Cichonski
###### With contributions from Tae Burque & Isil Sanusoglu
###### For the Science of Imagination Laboratory
###### CARLETON UNIVERSITY

##### Required packages
* numpy
* pandas
* scipy
* matplotlib
* PIL
* open3d
* blender

##### Usage Instructions
1. Drop JSON files containing per-frame object annotations in the `json` folder.
2. If annotations are related to the 3D point clouds instead of the 2D frames, they will be automatically generated from the <b>.label</b>, <b>.ply</b>, and <b>.pose</b> files in `data/{datasetName}/pc/` combined with the extrinsics file in `data/{datasetName}/{sceneName}/extrinsics/extrinsics.txt`.
3. In a bash terminal, run `python imagine.py`.
4. Follow the instructions through the menus to generate any number of novel indoor 3D scenes.
  * <b>Note:</b> Select the option to process input data before selecting the option to imagine a scene

##### Key Scripts
###### modules/collect_data.py

**INPUT:**
JSON files from SUN3D database (note: Only a fraction of all the data has been manually labelled; this data is in the JSON files)
<br>
**OUTPUT:**
SUN3D folder structure with .3d, .cen, .csv, ,.jpg for each frame

###### modules/prepare_data.py

**INPUT:**
The output from `collect_data.py`
<br>
**OUTPUT:**
* `triplets.csv/triplets.pickle` - averaged angles and distances between permutations of triplets of objects
* `object-sizes.csv` - approximated object sizes

###### modules/render.py

**INPUT:**
3DS files in the 3d folder that match the object names obtained from the selected database and the output from `collect_data.py` and `prepare_data.py`
<br>
**OUTPUT:**
3d PNG scene image(s) in the output folder
