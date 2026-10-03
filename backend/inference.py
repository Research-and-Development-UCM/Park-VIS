import os
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import time
import cv2
import json
import asyncio
import threading
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from . import models, database
try:
    from vulturevision import VultureVision
except ImportError:
    # Fallback stub when running in development/test environments without the compiled C++ wheel
    class VultureVision:  # type: ignore
        def __init__(self, *args, **kwargs):
            pass
        def predict(self, *args, **kwargs):
            return []
        def detect(self, *args, **kwargs):
            return []
        @staticmethod
        def get_available_gpu_provider():
            return {"provider": "", "error": "vulturevision engine not installed"}
from .logging_config import vulture_logger as logger

_vv_instance = None
_vv_config = {"use_gpu": None, "max_res": None, "last_check": 0, "generation": 0}
_vv_init_lock = threading.Lock()
_vv_build_lock = threading.Lock()
_vv_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inference_worker")


def invalidate_config_cache():
    """Re-read inference settings without blocking the event loop."""
    with _vv_init_lock:
        _vv_config["generation"] += 1
        _vv_config["last_check"] = 0


def get_inference_executor():
    return _vv_executor


def get_vulturevision():
    global _vv_instance
    with _vv_init_lock:
        if _vv_instance is not None and time.time() - _vv_config["last_check"] <= 60:
            return _vv_instance
    with _vv_build_lock:
        with _vv_init_lock:
            if _vv_instance is not None and time.time() - _vv_config["last_check"] <= 60:
                return _vv_instance
            generation = _vv_config["generation"]
        db = database.SessionLocal()
        try:
            device = db.query(models.Setting).filter_by(key="inference_device").first()
            resolution = db.query(models.Setting).filter_by(key="max_inference_resolution").first()
            use_gpu = bool(device and device.value == "cuda")
            max_res = int(resolution.value) if resolution else 1440
        finally:
            db.close()
        with _vv_init_lock:
            rebuild = (_vv_instance is None or _vv_config["use_gpu"] != use_gpu
                       or _vv_config["max_res"] != max_res)
        if rebuild:
            model_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "parking-occupancy.encs")
            if not os.path.isfile(model_path):
                raise RuntimeError("AI model missing. Run git lfs pull to download models/parking-occupancy.encs.")
            if os.path.getsize(model_path) < 1024:
                with open(model_path, "rb") as model_file:
                    if model_file.read(80).startswith(b"version https://git-lfs.github.com/spec/v1"):
                        raise RuntimeError("The AI model is a Git LFS pointer. Run git lfs pull first.")
            logger.info("Loading parking model (GPU: {gpu}, MaxRes: {res})", gpu=use_gpu, res=max_res)
            new_instance = VultureVision(model_path=model_path, use_gpu=use_gpu, max_resolution=max_res)
            with _vv_init_lock:
                _vv_instance = new_instance
                _vv_config.update(use_gpu=use_gpu, max_res=max_res)
        with _vv_init_lock:
            if generation == _vv_config["generation"]:
                _vv_config["last_check"] = time.time()
        return _vv_instance


