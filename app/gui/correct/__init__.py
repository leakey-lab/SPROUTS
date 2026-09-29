"""The Correct workspace: one canvas that edits the mask and the skeleton of an image.

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
- ``mask_tools``: the mask's Brush, Eraser and Fill tools.
- ``skeleton_document``: ``SkeletonDocument``, the skeleton model, which file it came
  from and where it saves. Qt-free.
- ``skeleton_layer``: ``SkeletonLayer``, the skeleton as an edit layer over the
  skeleton tools.
- ``controls``: ``CorrectControls``, the rail groups, dock controls and polyline prompt.
- ``layer_panel``: ``LayerPanel``, the Layers panel.
- ``editor``: ``CorrectEditor``, the page ``MainWindow`` shows; it only composes the
  parts above.
"""
