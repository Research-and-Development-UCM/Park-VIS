import unittest
from pathlib import Path
import numpy as np
from backend.marek_engine import MarekParkingEngine


class MarekEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import torch
        except ImportError:
            raise unittest.SkipTest("Install requirements-marek.txt")
        torch.set_num_threads(2)
        weights = Path(__file__).resolve().parents[1] / "models/parking_RCNN_128_square_gopro.pt"
        if not weights.exists() or weights.stat().st_size < 1024:
            raise unittest.SkipTest("Actual Marek checkpoint required")
        cls.engine = MarekParkingEngine(weights, max_resolution=128, batch_size=2)
        cls.image = np.random.default_rng(17).integers(0, 256, (128, 128, 3), dtype=np.uint8)
        cls.rois = np.array([[[.1,.1],[.3,.1],[.3,.3],[.1,.3]], [[.6,.6],[.8,.6],[.8,.8],[.6,.8]], [[0,0],[.1,0],[.1,.1],[0,.1]]], dtype=np.float32)

    def test_preprocessing_matches_rgb_imagenet_normalization(self):
        pixel = np.array([[[0, 64, 255]]], dtype=np.uint8)
        actual = self.engine.preprocess(pixel).cpu().numpy()[:,0,0]
        expected = (np.array([255,64,0])/255 - np.array([.485,.456,.406])) / np.array([.229,.224,.225])
        np.testing.assert_allclose(actual, expected, atol=1e-6)

    def test_adapter_matches_direct_upstream_model_and_preserves_roi_order(self):
        torch = self.engine.torch
        with torch.inference_mode():
            expected = self.engine.model(self.engine.preprocess(self.image), torch.tensor(self.rois)).softmax(1)[:,1].cpu().numpy()
        actual = self.engine.predict(self.image,self.rois)
        np.testing.assert_allclose(actual, expected, atol=2e-6)
        self.assertEqual(len(actual),3)
        self.assertTrue(np.isfinite(actual).all())
        self.assertTrue(((actual>=0)&(actual<=1)).all())

    def test_invalid_polygons_raise_instead_of_reporting_vacant(self):
        for rois in (self.rois+2, np.full((1,4,2),np.nan), np.zeros((1,4,2)), np.zeros((1,3,2))):
            with self.assertRaises(ValueError):
                self.engine.predict(self.image,rois)

    def test_empty_roi_set_has_no_predictions(self):
        self.assertEqual(self.engine.predict(self.image,np.empty((0,4,2))).size,0)

    def test_missing_weights_fail_clearly(self):
        with self.assertRaisesRegex(RuntimeError,"weights missing"):
            MarekParkingEngine("nonexistent-checkpoint.pt")


if __name__ == "__main__":
    unittest.main()
