"""
Side-angle single-stall occupancy proof (IMG_3762.mp4).

Pass 1 of the side-angle work: prove that BOTTOM-CENTER point-in-polygon + temporal
smoothing correctly tracks ONE stall going OCCUPIED -> OPEN on the oblique camera
angle, before touching parking.py. Standalone; does not modify main.py / parking.py.

Why bottom-center (not centroid): on a side view the box center floats up onto the
car body, so it lands in the wrong stall. The tire-contact point -- bottom-center
(int((x1+x2)/2), int(y2)) -- sits where the car meets the asphalt, which is what the
stall polygon actually covers. (No +50 hack.)

Demo stall: the silver Mercedes ML middle stall. It is OCCUPIED early and the car
pulls out near the end, so this clip shows a real OCCUPIED -> OPEN transition.

Coordinate space: matches the existing pipeline (Option A) -- frames are resized to
1080x600 before detection, exactly like main.py, so the polygon you draw via the normal
img.py -> se.py flow (which annotates on the 1080x600 still) lines up. Override with
--resize if you ever annotate at a different size (use WxH, or "none" for native).

Pipeline for this demo:
    1. python img.py                 # grabs a 1080x600 still (img_0.jpg) from IMG_3762
    2. python se.py                  # draw the silver stall, Save -> bounding_boxes.json
    3. python side_angle_demo.py --json bounding_boxes.json --save-clip

Flags:
    --stall N        which stall index in the json to track (default 0)
    --conf C         detection confidence (default 0.3)
    --flip-frames N  consecutive consistent reads required to flip state (default 5)
    --resize WxH     frame size to run detection at; must match annotation space
                     (default 1080x600; pass "none" for native resolution)
    --save-clip      write an annotated .mp4 of the tracked stall to outputs/
    --no-window      skip the live cv2 window (headless)
"""

from __future__ import annotations

import argparse
import json
import os
from collections import deque

import cv2
import numpy as np
from ultralytics import YOLO

VEHICLE_CLASSES = [2, 3, 7]  # car, motorcycle, truck/SUV (COCO); matches main branch


def load_stall(json_file: str, idx: int) -> np.ndarray:
    with open(json_file, encoding="utf-8") as f:
        data = json.load(f)
    if not data:
        raise SystemExit(f"{json_file} has no stalls — draw one first.")
    if idx >= len(data):
        raise SystemExit(f"--stall {idx} out of range ({len(data)} stalls in {json_file}).")
    return np.array(data[idx]["points"], np.int32)


