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

`imagine.py` coordinates the command-line workflow. Its supporting modules handle the individual stages used to rebuild source data, combination catalogs, and scene outputs.

#### Workflow at a Glance

| Role | Script | What it does |
| --- | --- | --- |
| Main entry point | [`imagine.py`](imagine.py) | Guides data preparation and scene generation, then launches Blender and GIF assembly. |
| Source extraction | [`modules/collect_data.py`](modules/collect_data.py) | Extracts per-frame world geometry and color evidence; optional processing exports relational measurements. |
| Data preparation | [`modules/prepare_data.py`](modules/prepare_data.py) | Aggregates observations, estimates object sizes, and constructs novel scene coordinates. |
| Combination catalog | [`modules/working_combos.py`](modules/working_combos.py) | Finds object sets supported by the observed relationship graph. |
| Blender renderer | [`modules/render.py`](modules/render.py) | Builds the room, places and adjusts assets, and renders the scene sequence. |
| GIF assembly | [`modules/png2gif.py`](modules/png2gif.py) | Interpolates the rendered PNG sequence into the final animation. |

#### Inputs and Outputs

<details open>
<summary><strong>Main entry point — <code>imagine.py</code></strong></summary>

**Inputs**

* Interactive selections for data processing, working-combination generation, scene type, object selection, and scene count.
* Prepared relationship, manual object-size, color, and working-combination tables in `data/`.
* The Blender template `suggested_setup.blend` and matching assets in `assets/`.

**Outputs**

* One numbered `output/<run-number>/` directory per completed scene.
* The renderer's input JSON, PNG frames, bird's-eye snapshots, final coordinate CSV, and assembled GIF described below.

</details>

<details>
<summary><strong>Source extraction — <code>modules/collect_data.py</code></strong></summary>

**Inputs**

* `json/{datasetName}/*.json` files containing 2D annotation coordinates for each labeled object in each frame.
* `data/{datasetName}/{sceneName}/image/*` 2D images per frame.
* `data/{datasetName}/{sceneName}/depth/*` 2D depth maps per frame.
* `data/{datasetName}/{sceneName}/extrinsics/*` camera extrinsics for each frame.
* `data/{datasetName}/{sceneName}/intrinsics.txt` camera intrinsics.

**Outputs**

The default processing path writes:

* `data/{datasetName}/{sceneName}/{frameNumber}.3d`, containing the available 3D world coordinates for each object in the frame.
* `data/object_colors.csv`, which accumulates sampled object colors from the labeled image regions.
* `data/{datasetName}/objects.log`, an inventory of the objects encountered across that dataset.

When the optional scene-level centroid block is enabled, it also writes:

* `data/{datasetName}/{sceneName}/centroids.csv`, containing the world-coordinate centroid of each object calculated from all available 3D points.
* `data/{datasetName}/{sceneName}/{frameNumber}.csv`, containing angles (degrees) and distances (meters) between every permutation of **3 objects + camera** in the frame.
* `data/{datasetName}/{sceneName}/{frameNumber}.jpg`, an annotated source image with colored object polygons and centroid labels.

</details>

<details>
<summary><strong>Data preparation — <code>modules/prepare_data.py</code></strong></summary>

**Input**

* The per-frame geometry and optional relation files produced by `modules/collect_data.py`.
* `data/object_sizes_manual.csv` and `data/object_colors.csv` when constructing a novel scene.

**Outputs**

* `data/triplets.csv` contains angles and distances between permutations of **3 objects + camera**, aggregated from all frames in all scenes.
* `data/object_sizes.csv` contains the object-size estimates produced by the approximation step.
* The `coords` value passed to `modules/render.py`, containing world-coordinate locations for a novel 3D scene.

</details>

<details>
<summary><strong>Combination catalog — <code>modules/working_combos.py</code></strong></summary>

**Input**

* `data/triplets.csv`, containing the observed object triplets prepared from source scenes.

**Outputs**

* `data/working-combos.csv`, the supported combinations of three to six objects.
* Refined and room-specific selections in `data/working-combos-refined.csv`, `data/working-combos-bedroom.csv`, `data/working-combos-livingroom.csv`, `data/working-combos-bathroom.csv`, and `data/working-combos-kitchen.csv`.

</details>

<details>
<summary><strong>Blender renderer — <code>modules/render.py</code></strong></summary>

**Inputs**

* A JSON-serializable object-coordinate dictionary generated by `modules/prepare_data.py`; the same value is saved as `output/<run-number>/00_blender_inputs.json` for reproducibility.
* `assets/{objectName}_####.obj` files whose names match object labels in the RGB-D datasets.
* `suggested_setup.blend`, used by `imagine.py` as the background Blender scene.

**Outputs**

* `output/<run-number>/{object-sequence}_####.png` animation frames, where the stem records the shuffled object-placement order.
* `output/<run-number>/01_birds_eye_view.png`, a bird's-eye view after all objects are placed and fully visible.
* `output/<run-number>/02_birds_eye_view.png`, a bird's-eye view after all objects are placed with the imagination effect applied.
* Structured final world coordinates, rotations, sizes, and colors returned to `imagine.py`, which writes `output/<run-number>/{object-sequence}.csv`.

</details>

<details>
<summary><strong>GIF assembly — <code>modules/png2gif.py</code></strong></summary>

**Input**

* The ordered scene PNG frames in a completed numbered output directory.

**Output**

* `output/<run-number>/{object-sequence}.gif`, with interpolated transition frames between successive renderer outputs.

</details>

### Serverless Research Tooling

The `serverless/` directory contains the reproducible runtime, deployment packaging, benchmarks, comparison studies, and publication-evidence tooling. Start with the [serverless overview](serverless/README.md), then use the [room-generation comparison guide](serverless/benchmark/README.md) and [controlled platform benchmark guide](serverless/cloud_benchmark/README.md) for the corresponding research workflows and evidence boundaries.
