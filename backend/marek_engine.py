"""Adapter for Martin Marek's RCNN_128_square_gopro checkpoint."""
from pathlib import Path
import cv2
import numpy as np


class MarekParkingEngine:
    def __init__(self, model_path, use_gpu=False, max_resolution=1440, batch_size=32):
        try:
            import torch
            from .marek.rcnn import RCNN
        except ImportError as exc:
            raise RuntimeError("The Marek model requires PyTorch. Install requirements-marek.txt in the server environment.") from exc
        path = Path(model_path)
        if not path.is_file():
            raise RuntimeError("Marek weights missing: models/parking_RCNN_128_square_gopro.pt")
        if path.stat().st_size < 1024:
            raise RuntimeError("Marek weights are a Git LFS pointer or an incomplete file. Download the actual .pt checkpoint.")
        self.torch = torch
        self.device = torch.device("cuda" if use_gpu and torch.cuda.is_available() else "cpu")
        self.max_resolution = int(max_resolution)
        self.batch_size = max(1, int(batch_size))
        self.model = RCNN(roi_res=128, pooling_type="square")
        state = torch.load(path, map_location="cpu", weights_only=True)
        self.model.load_state_dict(state, strict=True)
        self.model.to(self.device).eval()
        self.mean = torch.tensor([0.485, 0.456, 0.406], device=self.device).view(3, 1, 1)
        self.std = torch.tensor([0.229, 0.224, 0.225], device=self.device).view(3, 1, 1)

    def preprocess(self, image):
        torch = self.torch
        if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
            raise ValueError("Expected an 8-bit BGR camera image.")
        height, width = image.shape[:2]
        if self.max_resolution > 0 and max(height, width) > self.max_resolution:
            scale = self.max_resolution / max(height, width)
            image = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        tensor = torch.from_numpy(rgb).permute(2, 0, 1).to(self.device, dtype=torch.float32) / 255.0
        return (tensor - self.mean) / self.std

    def predict(self, image, rois):
        torch = self.torch
        rois = np.asarray(rois, dtype=np.float32)
        if rois.size == 0:
            return np.empty(0, dtype=np.float32)
        if rois.ndim != 3 or rois.shape[1:] != (4, 2) or not np.isfinite(rois).all() or (rois < 0).any() or (rois > 1).any():
            raise ValueError("Camera-space polygons must have four finite normalized (x,y) corners in [0,1].")
        if (np.ptp(rois[:, :, 0], axis=1) <= 0).any() or (np.ptp(rois[:, :, 1], axis=1) <= 0).any():
            raise ValueError("Camera-space polygons must have nonzero width and height.")
        probabilities = []
        with torch.inference_mode():
            tensor = self.preprocess(image)
            coordinates = torch.as_tensor(rois, device=self.device)
            for offset in range(0, len(rois), self.batch_size):
                logits = self.model(tensor, coordinates[offset:offset + self.batch_size])
                probabilities.append(logits.softmax(dim=1)[:, 1].cpu().numpy())
        scores = np.concatenate(probabilities)
        if len(scores) != len(rois) or not np.isfinite(scores).all():
            raise RuntimeError("Marek model returned invalid occupancy probabilities.")
        return scores
