"""Background uploader for ``Improve AI`` training feedback.

The on-prem ``/api/admin/feedback/submit`` handler saves submissions
to local ``training_feedback/{images,annotations}/`` and returns
immediately. A separate background task (see
``backend.scheduler._feedback_upload_loop``) periodically scans those
directories and uploads everything to the cloud billing portal's
S3 bucket via presigned PUT URLs.

This separation gives us a few things:

* **Resilience** — submissions succeed even when the cloud or S3 is
  unreachable. Files just queue up locally and drain when connectivity
  comes back.
* **Backpressure** — when the queue grows past a threshold we expose a
  warning to the UI so operators notice the upload loop is broken.
* **Bounded retry** — files that fail too many times (e.g. account
  deleted, HWID changed) move into ``training_feedback/failed/`` so
  they stop blocking the rest. They're preserved for debugging.
* **No AWS creds on-prem** — the cloud is the only thing that holds
  AWS keys, which is critical since the on-prem image is publicly
  available.

The functions here are split out from ``scheduler.py`` so they're
unit-testable in isolation (no event loop, no scheduler state).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

# Reuse the project's loguru-backed logger so messages land in the
# same format / sinks as everything else (otherwise tests + test
# log inspection see two different formats from the same module).
from ..logging_config import vulture_logger as logger
from ..config import config


# Configuration constants. None of these are user-tunable today — if
# we ever need to expose them, do so via env vars in ``config.py``.
TICK_INTERVAL_SECONDS = 60.0
BATCH_SIZE = 5                    # files uploaded per tick
MAX_ATTEMPTS = 30                 # move to ``failed/`` after this many failures
MAX_AGE_DAYS = 30                 # same, but for files older than this
REQUEST_TIMEOUT_SECONDS = 30.0


# Paths under the persistent data directory (``config.DATA_DIR``).
# ``training_feedback`` is the legacy name and we keep it for backward
# compatibility with any pre-existing files.
ANNOTATIONS_DIR = os.path.join(config.DATA_DIR, "training_feedback", "annotations")
IMAGES_DIR = os.path.join(config.DATA_DIR, "training_feedback", "images")
FAILED_DIR = os.path.join(config.DATA_DIR, "training_feedback", "failed")


@dataclass
class UploadOutcome:
    """Result of attempting to upload one feedback submission.

    Returned to the scheduler so it can track health metrics (we don't
    currently expose these to the UI — the queue length is the only
    externally-visible signal).
    """
    filename: str
    success: bool
    error: Optional[str] = None


def ensure_dirs() -> None:
    """Create the upload directories if they don't already exist.

    Called on every tick so the loop is robust against the directories
    being deleted out from under us (e.g. by an operator running
    ``rm -rf training_feedback``).
    """
    for d in (ANNOTATIONS_DIR, IMAGES_DIR, FAILED_DIR):
        os.makedirs(d, exist_ok=True)


def pending_count() -> int:
    """Number of JSON files waiting to be uploaded.

    Cheap O(n) directory listing — fine at the volumes we expect (a
    busy operator might submit hundreds per day). If this ever needs
    to scale, swap to counting via scandir + a cached stat.
    """
    if not os.path.isdir(ANNOTATIONS_DIR):
        return 0
    try:
        return sum(
            1 for f in os.listdir(ANNOTATIONS_DIR) if f.endswith(".json")
        )
    except OSError as exc:
        logger.warning("Failed to list {d}: {e}", d=ANNOTATIONS_DIR, e=exc)
        return 0


def _is_too_old(submitted_at_iso: Optional[str]) -> bool:
    """True if the submission is older than ``MAX_AGE_DAYS``."""
    if not submitted_at_iso:
        return False
    try:
        submitted_at = datetime.fromisoformat(submitted_at_iso)
    except (TypeError, ValueError):
        return False
    if submitted_at.tzinfo is None:
        submitted_at = submitted_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - submitted_at
    return age.days >= MAX_AGE_DAYS


def _should_quarantine(metadata: dict) -> Optional[str]:
    """Return a reason string if the submission should be moved to
    ``failed/``, else None.

    Two ways to be quarantined:
    1. ``upload_attempts >= MAX_ATTEMPTS`` — keeps failing every tick
       (HWID no longer matches a cloud device, account deleted, etc).
    2. Submitted more than ``MAX_AGE_DAYS`` ago — even if it might
       eventually upload, the data is too old to be useful for training
       and the queue needs to drain.
    """
    attempts = int(metadata.get("upload_attempts", 0) or 0)
    if attempts >= MAX_ATTEMPTS:
        return f"exceeded {MAX_ATTEMPTS} upload attempts"
    if _is_too_old(metadata.get("submitted_at")):
        return f"older than {MAX_AGE_DAYS} days"
    return None


def _move_to_failed(json_filename: str, reason: str) -> None:
    """Move a JSON + its image to ``training_feedback/failed/``.

    Both files keep their original names so an operator can dig into
    a specific submission by scanning the directory. We move (not
    copy) so the active queue actually drains — otherwise the
    quarantine would just bloat the directory without freeing anything.
    """
    import shutil

    base = json_filename[:-5]  # strip .json
    src_json = os.path.join(ANNOTATIONS_DIR, json_filename)
    src_img = os.path.join(IMAGES_DIR, f"{base}.jpg")
    dst_json = os.path.join(FAILED_DIR, json_filename)
    dst_img = os.path.join(FAILED_DIR, f"{base}.jpg")
    try:
        if os.path.exists(src_json):
            shutil.move(src_json, dst_json)
        if os.path.exists(src_img):
            shutil.move(src_img, dst_img)
        logger.warning(
            "Quarantined feedback submission {name}: {reason}",
            name=json_filename,
            reason=reason,
        )
    except OSError as exc:
        logger.error(
            "Failed to move {name} to failed/: {e}",
            name=json_filename,
            e=exc,
        )


def _bump_attempts(json_path: str) -> None:
    """Increment ``upload_attempts`` and stamp ``last_attempt_at``.

    Done in-place so the next tick sees the updated counter and we can
    decide whether to retry or quarantine. We rewrite the whole JSON
    rather than patching bytes to keep the file format predictable.
    """
    try:
        with open(json_path, "r") as f:
            metadata = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    metadata["upload_attempts"] = int(metadata.get("upload_attempts", 0) or 0) + 1
    metadata["last_attempt_at"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(json_path, "w") as f:
            json.dump(metadata, f, indent=2)
    except OSError as exc:
        logger.warning("Failed to bump attempts on {p}: {e}", p=json_path, e=exc)


async def _upload_one(
    client: httpx.AsyncClient,
    cloud_base_url: str,
    json_filename: str,
    metadata: dict,
) -> UploadOutcome:
    """Try to upload a single submission. Returns the outcome (never
    raises — exceptions are caught and surfaced as ``success=False``).

    Failure modes that are NOT counted as "this file is broken" — they
    leave the file in the queue and bump its attempt counter:

    * Cloud unreachable (network error)
    * Cloud returns 5xx
    * S3 PUT fails (network error or presigned URL expired)

    These ARE treated as terminal — the file moves to ``failed/``:

    * Cloud returns 403/404 (HWID no longer matches an active device
      OR account not found OR presign is over the daily rate limit)
    """
    json_path = os.path.join(ANNOTATIONS_DIR, json_filename)
    base = json_filename[:-5]
    img_path = os.path.join(IMAGES_DIR, f"{base}.jpg")

    # Read image bytes. If the image is missing (orphaned annotation,
    # disk corruption, manual cleanup) we move the annotation straight
    # to failed/ — there's nothing to upload.
    try:
        with open(img_path, "rb") as f:
            img_bytes = f.read()
    except OSError as exc:
        _move_to_failed(
            json_filename,
            f"image file missing or unreadable ({exc})",
        )
        return UploadOutcome(json_filename, False, str(exc))

    # Ask the cloud for presigned URLs.
    # The on-prem has no email column on its User model — only
    # ``username``. The cloud resolves the owning user via HWID
    # alone (lookups the device → user), so we don't need to send
    # email at all.
    # ``submitted_at`` is passed through so the S3 key encodes the
    # user-click time, not when this upload actually landed (could be
    # hours later if the cloud was unreachable). Falls back to "now"
    # on the cloud side if we somehow lost the field.
    presign_body = {
        "hwid": metadata.get("hwid", ""),
        "camera_id": int(metadata["camera_id"]),
        "scan_id": int(metadata["scan_id"]),
        "submitted_at": metadata.get("submitted_at"),
        "app_version": metadata.get("app_version", config.APP_VERSION),
    }
    try:
        presign_resp = await client.post(
            f"{cloud_base_url.rstrip('/')}/api/v1/feedback/presign",
            json=presign_body,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        # Cloud unreachable — retry next tick. Don't bump attempts here
        # because that would mask the difference between "cloud is down"
        # (transient) and "this submission will never upload" (terminal).
        logger.warning(
            "Presign request failed for {n}: {e}", n=json_filename, e=exc
        )
        return UploadOutcome(json_filename, False, str(exc))

    if presign_resp.status_code == 429:
        # Presign rate-limited us — file isn't broken, leave it queued
        # but DO bump attempts so we eventually quarantine if the user
        # has been hammering us all day.
        _bump_attempts(json_path)
        return UploadOutcome(
            json_filename,
            False,
            "presign rate limit hit (429)",
        )

    if presign_resp.status_code in (403, 404):
        # HWID no longer matches a cloud device, or account not found.
        # This submission will never upload — quarantine immediately
        # rather than wasting 30 ticks discovering that.
        _move_to_failed(
            json_filename,
            f"cloud rejected presign ({presign_resp.status_code})",
        )
        return UploadOutcome(
            json_filename,
            False,
            f"cloud rejected presign ({presign_resp.status_code})",
        )

    if presign_resp.status_code >= 500:
        # Cloud error — retry next tick, don't bump attempts (the file
        # isn't broken, the cloud is).
        logger.warning(
            "Presign {sc} for {n}: {body}",
            sc=presign_resp.status_code,
            n=json_filename,
            body=presign_resp.text[:200],
        )
        return UploadOutcome(
            json_filename,
            False,
            f"cloud {presign_resp.status_code}",
        )

    if presign_resp.status_code != 200:
        # Any other 4xx — likely a malformed submission (bad scan_id,
        # missing hwid). Quarantine; retries won't help.
        _move_to_failed(
            json_filename,
            f"presign failed {presign_resp.status_code}",
        )
        return UploadOutcome(
            json_filename,
            False,
            f"presign {presign_resp.status_code}",
        )

    try:
        presign_data = presign_resp.json()
        image_url = presign_data["image_upload_url"]
        metadata_url = presign_data["metadata_upload_url"]
    except (ValueError, KeyError) as exc:
        _bump_attempts(json_path)
        return UploadOutcome(
            json_filename,
            False,
            f"malformed presign response: {exc}",
        )

    # PUT image to S3.
    try:
        img_put = await client.put(
            image_url,
            content=img_bytes,
            headers={"Content-Type": "image/jpeg"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        _bump_attempts(json_path)
        return UploadOutcome(
            json_filename,
            False,
            f"image PUT network error: {exc}",
        )
    if img_put.status_code not in (200, 204):
        _bump_attempts(json_path)
        return UploadOutcome(
            json_filename,
            False,
            f"image PUT {img_put.status_code}",
        )

    # PUT metadata to S3. The metadata includes everything in the JSON
    # plus nothing — we send the JSON exactly as written to disk so
    # an operator inspecting ``training_feedback/failed/`` has the same
    # view as the uploader.
    try:
        with open(json_path, "r") as f:
            metadata_bytes = f.read().encode("utf-8")
    except OSError as exc:
        # Image already uploaded, but we can't read the metadata to
        # send it. Mark as failed for manual recovery — better to be
        # safe than to have orphan images in S3 with no annotations.
        _move_to_failed(
            json_filename,
            f"could not read metadata after image upload ({exc})",
        )
        return UploadOutcome(
            json_filename,
            False,
            f"metadata read failed after image upload: {exc}",
        )
    try:
        meta_put = await client.put(
            metadata_url,
            content=metadata_bytes,
            headers={"Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        _bump_attempts(json_path)
        return UploadOutcome(
            json_filename,
            False,
            f"metadata PUT network error: {exc}",
        )
    if meta_put.status_code not in (200, 204):
        _bump_attempts(json_path)
        return UploadOutcome(
            json_filename,
            False,
            f"metadata PUT {meta_put.status_code}",
        )

    # Both uploads succeeded. Delete the local files — atomic-ish via
    # a try/except so a partial delete doesn't leave us in a worse
    # state than we started (which would be: re-uploading the same
    # submission on the next tick).
    try:
        os.remove(img_path)
    except OSError:
        pass
    try:
        os.remove(json_path)
    except OSError as exc:
        # Image gone but JSON still here — the next tick will try to
        # upload again. Worst case is a duplicate image in S3 with the
        # same key (S3 overwrites on PUT), so it's idempotent. Log so
        # we know about it.
        logger.warning(
            "Image uploaded but could not delete local JSON {n}: {e}",
            n=json_filename,
            e=exc,
        )
    logger.info(
        "Uploaded feedback {n} (camera {c}, scan {s})",
        n=json_filename,
        c=metadata.get("camera_id"),
        s=metadata.get("scan_id"),
    )
    return UploadOutcome(json_filename, True)


async def run_upload_tick(cloud_base_url: str) -> list[UploadOutcome]:
    """One tick of the uploader. Returns the per-file outcomes.

    Processes up to ``BATCH_SIZE`` files per tick to avoid long-running
    tasks blocking the rest of the scheduler. Anything left in the
    queue rolls over to the next tick.
    """
    if not cloud_base_url:
        return []

    ensure_dirs()

    if not os.path.isdir(ANNOTATIONS_DIR):
        return []

    try:
        all_files = sorted(
            f for f in os.listdir(ANNOTATIONS_DIR) if f.endswith(".json")
        )
    except OSError as exc:
        logger.warning("Failed to list annotations dir: {e}", e=exc)
        return []

    batch = all_files[:BATCH_SIZE]
    if not batch:
        return []

    outcomes: list[UploadOutcome] = []
    async with httpx.AsyncClient() as client:
        for json_filename in batch:
            # Wrap the full per-file body so a malformed metadata
            # payload (KeyError on missing camera_id, ValueError on
            # non-numeric id) doesn't abort the rest of the batch.
            # Without this guard a single corrupt file could leave
            # every following file stuck at the head of the queue for
            # 30 days before being quarantined individually.
            try:
                json_path = os.path.join(ANNOTATIONS_DIR, json_filename)
                try:
                    with open(json_path, "r") as f:
                        metadata = json.load(f)
                except (OSError, json.JSONDecodeError) as exc:
                    _move_to_failed(json_filename, f"corrupt metadata JSON: {exc}")
                    outcomes.append(UploadOutcome(json_filename, False, str(exc)))
                    continue

                quarantine_reason = _should_quarantine(metadata)
                if quarantine_reason:
                    _move_to_failed(json_filename, quarantine_reason)
                    outcomes.append(UploadOutcome(json_filename, False, quarantine_reason))
                    continue

                outcome = await _upload_one(client, cloud_base_url, json_filename, metadata)
                outcomes.append(outcome)
            except (KeyError, ValueError, TypeError) as exc:
                logger.error(
                    "Malformed feedback metadata in {n}: {e}; quarantining",
                    n=json_filename, e=exc,
                )
                _move_to_failed(json_filename, f"malformed metadata: {exc}")
                outcomes.append(UploadOutcome(json_filename, False, f"malformed metadata: {exc}"))

    return outcomes