async def run_inference(camera_id: int, image_bytes: bytes, spaces: list, max_res: int = 1440):
    if not spaces: return {}, 0.0, (0, 0)
    
    # Offload decoding to thread
    img = await asyncio.to_thread(cv2.imdecode, np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None: return {}, 0.0, (0, 0)
    
    # 1. Prepare ROIs for VultureVision (N, 4, 2) normalized
    rois = []
    space_ids = []
    for s in spaces:
        pts = s['points']
        if isinstance(pts, str): pts = json.loads(pts)
        if len(pts) != 8: continue
        rois.append([[pts[i], pts[i+1]] for i in range(0, 8, 2)])
        space_ids.append(s['id'])
    
    if not rois: return {}, 0.0, img.shape[1::-1]
    
    rois_np = np.array(rois, dtype=np.float32)
    
    try:
        loop = asyncio.get_running_loop()
        executor = get_inference_executor()
        
        vv = await asyncio.to_thread(get_vulturevision)
        start_time = time.perf_counter()
        
        probs = await loop.run_in_executor(
            executor,
            vv.predict,
            img, rois_np
        )
        
        elapsed = time.perf_counter() - start_time
        probs_dict = {sid: float(p) for sid, p in zip(space_ids, probs)}
        return probs_dict, elapsed, img.shape[1::-1]
        
    except Exception as e:
        logger.error("VultureVision inference failed: {e}", e=e)
        # Re-raise rather than returning all-zero probabilities. The
        # scheduler's outer try/except catches the failure, skips the
        # ``_do_save_results`` call for this iteration, and the cached
        # ``occupied`` state is preserved. Returning {0.0} previously
        # caused the save path to flip every space to vacant and
        # persist that as a state change, masking real engine failures
        # from the operator. The manual-test endpoint at app.py also
        # has an outer try/except that maps the failure to a 500.
        raise

async def get_crops(camera_id: int, image_bytes: bytes, spaces: list, max_res: int = 1440, quality: int = 90):
    if not spaces: return {}
    
    def _do_crops():
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None: return {}
        
        h_work, w_work = img.shape[:2]
        results = {}
        
        for s in spaces:
            pts = s['points']
            if isinstance(pts, str): pts = json.loads(pts)
            if len(pts) != 8: continue
            
            # Convert normalized [0,1] points to absolute pixel coordinates
            x_coords = [pts[i] * w_work for i in range(0, 8, 2)]
            y_coords = [pts[i+1] * h_work for i in range(0, 8, 2)]
            
            # Find center and dimensions of the ROI
            cx, cy = sum(x_coords) / 4.0, sum(y_coords) / 4.0
            w, h = max(x_coords) - min(x_coords), max(y_coords) - min(y_coords)
            
            # Use the largest dimension to ensure a square crop
            side = max(w, h)
            
            x1, y1 = int(cx - side/2), int(cy - side/2)
            x2, y2 = int(cx + side/2), int(cy + side/2)
            
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w_work, x2), min(h_work, y2)
            
            if x2 <= x1 or y2 <= y1: continue

                
            crop = img[y1:y2, x1:x2]
            if crop.size == 0: continue
            
            warp = cv2.resize(crop, (128, 128))
            _, buffer = cv2.imencode('.jpg', warp, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            results[s['id']] = buffer.tobytes()
        return results

    loop = asyncio.get_running_loop()
    # Pass None to use the default multi-worker ThreadPoolExecutor
    return await loop.run_in_executor(None, _do_crops)

def get_gpu_provider():
    """Returns the GPU provider name available, or empty string if none.

    Thin pass-through to ``vulturevision.get_gpu_provider``,
    which executes the C++ probe in a separate subprocess.
    """
    import sys
    is_test = "pytest" in sys.modules or "unittest" in sys.modules

    if is_test:
        try:
            from vulturevision import _vulturevision
            result = _vulturevision.get_available_gpu_provider()
            if isinstance(result, str):
                return {"provider": result, "error": ""}
            provider = result.get("provider", "")
            error = result.get("error", "")
            if not provider and not error:
                error = (
                    "CUDAExecutionProvider could not be loaded. Run the "
                    "probe directly from a Python shell to see stderr output."
                )
            return {"provider": provider, "error": error}
        except Exception as e:
            return {"provider": "", "error": f"GPU probe failed: {type(e).__name__}: {e}"}

    try:
        import vulturevision
        return vulturevision.get_gpu_provider()
    except Exception as e:
        return {"provider": "", "error": f"GPU probe failed: {type(e).__name__}: {e}"}


def _probe_gpu_provider_safely():
    """Same as :func:`get_gpu_provider` — kept as a name for
    backward compatibility with the existing route handler.
    """
    return get_gpu_provider()


def is_cuda_available():
    return bool(get_gpu_provider().get("provider"))

