## SOILIE-3D
v.24.07.05

🌐 [Our Website](https://soilie3d.com/)

Written by Mike Cichonski <br>
With contributions from Isil Sanusoglu & Tae Burque <br>
For the Science of Imagination Laboratory (SOIL) <br>
CARLETON UNIVERSITY <br>
Ottawa, Canada <br>

### What is SOILIE-3D?

Science of Imagination Lab: Imagination Engine 3D ("SOILIE-3D"), is an innovative tool that harnesses the power of AI to simulate human visual imagination by generating (or "imagining") novel indoor three-dimensional scenes. Objects are placed in the scene based on real-world examples extracted from RGB-D datasets with object labels. To put it in human brain terms, this labeled RGB-D data is roughly equivalent our engine's memories of past scenes it has encountered. Objects that were placed most recently are the most opaque and detailed to simulate the recency effect, while earlier objects "fade" from existence. Walls and floors also become more transparent the further they are from the most recent object. The final image is a snapshot representation of what an imagined visual scene might actually look like. Outputs also contain a GIF animation to simulate how an imagined scene might change over time as we introduce new objects into it.

### Required Runtime

To get started, you will need to install some open-source software:
* [Blender](https://www.blender.org/download/)
* [Python](https://www.python.org/downloads/)

### Installation Instructions

###### Required Python Packages
* numpy
* pandas
* scipy
* scikit-image
* matplotlib
* pillow
* plyfile

After installing Blender and Python, you can install all required packages using pip. Simply type the following into your terminal or console window after navigating to the cloned repository folder and select yes (y) when prompted:
<br><br>
`pip install -U -r requirements.txt`

### Usage Instructions
1. [Download](https://soilie3d.com/data/?p=assets/) asset files and drop them in the `assets` folder.
2. In a bash terminal, run `python imagine.py`.
3. Follow the instructions through the menus to generate any number of novel indoor 3D scenes.
> ℹ️ Select the option to process input data before selecting the option to imagine a scene. Processing the input data may take several hours, but only needs to be done once.

### Key Scripts
#### 1. modules/collect_data.py

###### **INPUTS**
* `json/{datasetName}/{sceneName}.json` JSON files containing 2D annotation coordinates for each labeled object in each frame.
* `data/{datasetName}/{sceneName}/image/*` 2D images per frame.
* `data/{datasetName}/{sceneName}/depth/*` 2D depth map per frame.
* `data/{datasetName}/{sceneName}/extrinsics/*` Camera extrinsics for each frame.
* `data/{datasetName}/{sceneName}/intrinsics.txt` Camera intrinsics.

###### **OUTPUTS**

For each frame:
* `##.3d` Contains all 3d world coordinates for each object in the frame.
* `##.cen` Contains the 3d world coordinate centroids for each object in the frame, calculated using <b>only the points present in the frame.</b>
* `##.csv` Contains angles (degrees) and distances (meters) between every permutation of <b>3 objects + camera</b> in the frame.
* `##.jpg` Contains the projected 2D bounding boxes for each object in the frame, with labels appearing in the location of the centroids.
<br>

For each scene:
* `centroids.csv` Contains the 3d world coordinate centroids for each object in the scene, calculated using all available 3d points.

#### 2. modules/prepare_data.py

###### **INPUTS**
* The output from `collect_data.py`

###### **OUTPUTS**
* `data/triplets.csv` Angles and distances between permutations of <b>3 objects + camera</b>, aggregated from all frames in all scenes.
* `data/object-sizes.csv` Approximated object sizes for all objects found in all scenes.
* `coords` Variable passed to render.py containing world coordinate locations for a novel 3d scene.

#### 3. modules/render.py

###### **INPUTS**
* `00_blender_inputs.json` The output from `collect_data.py` and `prepare_data.py` (saved with output).
* `3d/{objectName}_####.obj` OBJ files in the 3d folder that match the object names in the RGBD databases.

###### **OUTPUTS**
* `output/{objectNames}.gif` GIF animation of imagined 3D scene(s).
* `output/{objectNames}_####.png` PNGs that compose the GIF animation.
* `output/01_birds_eye_view.png` Bird's eye view image of the scene after all objects are placed (fully visibile).
* `output/02_birds_eye_view.png` Bird's eye view image of the scene after all objects are placed (as imagined).
* `output/{objectNames}.csv` Corresponding world coordinate locations and sizes for all objects in each scene.
