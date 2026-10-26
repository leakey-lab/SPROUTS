"""The Correct workspace: one canvas that edits the mask, the skeleton and the class labels
of an image.

Modules (consumers import the submodules; this package imports nothing):

- ``layer``: the contract between the workspace and its edit layers (``EditLayer``,
  ``LayerHost``, ``Notice``, ``SaveOutcome`` ...). Qt-free at import.
- ``session``: ``CorrectSession``, the document lifecycle: open / close, the one undo
  timeline, dirty aggregation, save, save reports and the unsaved-changes prompt.
  Qt-free.
- ``tool_router``: ``ToolRouter``, canvas input to the active layer (B hold / tap,
  gesture keys, a hidden layer shown before it is edited).
- ``mask_document``: ``MaskDocument``, the mask plane, its history, saved baseline and
  file I/O. Qt-free.
- ``mask_layer``: ``MaskLayer``, the mask as an edit layer over ``PaintCanvas``.
- ``mask_tools``: the mask's Brush, Eraser and Fill tools (the raster layers' tools).
- ``raster_layer``: ``RasterLayer``, what the mask and class-label layers share: canvas,
  tools, input, gestures, history replay, ring and view state.
- ``classes_document``: ``ClassesDocument``, the class-label plane (``gt/<stem>.png``), its
  ticks, history, saved state and the registry. Qt-free.
- ``classes_files``: ``read_classes``, the class-label files of one image read and aligned on any
  thread (``ClassesRead``). Qt-free.
- ``classes_paint``: the class labels' canvas colours and ids read back from them. Qt-free.
- ``classes_layer``: ``ClassesLayer``, the class labels as an edit layer.
- ``skeleton_document``: ``SkeletonDocument``, the skeleton model, which file it came
  from and where it saves. Qt-free.
- ``skeleton_layer``: ``SkeletonLayer``, the skeleton as an edit layer over the
  skeleton tools.
- ``controls``: ``CorrectControls``, the rail groups, dock controls and polyline prompt.
- ``layer_panel``: ``LayerPanel``, the Layers panel.
- ``chip``: what the status chip shows (file name, worst layer state, library position,
  hint).
- ``read_ahead``: ``ReadAhead``, the next images read on a worker thread. Qt-free.
- ``paging``: ``Neighbours``, the library position, the neighbour read-ahead and the
  photos shared with the Library display; ``PagingApi``, the page's read-ahead API for
  MainWindow.
- ``read_back``: the image the page leaves, handed back to the read-ahead as it is in
  memory (``closed_image``). Qt-free.
- ``layer_prefs``: ``LayerPrefs``, the layers' opacity and visibility across restarts.
- ``keys``: the page's shortcuts (the registry's Correct rows, PgDn / PgUp).
- ``editor``: ``CorrectEditor``, the page ``MainWindow`` shows; it only composes the
  parts above.
"""
