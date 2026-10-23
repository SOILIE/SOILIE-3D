# Washington RGB-D Scenes v2 source geometry

SOILIE-3D processes the native 640 x 480 image grid with zero-based pixel
coordinates and this pinhole intrinsic matrix:

```text
K = [[525,   0, 319],
     [  0, 525, 239],
     [  0,   0,   1]]
```

The principal point is `(320, 240)` in MATLAB's one-based convention. Native
depth PNG values are tenths of a millimetre, so conversion to metres divides
the stored unsigned 16-bit values by 10,000.

The [dataset documentation](https://rgbd-dataset.cs.washington.edu/dataset/rgbd-scenes-v2/README.txt)
does not publish an exact intrinsic matrix. The matrix above is the project's
artifact-derived calibration: candidate matrices were used to project the
official scene PLY points into the aligned RGB and depth frames for all 14
scenes. The 525-pixel candidate produced the most consistent correspondence
with those native artifacts, including when compared with UW's
[`depthToCloud.m`](https://rgbd-dataset.cs.washington.edu/software/depthToCloud.m)
helper and the calibration used by the converted
[3DMatch consumer](https://github.com/andyzeng/3dmatch-toolbox/blob/72b08694710b5222855d1447a9b489d676b83557/training/match.hpp#L127-L133).
It is therefore a SOILIE-3D source contract, not a matrix claimed to have been
published by the University of Washington.

Extrinsics are frame-specific. Each official `.pose` row contains
`(a, b, c, d, x, y, z)`: a quaternion orientation followed by the camera
position in global coordinates. Preprocessing matches the pose row to its RGB
and depth frame, converts it to a camera-to-world matrix, and records the
sampled matrices with the processed scene.

Source downloads and dataset details are available from the
[RGB-D Scenes Dataset v2 page](https://rgbd-dataset.cs.washington.edu/dataset/rgbd-scenes-v2/).
