# Marek occupancy classifier

Vendored from https://github.com/martin-marek/parking-space-occupancy at commit 17da2c8b33055be8019483260f0efbcbdfda0478 (MIT, see LICENSE).

The only architecture changes are the local pooling import, a 128-pixel default ROI size matching this checkpoint, and weights=None to avoid downloading ImageNet weights when loading a complete checkpoint. Pooling behavior is unchanged.

Install requirements-marek.txt for the CPU runtime, copy the actual checkpoint to models/parking_RCNN_128_square_gopro.pt, and select Marek R-CNN in System Settings. The adapter uses BGR-to-RGB conversion, ImageNet normalization, the upstream square pooler, evaluation mode, batched inference, and softmax class 1 as occupied. Camera polygons use normalized [0,1] coordinates; map display coordinates are not camera detections.

The Inference Resolution setting downsizes large camera frames before pooling. Accuracy on your cameras needs validation with known empty/occupied examples; the published dataset accuracy is not a guarantee for your lot. Model errors raise without replacing saved occupancy with false vacancies.
