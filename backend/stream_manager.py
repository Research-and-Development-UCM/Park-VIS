import threading
import time
import os
import subprocess
import requests
import cv2
import hashlib
import numpy as np
from .logging_config import vulture_logger as logger
from .config import config

# We use gi.repository for direct GStreamer access
import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GstApp, GLib

class StreamManager:
    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(StreamManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        self.streams = {} # {camera_id: {'pipeline': Gst.Pipeline, 'last_frame': ndarray, 'active': bool, ...}}
        self.cooldowns = {} # {camera_id: last_error_time}
        self.manager_active = True
        self._gst_initialized = False

        # Start a debug logger thread
        self.debug_thread = threading.Thread(target=self._debug_logger, daemon=True, name="StreamManager-DebugLogger")
        self.debug_thread.start()
        
        self._initialized = True
        logger.info("StreamManager created (GStreamer init deferred to first stream)")

    def _ensure_gst(self):
        """Lazily initialize GStreamer on first use."""
        if self._gst_initialized:
            return
        logger.info("GStreamer init starting...")
        Gst.init(None)
        logger.info("GStreamer init done")
        
        # Start the GLib MainLoop in a background thread to handle GStreamer signals
        logger.info("Creating GLib MainLoop...")
        self.loop = GLib.MainLoop()
        logger.info("Starting MainLoop thread...")
        self.loop_thread = threading.Thread(target=self.loop.run, daemon=True, name="StreamManager-GLibMainLoop")
        self.loop_thread.start()
        logger.info("MainLoop thread started")
        
        self._gst_initialized = True
        logger.info("GStreamer StreamManager fully initialized")

    def _debug_logger(self):
        while self.manager_active:
            time.sleep(10) # 10s for deep debugging
            if not self.streams or not self.manager_active:
                continue
            
            logger.debug("--- [STREAM DEBUG STATUS] ---")
            for cid, data in self.streams.items():
                status = "ACTIVE" if data['active'] else "INACTIVE"
                frame_info = "N/A"
                fps = 0
                uptime = time.time() - data['start_time']
                
                with data['lock']:
                    if data['last_frame'] is not None:
                        h, w = data['last_frame'].shape[:2]
                        frame_info = f"{w}x{h}"
                        fps = data['frames_received'] / uptime if uptime > 0 else 0
                
                logger.debug("  Cam {cid}: {status} | Res: {res} | Avg FPS: {fps:.1f} | Uptime: {up:.0f}s", cid=cid, status=status, res=frame_info, fps=fps, up=uptime)
            logger.debug("-----------------------------")

    def _get_youtube_url(self, youtube_url, preferred_res="720"):
        try:
            # We add stealth flags:
            # --referer: Makes it look like the request is coming from YouTube's own site
            # --user-agent: Spoof a real Chrome browser
            # --geo-bypass: Avoids some regional blocks
            format_str = f"bestvideo[height<={preferred_res}][ext=mp4]/best[height<={preferred_res}]"
            cmd = [
                "yt-dlp", "-g", "-f", format_str, 
                "--no-warnings", "--no-cache-dir", 
                "--referer", "https://www.youtube.com/",
                "--user-agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "--extractor-args", "youtube:player_client=web",
                youtube_url
            ]
            logger.debug("Executing yt-dlp: {cmd}", cmd=" ".join(cmd))
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            resolved_url = result.stdout.strip()
            # If multiple URLs are returned (video + audio), take the first one (video)
            resolved_url = resolved_url.split('\n')[0]
            logger.debug("Successfully resolved direct YouTube URL (Length: {length})", length=len(resolved_url))
            return resolved_url
        except Exception as e:
            logger.error("Error extracting YouTube URL: {e}", e=e)
            return None

    def fetch_youtube_thumbnail(self, url: str):
        """Extracts Video ID and fetches the best available thumbnail."""
        video_id = None
        if "v=" in url:
            video_id = url.split("v=")[1].split("&")[0]
        elif "be/" in url:
            video_id = url.split("be/")[1].split("?")[0]
        elif "shorts/" in url:
            video_id = url.split("shorts/")[1].split("?")[0]
        elif "live/" in url:
            video_id = url.split("live/")[1].split("?")[0]
        
        if not video_id:
            return None
        
        # Try maxres, then hq, then default
        thumb_urls = [
            f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
            f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
            f"https://img.youtube.com/vi/{video_id}/0.jpg"
        ]
        for t_url in thumb_urls:
            try:
                resp = requests.get(t_url, timeout=5)
                if resp.status_code == 200:
                    # VALIDATION: Ensure image is high enough resolution
                    img_data = resp.content
                    nparr = np.frombuffer(img_data, np.uint8)
                    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if img is not None:
                        h, w = img.shape[:2]
                        if h >= 600 and w >= 600:
                            return img_data
                    else:
                        logger.warning("Ignoring tiny YouTube thumbnail ({w}x{h}) from {u}", w=w, h=h, u=t_url)
            # ``except:`` swallowed BaseException (KeyboardInterrupt,
            # SystemExit) and made the poller impossible to stop cleanly
            # with Ctrl+C. Narrow it to Exception.
            except Exception:
                continue
        return None

    def _get_pipeline_string(self, camera_id, source_type, url, user=None, password=None, res="1080", fps=5):
        """Constructs a GStreamer pipeline string with FPS capping."""
        if not url:
            logger.error("Cannot construct pipeline for camera {cid}: URL/Path is missing", cid=camera_id)
            return None
            
        # Rate limiting and conversion elements
        # Moved rate limit to be applied AFTER decoding for better control
        rate_limit = f" ! videorate ! video/x-raw,framerate={fps}/1 "

        if source_type == "youtube":
            stream_url = self._get_youtube_url(url, preferred_res=res)
            if not stream_url: return None
            # We add a queue named 'net_probe' immediately after the source
            # Explicit user-agent is REQUIRED to match yt-dlp's resolution
            ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            pipeline = (
                f'souphttpsrc location="{stream_url}" user-agent="{ua}" ! '
                f'queue name=net_probe_{camera_id} ! decodebin ! '
                f'videoconvert ! video/x-raw,format=BGR {rate_limit} ! '
                f'appsink name=sink_{camera_id} emit-signals=true sync=true drop=true max-buffers=1'
            )
            logger.debug("YouTube Pipeline (Cam {cid}): {p}", cid=camera_id, p=pipeline)
            return pipeline
        
        if source_type == "rtsp":
            rtsp_url = url
            if user and password:
                proto, rest = url.split("://", 1)
                rtsp_url = f"{proto}://{user}:{password}@{rest}"

            pipeline = (
                f'rtspsrc location="{rtsp_url}" latency=500 ! queue name=net_probe_{camera_id} ! rtph264depay ! '
                f'h264parse ! decodebin ! videoconvert ! video/x-raw,format=BGR {rate_limit} ! '
                f'queue max-size-buffers=2 ! '
                f'appsink name=sink_{camera_id} emit-signals=true sync=true drop=true max-buffers=1'
            )
            # H3 audit fix: redact credentials before logging.
            # The full pipeline string is built from the unredacted
            # URL (GStreamer needs the real credentials), but the log
            # line must never expose them — anyone with log access
            # (operator, support, log aggregator) would otherwise
            # recover the RTSP password.
            logger.debug(
                "RTSP Pipeline (Cam {cid}): {p}",
                cid=camera_id,
                p=_redact_url_credentials(pipeline),
            )
            return pipeline
        
        if source_type == "video":
            if not os.path.exists(url):
                logger.error("Video file not found for camera {cid}: {path}", cid=camera_id, path=url)
                return None
            
            # For files, sync=true is vital to avoid reading at 1000fps
            pipeline = (
                f'filesrc location="{url}" ! queue name=net_probe_{camera_id} ! decodebin ! videoconvert ! '
                f'video/x-raw,format=BGR {rate_limit} ! '
                f'appsink name=sink_{camera_id} emit-signals=true sync=true drop=true max-buffers=1'
            )
            logger.debug("Local Video Pipeline (Cam {cid}): {p}", cid=camera_id, p=pipeline)
            return pipeline
        
        return None

    def _check_and_restart_if_params_changed(self, camera_id, source_type, url, user, password, res, fps, youtube_mode=None):
        data = self.streams[camera_id]
        
        # Check if basic parameters changed
        changed = (
            data['source_type'] != source_type or
            data['url'] != url or
            data['res'] != res or
            data['fps'] != fps or
            data['user'] != user or
            data['password'] != password or
            data.get('youtube_mode') != youtube_mode
        )

        # For YouTube, also check if the resolved URL is too old (e.g. > 1 hour)
        # as YouTube tokens eventually expire.
        if not changed and source_type == "youtube" and youtube_mode != "thumbnail":
            if time.time() - data['start_time'] > 3600:
                logger.info("YouTube stream lease expired for camera {cid}, refreshing.", cid=camera_id)
                changed = True

        if changed:
            logger.info("Stream parameters changed for camera {cid}, restarting pipeline.", cid=camera_id)
            self.stop_stream(camera_id, remove=True)

    def _get_youtube_pipeline(self, camera_id, url, preferred_res="720", fps=5):
        """Uses yt-dlp as a subprocess to pipe video into GStreamer via stdin."""
        rate_limit = f" ! videorate ! video/x-raw,framerate={fps}/1 "

        # We use format 95/96/94 as they are highly stable live formats
        # best[height<=...] is the fallback
        cmd = [
            "yt-dlp",
            "-o", "-",
            "--format", f"95/96/94/bestvideo[height<={preferred_res}][ext=mp4]+bestaudio/best",
            "--no-part", "--no-cache-dir",
            "--downloader", "ffmpeg",
            "--downloader-args", "ffmpeg:-re",
            url
        ]

        # H2 audit fix: build the pipeline BEFORE the subprocess so
        # we can read back the ``fdsrc`` element and set its ``fd``
        # property to ``proc.stdout.fileno()``.  The previous code
        # hardcoded ``fdsrc fd=0`` (the Python process's stdin) but
        # ``proc.stdout`` lives on a different file descriptor —
        # yt-dlp's output was never wired to GStreamer, so its
        # stdout pipe filled up and yt-dlp blocked, while
        # GStreamer read from an unrelated fd (typically /dev/null
        # or the terminal).
        pipeline_str_template = (
            "fdsrc fd={fd} ! decodebin ! videoconvert ! "
            "video/x-raw,format=BGR {rate} ! "
            "appsink name=sink_{cid} emit-signals=true sync=true drop=true max-buffers=1"
        )

        logger.info("Launching YouTube Pipe for Cam {cid}: {cmd}", cid=camera_id, cmd=" ".join(cmd))

        try:
            # Start yt-dlp process first so we have its stdout fd
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            stdout_fd = proc.stdout.fileno()

            # Now build the pipeline with the correct fd
            pipeline_str = pipeline_str_template.format(
                fd=stdout_fd, rate=rate_limit, cid=camera_id,
            )
            pipeline = Gst.parse_launch(pipeline_str)

            # Belt-and-suspenders: also set the fdsrc property
            # explicitly in case the parse-time fd is not honored
            # (some GStreamer versions require this).
            fdsrc = pipeline.get_by_name("fdsrc0")
            if fdsrc is not None:
                fdsrc.set_property("fd", stdout_fd)

            return pipeline, proc
        except Exception as e:
            logger.error("Failed to start YouTube pipe: {e}", e=e)
            return None, None

    def start_stream(self, camera_id, source_type, url, user=None, password=None, res="1080", fps=5, youtube_mode=None):
        with self._lock:
            # Check for cooldown (15s default, ignored in DEBUG mode)
            is_debug = config.LOG_LEVEL == "DEBUG"
            last_error = self.cooldowns.get(camera_id, 0)
            if not is_debug and (time.time() - last_error < 15):
                # Still in cooldown, don't try yet
                return

            # Check if already running
            if camera_id in self.streams:
                self._check_and_restart_if_params_changed(camera_id, source_type, url, user, password, res, fps, youtube_mode)
                if camera_id in self.streams and self.streams[camera_id]['active']:
                    return

            if source_type == "youtube" and youtube_mode == "thumbnail":
                logger.info("Starting YouTube Thumbnail poller for camera {cid}...", cid=camera_id)
                stream_data = {
                    'pipeline': None,
                    'proc': None,
                    'source_type': source_type,
                    'youtube_mode': youtube_mode,
                    'url': url,
                    'user': user,
                    'password': password,
                    'res': res,
                    'fps': fps,
                    'last_frame': None,
                    'last_frame_time': 0,
                    'last_frame_hash': None,
                    'active': True,
                    'shutdown_flag': False,
                    'lock': threading.Lock(),
                    'frames_received': 0,
                    'frames_dropped': 0,
                    'bytes_recent': [],
                    'network_bytes_recent': [],
                    'start_time': time.time(),
                    'errors': [],
                    'handler_id': None,
                    'bus_id': None
                }
                self.streams[camera_id] = stream_data
                
                # Launch poller thread
                def thumb_poller():
                    while stream_data['active'] and not stream_data['shutdown_flag']:
                        try:
                            content = self.fetch_youtube_thumbnail(url)
                            if content:
                                new_hash = hashlib.sha256(content).hexdigest()
                                with stream_data['lock']:
                                    if new_hash != stream_data.get('last_frame_hash'):
                                        nparr = np.frombuffer(content, np.uint8)
                                        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                                        if frame is not None:
                                            stream_data['last_frame'] = frame
                                            stream_data['last_frame_time'] = time.time()
                                            stream_data['last_frame_hash'] = new_hash
                                            stream_data['frames_received'] += 1
                                            logger.debug("YouTube thumbnail changed for camera {cid}", cid=camera_id)
                                        else:
                                            logger.warning("Failed to decode YouTube thumbnail for camera {cid}", cid=camera_id)
                                    else:
                                        # Update heartbeat so we know the poller is still alive
                                        stream_data['last_heartbeat'] = time.time()
                        except Exception as e:
                            logger.error("Thumbnail poller error (Cam {cid}): {e}", cid=camera_id, e=e)
                        
                        # Wait 60s or until shutdown
                        for _ in range(600): # 60 seconds in 100ms chunks
                            if not stream_data['active'] or stream_data['shutdown_flag']:
                                break
                            time.sleep(0.1)
                    logger.debug("Thumbnail poller for camera {cid} exited.", cid=camera_id)

                threading.Thread(target=thumb_poller, daemon=True, name=f"YouTube-ThumbPoller-Cam{camera_id}").start()
                return

            self._ensure_gst()

            pipeline = None
            proc = None
            
            if source_type == "youtube":
                pipeline, proc = self._get_youtube_pipeline(camera_id, url, preferred_res=res, fps=fps)
            else:
                pipeline_str = self._get_pipeline_string(camera_id, source_type, url, user, password, res, fps)
                if not pipeline_str:
                    logger.error("Failed to construct pipeline for camera {cid}", cid=camera_id)
                    return
                logger.info("Starting stream for camera {cid}...", cid=camera_id)
                try:
                    pipeline = Gst.parse_launch(pipeline_str)
                except Exception as e:
                    logger.error("GStreamer parse error for camera {cid}: {e}", cid=camera_id, e=e)
                    return

            appsink = pipeline.get_by_name(f"sink_{camera_id}")

            stream_data = {
                'pipeline': pipeline,
                'proc': proc,
                'source_type': source_type,
                'youtube_mode': youtube_mode,
                'url': url,
                'user': user,
                'password': password,
                'res': res,
                'fps': fps,
                'last_frame': None,
                'last_frame_time': 0,
                'active': True,
                'shutdown_flag': False,
                'lock': threading.Lock(),
                'frames_received': 0,
                'frames_dropped': 0,
                'bytes_recent': [],
                'network_bytes_recent': [],
                'start_time': time.time(),
                'errors': [],
                'handler_id': None,
                'bus_id': None
            }
            # Audit fix: if a previous (inactive/diagnostic) entry is still
            # holding a pipeline or yt-dlp subprocess, tear it down before we
            # overwrite it, otherwise those resources are silently leaked.
            prev_entry = self.streams.get(camera_id)
            if prev_entry is not None and (prev_entry.get('pipeline') is not None or prev_entry.get('proc') is not None):
                logger.info("Stopping replaced stream resources for camera {cid} before rebuild", cid=camera_id)
                self._teardown_components(prev_entry, camera_id)
            self.streams[camera_id] = stream_data

            # --- Network Probing (Sliding Window) ---
            if not proc:
                probe_element = pipeline.get_by_name(f"net_probe_{camera_id}")
                if probe_element:
                    sink_pad = probe_element.get_static_pad("sink")
                    if sink_pad:
                        def network_probe_cb(pad, info):
                            buf = info.get_buffer()
                            if buf:
                                now = time.time()
                                with stream_data['lock']:
                                    stream_data['network_bytes_recent'].append((now, buf.get_size()))
                                    while stream_data['network_bytes_recent'] and (now - stream_data['network_bytes_recent'][0][0] > 10 or len(stream_data['network_bytes_recent']) > 1000):
                                        stream_data['network_bytes_recent'].pop(0)
                                del buf # Release C reference
                            return Gst.PadProbeReturn.OK
                        
                        stream_data['probe_id'] = sink_pad.add_probe(Gst.PadProbeType.BUFFER, network_probe_cb)

            def on_new_sample(sink):
                # Critical check for manager active state first
                if not self.manager_active or stream_data['shutdown_flag']:
                    return Gst.FlowReturn.OK

                sample = sink.emit("pull-sample")
                if not sample:
                    with stream_data['lock']:
                        stream_data['frames_dropped'] += 1
                    return Gst.FlowReturn.ERROR
                
                buf = sample.get_buffer()
                caps = sample.get_caps()
                
                # Extract width and height from caps
                struct = caps.get_structure(0)
                width = struct.get_value("width")
                height = struct.get_value("height")
                
                # Map the buffer to a numpy array
                success, info = buf.map(Gst.MapFlags.READ)
                if not success:
                    return Gst.FlowReturn.ERROR
                
                try:
                    # Final check before reshape/copy to avoid work during shutdown
                    if not self.manager_active or stream_data['shutdown_flag']:
                        return Gst.FlowReturn.OK

                    frame = np.frombuffer(info.data, dtype=np.uint8).reshape((height, width, 3)).copy()
                    now = time.time()
                    with stream_data['lock']:
                        stream_data['last_frame'] = frame
                        stream_data['frames_received'] += 1
                        stream_data['last_frame_time'] = now
                        stream_data['bytes_recent'].append((now, info.size))
                        # Prune older than 10s or if count exceeds 500
                        while stream_data['bytes_recent'] and (now - stream_data['bytes_recent'][0][0] > 10 or len(stream_data['bytes_recent']) > 500):
                            stream_data['bytes_recent'].pop(0)
                finally:
                    buf.unmap(info)
                    # Explicitly release GStreamer references to prevent C-level memory leaks
                    del buf
                    del sample
                
                return Gst.FlowReturn.OK

            stream_data['handler_id'] = appsink.connect("new-sample", on_new_sample)

            # Monitor the bus for errors/EOS
            bus = pipeline.get_bus()
            bus.add_signal_watch()
            
            def on_message(bus, message):
                t = message.type
                if t == Gst.MessageType.ERROR:
                    err, debug = message.parse_error()
                    err_msg = f"{err}"
                    with stream_data['lock']:
                        stream_data['errors'].append({"time": time.time(), "msg": err_msg})
                        if len(stream_data['errors']) > 5: stream_data['errors'].pop(0)

                    # Record cooldown
                    with self._lock:
                        self.cooldowns[camera_id] = time.time()

                    # Log both the error string and the debug string.
                    # The debug string is what GStreamer uses to name
                    # the missing plugin ("gstaom", "av1parse", etc.)
                    # — without it, "Your GStreamer installation is
                    # missing a plug-in" is unsolvable.
                    logger.warning(
                        "GStreamer Error (Cam {cid}): {err} | debug: {dbg}",
                        cid=camera_id, err=err, dbg=(debug or "(no debug)"),
                    )
                    threading.Thread(target=self.stop_stream, args=(camera_id,), daemon=True, name=f"StreamManager-StopStream-Cam{camera_id}").start()
                elif t == Gst.MessageType.EOS:
                    if source_type == "video":
                        logger.debug("Looping video for camera {cid}", cid=camera_id)
                        pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, 0)
                    else:
                        logger.warning("Stream End-of-Stream (Cam {cid})", cid=camera_id)
                        self.stop_stream(camera_id)
                elif t == Gst.MessageType.STATE_CHANGED:
                    old, new, pending = message.parse_state_changed()
                    if message.src == pipeline:
                        logger.debug("Pipeline state changed (Cam {cid}): {old} -> {new}", cid=camera_id, old=old.value_nick, new=new.value_nick)

            stream_data['bus_id'] = bus.connect("message", on_message)
            pipeline.set_state(Gst.State.PLAYING)

    def get_latest_frame(self, camera_id, since=None):
        if camera_id in self.streams:
            data = self.streams[camera_id]
            # Fast check
            if not data['active']: return None

            with data['lock']:
                if since is not None and data['last_frame_time'] <= since:
                    return None
                return data['last_frame']
        return None

    def get_diagnostics(self):
        """Returns detailed health metrics for all active streams."""
        results = {}
        with self._lock:
            for cid, data in self.streams.items():
                uptime = time.time() - data['start_time']
                
                # Get GStreamer state or pseudo-state
                state_name = "N/A"
                if data['active']:
                    if data.get('pipeline'):
                        _, state, _ = data['pipeline'].get_state(0)
                        state_name = Gst.Element.state_get_name(state)
                    elif data.get('youtube_mode') == 'thumbnail':
                        state_name = "POLLING"
                else:
                    # Inactive stream - check if it's in cooldown
                    last_err_time = self.cooldowns.get(cid, 0)
                    if time.time() - last_err_time < 15:
                        state_name = "COOLDOWN"
                    else:
                        state_name = "FAILED"
                
                # Performance calc (Live window)
                with data['lock']:
                    now = time.time()
                    # Prune just in case (same logic as callbacks for consistency)
                    while data['bytes_recent'] and (now - data['bytes_recent'][0][0] > 10 or len(data['bytes_recent']) > 500): 
                        data['bytes_recent'].pop(0)
                    while data['network_bytes_recent'] and (now - data['network_bytes_recent'][0][0] > 10 or len(data['network_bytes_recent']) > 1000): 
                        data['network_bytes_recent'].pop(0)
                    
                    raw_bits = sum(b for _, b in data['bytes_recent']) * 8
                    net_bits = sum(b for _, b in data['network_bytes_recent']) * 8
                    
                    # We use the actual time span of data in the window for precision
                    window_raw = (data['bytes_recent'][-1][0] - data['bytes_recent'][0][0]) if len(data['bytes_recent']) > 1 else 10
                    window_net = (data['network_bytes_recent'][-1][0] - data['network_bytes_recent'][0][0]) if len(data['network_bytes_recent']) > 1 else 10
                    
                    res_str = "N/A"
                    if data['last_frame'] is not None:
                        h, w = data['last_frame'].shape[:2]
                        res_str = f"{w}x{h}"
                    
                    results[cid] = {
                        "active": data['active'],
                        "source_type": data['source_type'],
                        "state": state_name,
                        "uptime_seconds": round(uptime),
                        "resolution": res_str,
                        "fps_target": data['fps'],
                        "fps_actual": round(data['frames_received'] / uptime, 1) if uptime > 0 else 0,
                        "bitrate_mbps": round(raw_bits / (max(1, window_raw) * 1000000), 2),
                        "network_bitrate_mbps": round(net_bits / (max(1, window_net) * 1000000), 3),
                        "total_received": data['frames_received'],
                        "total_dropped": data['frames_dropped'],
                        "last_frame_seconds_ago": round(time.time() - data['last_frame_time'], 1) if data['last_frame_time'] > 0 else -1,
                        "errors": data['errors']
                    }
        return results

    def _teardown_components(self, data, camera_id):
        """Release a stream entry's component resources (yt-dlp proc and
        GStreamer pipeline / signal handlers) WITHOUT taking ``self._lock``.

        ``stop_stream`` runs this after it has set the flags under the lock
        and released it; ``start_stream`` calls it on a leftover entry before
        replacing it (the audit's leak where an inactive entry was silently
        overwritten, leaking its pipeline and yt-dlp subprocess).
        """
        if not data:
            return
        logger.info("Stopping stream for camera {cid} and clearing internal resources", cid=camera_id)

        pipeline = data.get('pipeline')

        # 1. Kill yt-dlp subprocess if it exists
        if data.get('proc'):
            try:
                data['proc'].kill()
                data['proc'].wait(timeout=1.0)
            except Exception:
                pass

        # 2. Stop the pipeline
        if pipeline:
            pipeline.set_state(Gst.State.NULL)

        # 3. Disconnect signal handlers to release references
        try:
            if pipeline:
                appsink = pipeline.get_by_name(f"sink_{camera_id}")
                if appsink and data.get('handler_id'):
                    appsink.disconnect(data['handler_id'])

                bus = pipeline.get_bus()
                if bus:
                    if data.get('bus_id'):
                        bus.disconnect(data['bus_id'])
                    bus.remove_signal_watch()

                if not data.get('proc'): # Probes were added to net_probe element
                    probe_element = pipeline.get_by_name(f"net_probe_{camera_id}")
                    if probe_element and data.get('probe_id'):
                        sink_pad = probe_element.get_static_pad("sink")
                        if sink_pad:
                            sink_pad.remove_probe(data['probe_id'])
        except Exception as e:
            logger.debug("Minor error during GStreamer cleanup for cam {cid}: {e}", cid=camera_id, e=e)

        # 4. Explicitly release references to trigger C-level cleanup
        if 'pipeline' in data: del data['pipeline']
        if 'proc' in data: del data['proc']

    def stop_stream(self, camera_id, remove=False):
        if not self._gst_initialized:
            return
        
        # We use a localized lock check to prevent global deadlocks
        data = None
        with self._lock:
            if camera_id in self.streams:
                data = self.streams[camera_id]
                data['active'] = False
                data['shutdown_flag'] = True
        
        if data:
            logger.info("Stopping stream for camera {cid} and clearing internal resources", cid=camera_id)
            self._teardown_components(data, camera_id)

            # Final cleanup: ONLY remove from dictionary if 'remove' is True (parameter change)
            # Otherwise, keep it in self.streams so diagnostics can see the failure/error history.
            if remove:
                with self._lock:
                    if camera_id in self.streams:
                        del self.streams[camera_id]
            
            logger.debug("Resources for camera {cid} fully released.", cid=camera_id)

    def stop_all(self):
        logger.info("Stopping all streams and GLib loop...")
        self.manager_active = False
        
        # Take a snapshot of keys to avoid modification during iteration
        with self._lock:
            camera_ids = list(self.streams.keys())
        
        for cid in camera_ids:
            self.stop_stream(cid)
        
        if self._gst_initialized and self.loop.is_running():
            self.loop.quit()
        logger.info("All streaming resources released.")

stream_manager = StreamManager()


def _redact_url_credentials(url: str) -> str:
    """Replace any ``user:password@`` segment in a URL with ``***:***@``.

    H3 audit fix: the GStreamer pipeline log lines used to include
    the full RTSP URL (with embedded credentials) at DEBUG level.
    Anyone with log-file access could recover the camera password
    by grepping for ``rtspsrc location=``.  This helper strips the
    credentials from anything that looks like a URL inside a
    pipeline string before logging.

    The function is intentionally narrow: it only matches the
    ``user:password@`` segment that immediately follows the
    ``scheme://`` and only replaces that one segment.  Scheme-less
    strings, URLs without credentials, and other text pass through
    unchanged.
    """
    import re
    # Match "scheme://user:password@host" → "scheme://***:***@host"
    return re.sub(
        r"([a-zA-Z][a-zA-Z0-9+.\-]*://)([^:@\s/]+):([^@\s/]+)@",
        r"\1***:***@",
        url,
    )
