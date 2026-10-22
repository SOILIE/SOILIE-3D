<table>
  <tr>
    <td width="128" valign="middle">
      <img src="images/soilie-mark.webp" alt="SOILIE mark" width="112">
    </td>
    <td valign="middle">
<pre>
 ::::::::   :::::::: ::::::: :::     ::::::: :::::::      :::::::  ::::::::  
:+:    :+: :+:    :+:  :+:   :+:       :+:   :+:         :+:   :+: :+:   :+:
+:+        +:+    +:+  +:+   +:+       +:+   +:+               +:+ +:+   +:+
+#++:++#++ +#+    +:+  +#+   +#+       +#+   +#:+#  +:++#+  +#++:  +#+   +:+
       +#+ +#+    +#+  +#+   +#+       +#+   +#+               +#+ +#+   +#+
#+#    #+# #+#    #+#  #+#   #+#       #+#   #+#         #+#   #+# #+#   #+#
 ########   ######## ####### ####### ####### #######      #######  ########  
</pre>
    </td>
  </tr>
</table>

**Current model version: 4.0.3**<br>
Original source snapshot: 24.07.05

🌐 [Our Website](https://soilie3d.com/)

Written by Mike Cichonski<br>
With contributions from Isil Sanusoglu & Tae Burque<br>
For the Science of Imagination Laboratory (SOIL)<br>
CARLETON UNIVERSITY<br>
Ottawa, Canada

### What is SOILIE-3D?

Science of Imagination Lab: Imagination Engine 3D ("SOILIE-3D") is an innovative tool that harnesses the power of AI to simulate human visual imagination by generating (or "imagining") novel indoor three-dimensional scenes. Objects are placed in the scene based on real-world examples extracted from RGB-D datasets with object labels. To put it in human-brain terms, this labeled RGB-D data is roughly equivalent to our engine's memories of past scenes it has encountered. Objects that were placed most recently are the most opaque and detailed to simulate the recency effect, while earlier objects "fade" from existence. Walls and floors also become more transparent the further they are from the most recent object. The final image is a snapshot representation of what an imagined visual scene might actually look like. Outputs also contain a GIF animation to simulate how an imagined scene might change over time as we introduce new objects into it.

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

After installing Blender and Python, navigate to the cloned repository and install the required packages with:

```bash
pip install -U -r requirements.txt
```

### Usage Instructions

1. [Download](https://soilie3d.com/data/?p=assets/) the asset files and place them in the `assets` folder.
2. In a terminal, run `python imagine.py`.
3. Follow the menu prompts to generate any number of novel indoor 3D scenes.

> ℹ️ Select the option to process input data before selecting the option to imagine a scene. Processing the input data may take several hours, but only needs to be done once.

### Key Scripts

#### 1. `imagine.py`

###### **INPUTS**

* Interactive selections for data processing, working-combination generation, scene type, object selection, and scene count.
* The prepared relationship and object-size tables in `data/`, the Blender template `suggested_setup.blend`, and matching assets in `assets/`.

###### **OUTPUTS**

* One numbered `output/<run-number>/` directory per completed scene.
* The renderer's input JSON, PNG frames, bird's-eye snapshots, final coordinate CSV, and assembled GIF described below.

#### 2. `modules/collect_data.py`

###### **INPUTS**

* `json/{datasetName}/*.json` JSON files containing 2D annotation coordinates for each labeled object in each frame.
* `data/{datasetName}/{sceneName}/image/*` 2D images per frame.
* `data/{datasetName}/{sceneName}/depth/*` 2D depth maps per frame.
* `data/{datasetName}/{sceneName}/extrinsics/*` Camera extrinsics for each frame.
* `data/{datasetName}/{sceneName}/intrinsics.txt` Camera intrinsics.

###### **OUTPUTS**

For each frame:

* `{frameNumber}.3d` contains all 3D world coordinates for each object in the frame.
* `{frameNumber}.csv` contains angles (degrees) and distances (meters) between every permutation of **3 objects + camera** in the frame when the optional scene-level centroid block is enabled.
* `{frameNumber}.jpg` contains the projected 2D bounding boxes for each object in the frame, with labels appearing at their centroids when that same optional block exports the annotated source image.

For each scene, optional scene-level centroid processing writes `centroids.csv`, containing the 3D world-coordinate centroids for each object calculated from all available 3D points.

#### 3. `modules/prepare_data.py`

###### **INPUTS**

* The output from `collect_data.py`.

###### **OUTPUTS**

* `data/triplets.csv` contains angles and distances between permutations of **3 objects + camera**, aggregated from all frames in all scenes.
* `data/object_sizes.csv` contains approximated object sizes for all objects found in all scenes.
* The `coords` value passed to `modules/render.py`, containing world-coordinate locations for a novel 3D scene.

#### 4. `modules/working_combos.py`

###### **INPUTS**

* `data/triplets.csv`, containing the observed object triplets prepared from source scenes.

###### **OUTPUTS**

* `data/working-combos.csv`, the complete set of usable object combinations.
* Refined and room-specific selections in `data/working-combos-refined.csv`, `data/working-combos-bedroom.csv`, `data/working-combos-livingroom.csv`, `data/working-combos-bathroom.csv`, and `data/working-combos-kitchen.csv`.

#### 5. `modules/render.py`

###### **INPUTS**

* A JSON-serializable object-coordinate dictionary generated by `prepare_data.py`; the same value is saved as `output/<run-number>/00_blender_inputs.json` for reproducibility.
* `assets/{objectName}_####.obj` OBJ files whose names match object labels in the RGB-D datasets.
* `suggested_setup.blend`, used by `imagine.py` as the background Blender scene.

###### **OUTPUTS**

* `output/<run-number>/{sceneName}_####.png` animation frames.
* `output/<run-number>/01_birds_eye_view.png`, a bird's-eye view after all objects are placed and fully visible.
* `output/<run-number>/02_birds_eye_view.png`, a bird's-eye view after all objects are placed with the imagination effect applied.
* Structured final world coordinates, rotations, sizes, and colors returned to `imagine.py`, which writes `output/<run-number>/{sceneName}.csv`.

#### 6. `modules/png2gif.py`

###### **INPUTS**

* The ordered scene PNG frames in a completed numbered output directory.

###### **OUTPUTS**

* `output/<run-number>/{sceneName}.gif`, with interpolated transition frames between successive renderer outputs.

### Serverless Research Tooling

The `serverless/` directory contains the reproducible runtime, deployment packaging, benchmarks, comparison studies, and publication-evidence tooling. Start with the [serverless overview](serverless/README.md), then use the [room-generation comparison guide](serverless/benchmark/README.md) and [controlled platform benchmark guide](serverless/cloud_benchmark/README.md) for the corresponding research workflows and evidence boundaries.
