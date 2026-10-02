"""
Dump ONE native-resolution reference frame to annotate on.

The old img.py resizes to 1080x600; the side-angle work uses NATIVE 1920x1080 so the
stall polygon you draw matches the frame side_angle_demo.py runs detection on. Grab a
frame with this, draw your stall in the Tk annotator on it, and the coordinates line up.

Usage:
    python grab_frame.py videos/IMG_3762.mp4 --frame 0 --out ref_3762.jpg
"""

import argparse

import cv2


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--frame", type=int, default=0, help="frame index to grab")
    ap.add_argument("--out", default="ref_frame.jpg")
    args = ap.parse_args()

    cap = cv2.VideoCapture(args.source)
    assert cap.isOpened(), f"cannot open {args.source}"
    cap.set(cv2.CAP_PROP_POS_FRAMES, args.frame)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"could not read frame {args.frame} from {args.source}")
    cv2.imwrite(args.out, frame)
    print(f"wrote {args.out} ({frame.shape[1]}x{frame.shape[0]}) from frame {args.frame}")


if __name__ == "__main__":
    main()
