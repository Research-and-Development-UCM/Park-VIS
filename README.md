# 🅿️ ParkVis — Smart Parking Detection

Real-time parking-space occupancy detection for the **Bellevue Lot at UC Merced**, built on
[YOLOv11](https://docs.ultralytics.com/). The system watches an elevated camera view of the lot,
detects vehicles, maps them onto predefined parking-stall regions, and reports how many spaces are
**occupied** vs. **available** — all without storing any image data.

---

## How it works

```
┌──────────────┐     ┌──────────────┐     ┌────────────────┐     ┌──────────────┐
│  Camera feed │ ──▶ │  YOLOv11     │ ──▶ │ Stall mapping  │ ──▶ │  Occupancy   │
│  (lot view)  │     │  detection   │     │ (point-in-poly)│     │  count + UI  │
└──────────────┘     └──────────────┘     └────────────────┘     └──────────────┘
```

1. **Detect** — YOLOv11 finds vehicles in each frame.
2. **Map** — each detection's centroid is tested against hand-drawn stall polygons.
3. **Report** — a stall with a vehicle centroid inside it is marked occupied; the rest are available.

Occupied stalls are outlined in **red**, available stalls in **green**, and a live
Occupancy / Available tally is drawn on the frame.

---

## Project layout

| File | Purpose |
|------|---------|
| `main.py` | Entry point — runs detection + occupancy on a video feed |
| `parking.py` | Core logic: `ParkingManagement` (occupancy) and `ParkingPtsSelection` (annotation UI) |
| `se.py` | Launches the stall-annotation tool |
| `img.py` | Utility to grab still frames from a video for annotation |
| `bounding_boxes.json` | Saved parking-stall regions (7 stalls currently defined) |
| `yolo11s.pt` | YOLOv11-small model weights |

---

## Quick start

### 1. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install ultralytics opencv-python
```

> **macOS note:** `tkinter` is part of the Python standard library — it is **not** a pip package.
> If Ultralytics tries to `pip install tkinter` and fails, that check can be ignored (the import
> itself works). Confirm with `python -m tkinter`; a small window with Tk 8.6 means you're set.

### 2. Grab a reference frame

Pull a still from your lot video to annotate on:

```bash
python img.py
```

### 3. Annotate parking stalls

```bash
python se.py
```

Upload the reference frame, click **4 points per stall** to outline each space, then **Save**.
This writes `bounding_boxes.json`.

> ⚠️ **Stall regions are view-specific.** They only line up with the exact camera angle and frame
> resolution they were drawn on. Re-annotate whenever the camera moves or the frame size changes.

### 4. Run detection

```bash
python main.py
```

Press **Esc** to quit.

---

## Configuration

Set in `main.py` when creating the manager:

```python
parking_manager = ParkingManagement(
    model="yolo11s.pt",            # model weights
    classes=[2],                   # COCO class 2 = car (add 3,5,7 for motorcycle/bus/truck)
    json_file="bounding_boxes.json"
)
```

---

## Design constraints

- **No image storage.** Frames are processed in memory and discarded — occupancy is the only thing
  that persists. This is a hard requirement agreed with campus staff and is honored by design.
- **Privacy first.** No license-plate reading, no person tracking. Each stall reduces to a single
  occupied/available bit.

---

## Status & known limitations

🚧 **Early prototype.** The detection + occupancy loop runs, but accuracy tuning is ongoing:

- Stall regions in `bounding_boxes.json` need to be **re-drawn on the current elevated frame** —
  they were annotated on a different view and don't yet align with the live footage.
- Only part of the lot is annotated; the empty stalls on the far side still need regions.
- Tightly packed rows partially occlude each other; raising inference resolution (`imgsz=1280`)
  recovers some missed vehicles.

See the issues above before trusting the occupancy numbers.

---

## Roadmap

- [ ] Re-annotate full lot on the mounted-camera view
- [ ] Clean up duplicate class definitions in `parking.py`
- [ ] Tune detection resolution + confidence for packed rows
- [ ] Optional shadow-robust preprocessing (CLAHE) for hard lighting
- [ ] Backend API + mobile app to surface live availability

---

## Acknowledgements

Built on [Ultralytics YOLOv11](https://github.com/ultralytics/ultralytics). Detection powered by
the COCO-pretrained vehicle classes, fine-tuned on Bellevue Lot footage.
