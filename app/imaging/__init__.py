"""Qt-free image processing helpers shared by the canvases (numpy + cv2 only).

Modules:

* :mod:`app.imaging.pyramid` - half-scale image pyramids (area average and max-pool)
  with lazy levels and dirty-rect patching, used for level-of-detail rendering.
* :mod:`app.imaging.skeleton_vectors` - the vector links of a 1 px skeleton raster
  (numpy only), which the skeleton layer item draws when magnified.

This package module imports nothing; import the submodules directly.
"""