def anchor_point(box, frac: float) -> tuple[int, int]:
    """Horizontal-center point at a given vertical fraction of the box.
    frac=1.0 -> bottom edge (tire contact), 0.5 -> box center, 0.8 -> low on the body.
    Lower-on-body (not dead bottom) keeps the point inside a thin stall polygon while
    still avoiding the centroid 'floats onto the roof / wrong stall' problem."""
    x1, y1, x2, y2 = box
    return int((x1 + x2) / 2), int(y1 + (y2 - y1) * frac)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--source", default="videos/IMG_3762.mp4")
    ap.add_argument("--json", default="bounding_boxes.json")
    ap.add_argument("--model", default="yolo11s.pt")
    ap.add_argument("--stall", type=int, default=0, help="stall index in the json")
    ap.add_argument("--conf", type=float, default=0.3)
    ap.add_argument("--anchor", type=float, default=0.8,
                    help="vertical fraction of the box to test (1.0=bottom edge, "
                         "0.5=center, 0.8=low on body; default 0.8)")
    ap.add_argument("--flip-frames", type=int, default=5)
    ap.add_argument("--resize", default="1080x600",
                    help="detection frame size WxH matching annotation space, or 'none'")
    ap.add_argument("--save-clip", action="store_true")
    ap.add_argument("--no-window", action="store_true")
    args = ap.parse_args()

    if args.resize.lower() == "none":
        resize_to = None
    else:
        rw, rh = (int(v) for v in args.resize.lower().split("x"))
        resize_to = (rw, rh)

    poly = load_stall(args.json, args.stall)
    model = YOLO(args.model)

    cap = cv2.VideoCapture(args.source)
    assert cap.isOpened(), f"cannot open {args.source}"
    native_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    native_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    w, h = resize_to if resize_to else (native_w, native_h)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    print(f"source {args.source} native {native_w}x{native_h} -> detect at {w}x{h} | "
          f"stall {args.stall} | classes={VEHICLE_CLASSES} conf={args.conf} "
          f"flip_frames={args.flip_frames}")

    writer = None
    if args.save_clip:
        os.makedirs("outputs", exist_ok=True)
        clip_path = f"outputs/side_angle_stall{args.stall}.mp4"
        try:
            import imageio
            writer = ("imageio", imageio.get_writer(clip_path, fps=fps, codec="libx264",
                                                    quality=8, macro_block_size=None))
        except Exception:
            writer = ("cv2", cv2.VideoWriter(
                clip_path.replace(".mp4", ".avi"),
                cv2.VideoWriter_fourcc(*"MJPG"), fps, (w, h)))

    # temporal smoothing: committed state flips only after flip_frames consistent reads
    recent = deque(maxlen=args.flip_frames)
    committed = None          # None until first commit; then True/False
    transitions = []          # (frame_idx, from_state, to_state)
    fidx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if resize_to:
            frame = cv2.resize(frame, resize_to)

        res = model(frame, conf=args.conf, classes=VEHICLE_CLASSES, verbose=False)[0]
        raw_occupied = False
        pts = []
        for b in res.boxes:
            p = anchor_point(b.xyxy[0].tolist(), args.anchor)
            pts.append(p)
            if cv2.pointPolygonTest(poly, p, False) >= 0:
                raw_occupied = True

        recent.append(raw_occupied)
        # commit a flip only when the whole window agrees (and it differs from committed)
        if len(recent) == recent.maxlen and all(v == recent[0] for v in recent):
            if committed is None:
                committed = recent[0]
            elif recent[0] != committed:
                transitions.append((fidx, committed, recent[0]))
                print(f"  frame {fidx:4d}: {('OCCUPIED' if committed else 'OPEN')}"
                      f" -> {('OCCUPIED' if recent[0] else 'OPEN')}")
                committed = recent[0]

        state = committed if committed is not None else raw_occupied
        if not args.no_window or writer:
            vis = frame.copy()
            col = (0, 0, 255) if state else (0, 200, 0)  # BGR: red occupied, green open
            cv2.polylines(vis, [poly], True, col, 3)
            for p in pts:
                inside = cv2.pointPolygonTest(poly, p, False) >= 0
                cv2.circle(vis, p, 6, (0, 0, 255) if inside else (255, 180, 0), -1)
            label = f"stall {args.stall}: {'OCCUPIED' if state else 'OPEN'}"
            cv2.rectangle(vis, (0, 0), (520, 40), (30, 30, 30), -1)
            cv2.putText(vis, label, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, col, 2)

            if writer:
                kind, wobj = writer
                wobj.append_data(cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)) if kind == "imageio" else wobj.write(vis)
            if not args.no_window:
                cv2.imshow("side-angle stall", vis)
                if cv2.waitKey(1) & 0xFF == 27:
                    break
        fidx += 1

    cap.release()
    if writer:
        writer[1].close() if writer[0] == "imageio" else writer[1].release()
    cv2.destroyAllWindows()

    print(f"\nprocessed {fidx} frames")
    if transitions:
        for f, a, b in transitions:
            print(f"  transition at frame {f}: {'OCCUPIED' if a else 'OPEN'} -> {'OCCUPIED' if b else 'OPEN'}")
        print("OCCUPIED -> OPEN demonstrated." if any(a and not b for f, a, b in transitions)
              else "state changed, but no OCCUPIED->OPEN flip.")
    else:
        print("no committed state transitions (stall stayed one state the whole clip).")


if __name__ == "__main__":
    main()
