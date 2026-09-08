import os
import shutil
import tempfile
import threading
import time
import uuid
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import yt_dlp


app = FastAPI(
    title="Video Downloader for CSR API",
    version="2.1.0",
)


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path("/tmp/csr_downloader")
BASE_DIR.mkdir(parents=True, exist_ok=True)

JOB_RETENTION_SECONDS = 60 * 60

jobs = {}
jobs_lock = threading.Lock()


# ============================================================
# REQUEST MODELS
# ============================================================

class VideoInfoRequest(BaseModel):
    url: str


class DownloadRequest(BaseModel):
    url: str
    mode: str
    format_id: str | None = None
    quality: str | None = None
    audio_codec: str | None = None
    audio_bitrate: int | None = None


# ============================================================
# COOKIE SUPPORT
# ============================================================

def get_cookie_value():
    preferred_names = {
        "cookies",
        "youtube_cookies",
        "youtube_cookie",
    }

    for key, value in os.environ.items():
        normalized = key.strip().lower()

        if normalized in preferred_names:
            if value and value.strip():
                return value.strip(), key

    return None, None


def prepare_cookie_file():
    cookie_value, source_name = get_cookie_value()

    if not cookie_value:
        return None, None

    cookie_value = cookie_value.replace("\\r\\n", "\n")
    cookie_value = cookie_value.replace("\\n", "\n")
    cookie_value = cookie_value.replace("\r\n", "\n")
    cookie_value = cookie_value.replace("\r", "\n")

    first_line = (
        cookie_value
        .lstrip()
        .split("\n", 1)[0]
        .strip()
    )

    if first_line not in (
        "# HTTP Cookie File",
        "# Netscape HTTP Cookie File",
    ):
        raise RuntimeError(
            "The Cookies environment variable is not "
            "a valid Netscape/Mozilla cookie file."
        )

    file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        suffix=".txt",
        delete=False,
    )

    try:
        file.write(cookie_value)
        file.flush()
        file.close()

        return file.name, source_name

    except Exception:
        try:
            file.close()
        except Exception:
            pass

        try:
            os.unlink(file.name)
        except Exception:
            pass

        raise


def delete_cookie_file(path):
    if not path:
        return

    try:
        os.unlink(path)
    except Exception:
        pass


# ============================================================
# COMMON HELPERS
# ============================================================

def validate_url(url):
    url = (url or "").strip()

    if not url:
        raise HTTPException(
            status_code=400,
            detail={
                "error_type": "InvalidURL",
                "stage": "URL_VALIDATION",
                "message": "The URL cannot be empty.",
            },
        )

    if not (
        url.startswith("http://")
        or url.startswith("https://")
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "error_type": "InvalidURL",
                "stage": "URL_VALIDATION",
                "message": (
                    "URL must begin with "
                    "http:// or https://."
                ),
            },
        )

    return url


def format_size(value):
    if value is None:
        return None

    try:
        value = float(value)
    except Exception:
        return None

    if value >= 1024 ** 3:
        return round(value / (1024 ** 3), 2)

    if value >= 1024 ** 2:
        return round(value / (1024 ** 2), 2)

    if value >= 1024:
        return round(value / 1024, 2)

    return round(value, 2)


def get_format_size(fmt):
    return (
        fmt.get("filesize")
        or fmt.get("filesize_approx")
    )


def safe_float(value):
    try:
        return float(value)
    except Exception:
        return 0.0


# ============================================================
# YT-DLP INFORMATION
# ============================================================

def extract_info(url):
    cookie_file = None

    try:
        cookie_file, cookie_source = (
            prepare_cookie_file()
        )

        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "extract_flat": False,
            "cookiefile": cookie_file,
            "retries": 3,
            "fragment_retries": 3,
        }

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                url,
                download=False,
            )

        return info, cookie_source

    finally:
        delete_cookie_file(cookie_file)


# ============================================================
# FORMAT BUILDING
# ============================================================

def build_formats(info):
    formats = info.get("formats") or []

    video = []
    audio = []

    seen_video = set()
    seen_audio = set()

    for fmt in formats:
        format_id = fmt.get("format_id")

        if not format_id:
            continue

        vcodec = fmt.get("vcodec")
        acodec = fmt.get("acodec")
        ext = fmt.get("ext")
        height = fmt.get("height")
        width = fmt.get("width")
        fps = fmt.get("fps")
        abr = fmt.get("abr")
        tbr = fmt.get("tbr")
        filesize = get_format_size(fmt)

        has_video = (
            vcodec
            and vcodec != "none"
        )

        has_audio = (
            acodec
            and acodec != "none"
        )

        # ----------------------------------------------------
        # VIDEO
        # ----------------------------------------------------

        if has_video and height:
            key = (
                int(height),
                ext,
                bool(has_audio),
                round(
                    safe_float(tbr),
                    1,
                ),
            )

            if key not in seen_video:
                seen_video.add(key)

                video.append({
                    "format_id": str(format_id),
                    "height": int(height),
                    "width": (
                        int(width)
                        if width
                        else None
                    ),
                    "fps": fps,
                    "ext": ext,
                    "has_audio": bool(has_audio),
                    "video_codec": vcodec,
                    "audio_codec": acodec,
                    "filesize": filesize,
                    "filesize_mb": format_size(
                        filesize
                    ),
                    "tbr": tbr,
                })

        # ----------------------------------------------------
        # AUDIO
        # ----------------------------------------------------

        elif has_audio and not has_video:
            bitrate = abr or tbr

            key = (
                str(format_id),
                ext,
                round(
                    safe_float(bitrate),
                    1,
                ),
            )

            if key not in seen_audio:
                seen_audio.add(key)

                audio.append({
                    "format_id": str(format_id),
                    "bitrate_kbps": (
                        round(
                            safe_float(bitrate),
                            1,
                        )
                        if bitrate
                        else None
                    ),
                    "ext": ext,
                    "audio_codec": acodec,
                    "filesize": filesize,
                    "filesize_mb": format_size(
                        filesize
                    ),
                })

    video.sort(
        key=lambda x: (
            x["height"] or 0,
            x["tbr"] or 0,
        ),
        reverse=True,
    )

    audio.sort(
        key=lambda x: (
            x["bitrate_kbps"] or 0
        ),
        reverse=True,
    )

    return video, audio


# ============================================================
# JOB MANAGEMENT
# ============================================================

def create_job():
    job_id = uuid.uuid4().hex

    job = {
        "id": job_id,

        "status": "queued",
        "stage": "QUEUED",

        "message": "Download job created.",

        "progress": 0,

        "downloaded_bytes": 0,
        "total_bytes": None,

        "speed": None,
        "eta": None,

        "filename": None,
        "file_path": None,

        "error_type": None,
        "error": None,
        "error_details": None,

        "worker_started": False,
        "worker_thread": None,

        "created_at": time.time(),
        "started_at": None,
        "completed_at": None,
        "updated_at": time.time(),
    }

    with jobs_lock:
        jobs[job_id] = job

    return job_id


def update_job(job_id, **values):
    with jobs_lock:
        job = jobs.get(job_id)

        if not job:
            return

        job.update(values)
        job["updated_at"] = time.time()


def get_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)

        if not job:
            return None

        return dict(job)


def cleanup_old_jobs():
    now = time.time()

    with jobs_lock:
        old_ids = []

        for job_id, job in jobs.items():
            if (
                now - job["updated_at"]
                > JOB_RETENTION_SECONDS
            ):
                old_ids.append(job_id)

        for job_id in old_ids:
            job = jobs.pop(
                job_id,
                None,
            )

            if not job:
                continue

            file_path = job.get(
                "file_path"
            )

            if file_path:
                try:
                    os.remove(file_path)
                except Exception:
                    pass

            job_dir = BASE_DIR / job_id

            try:
                shutil.rmtree(
                    job_dir,
                    ignore_errors=True,
                )
            except Exception:
                pass


# ============================================================
# FORMAT VALIDATION
# ============================================================

def find_format(info, format_id):
    formats = info.get("formats") or []

    for fmt in formats:
        if str(fmt.get("format_id")) == str(
            format_id
        ):
            return fmt

    return None


def choose_audio_for_video(info, video_fmt):
    formats = info.get("formats") or []

    video_ext = video_fmt.get("ext")

    candidates = []

    for fmt in formats:
        vcodec = fmt.get("vcodec")
        acodec = fmt.get("acodec")
        ext = fmt.get("ext")

        if (
            acodec
            and acodec != "none"
            and (
                not vcodec
                or vcodec == "none"
            )
        ):
            candidates.append(fmt)

    if not candidates:
        return None

    # MP4 video -> prefer M4A audio.
    if video_ext == "mp4":
        m4a = [
            f
            for f in candidates
            if f.get("ext") == "m4a"
        ]

        if m4a:
            candidates = m4a

    # WebM video -> prefer WebM audio.
    elif video_ext == "webm":
        webm = [
            f
            for f in candidates
            if f.get("ext") == "webm"
        ]

        if webm:
            candidates = webm

    candidates.sort(
        key=lambda x: (
            safe_float(
                x.get("abr")
                or x.get("tbr")
                or 0
            )
        ),
        reverse=True,
    )

    return candidates[0]


# ============================================================
# PROGRESS HOOK
# ============================================================

def make_progress_hook(job_id):

    def hook(data):
        try:
            status = data.get("status")

            if status == "downloading":
                downloaded = (
                    data.get("downloaded_bytes")
                    or 0
                )

                total = (
                    data.get("total_bytes")
                    or data.get(
                        "total_bytes_estimate"
                    )
                )

                if total:
                    percent = (
                        downloaded / total
                    ) * 100

                    percent = max(
                        0,
                        min(100, percent),
                    )
                else:
                    percent = None

                speed = data.get("speed")
                eta = data.get("eta")

                update_job(
                    job_id,

                    status="downloading",

                    stage="DOWNLOADING",

                    message=(
                        "Downloading media."
                    ),

                    progress=percent,

                    downloaded_bytes=downloaded,

                    total_bytes=total,

                    speed=speed,

                    eta=eta,
                )

            elif status == "finished":
                update_job(
                    job_id,

                    status="processing",

                    stage="POST_PROCESSING",

                    message=(
                        "Media download finished. "
                        "Processing output file."
                    ),

                    progress=100,
                )

        except Exception:
            # A progress-hook error must NEVER
            # terminate the actual yt-dlp download.
            pass

    return hook


# ============================================================
# DOWNLOAD WORKER
# ============================================================

def download_worker(
    job_id,
    request_data,
):
    cookie_file = None

    output_dir = (
        BASE_DIR / job_id
    )

    try:
        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        update_job(
            job_id,

            worker_started=True,

            worker_thread=(
                threading.current_thread().name
            ),

            started_at=time.time(),

            status="processing",

            stage="WORKER_STARTED",

            message=(
                "Download worker started."
            ),
        )

        url = request_data["url"]

        mode = request_data["mode"]

        requested_format_id = (
            request_data.get(
                "format_id"
            )
        )

        audio_codec = (
            request_data.get(
                "audio_codec"
            )
        )

        audio_bitrate = (
            request_data.get(
                "audio_bitrate"
            )
        )

        # ----------------------------------------------------
        # EXTRACT INFORMATION AGAIN
        # ----------------------------------------------------

        update_job(
            job_id,

            stage="EXTRACTING",

            message=(
                "Extracting media information "
                "again for format validation."
            ),
        )

        info, cookie_source = extract_info(
            url
        )

        if not info:
            raise RuntimeError(
                "yt-dlp returned no media information."
            )

        # ----------------------------------------------------
        # AUDIO
        # ----------------------------------------------------

        if mode == "audio":

            selected = find_format(
                info,
                requested_format_id,
            )

            if not selected:
                raise RuntimeError(
                    "The selected audio format "
                    "is no longer available."
                )

            selected_acodec = (
                selected.get("acodec")
            )

            if (
                not selected_acodec
                or selected_acodec == "none"
            ):
                raise RuntimeError(
                    "The selected format is "
                    "not an audio-only format."
                )

            update_job(
                job_id,

                stage="FORMAT_VALIDATION",

                message=(
                    "Audio format validated: "
                    + str(
                        selected.get(
                            "format_id"
                        )
                    )
                ),
            )

            cookie_file, _ = (
                prepare_cookie_file()
            )

            output_template = (
                str(output_dir)
                + "/%(title).180B.%(ext)s"
            )

            options = {
                "quiet": True,

                "no_warnings": True,

                "noplaylist": True,

                "cookiefile": cookie_file,

                "format": str(
                    selected["format_id"]
                ),

                "outtmpl": output_template,

                "progress_hooks": [
                    make_progress_hook(
                        job_id
                    )
                ],

                "retries": 5,

                "fragment_retries": 5,

                "continuedl": True,

                "overwrites": False,

                "concurrent_fragment_downloads": 4,

                "postprocessor_args": [],
            }

            # ------------------------------------------------
            # MP3
            # ------------------------------------------------

            if audio_codec == "mp3":

                bitrate = (
                    int(audio_bitrate)
                    if audio_bitrate
                    else 192
                )

                allowed_bitrates = {
                    320,
                    256,
                    192,
                    128,
                    96,
                }

                if bitrate not in allowed_bitrates:
                    raise RuntimeError(
                        "Unsupported MP3 bitrate. "
                        "Allowed values are "
                        "320, 256, 192, 128 and 96 kbps."
                    )

                options[
                    "postprocessors"
                ] = [
                    {
                        "key":
                        "FFmpegExtractAudio",

                        "preferredcodec":
                        "mp3",

                        "preferredquality":
                        str(bitrate),
                    }
                ]

            # ------------------------------------------------
            # M4A
            # ------------------------------------------------

            elif audio_codec == "m4a":

                options[
                    "postprocessors"
                ] = [
                    {
                        "key":
                        "FFmpegExtractAudio",

                        "preferredcodec":
                        "m4a",

                        "preferredquality":
                        "0",
                    }
                ]

            # ------------------------------------------------
            # OPUS
            # ------------------------------------------------

            elif audio_codec == "opus":

                options[
                    "postprocessors"
                ] = [
                    {
                        "key":
                        "FFmpegExtractAudio",

                        "preferredcodec":
                        "opus",

                        "preferredquality":
                        "0",
                    }
                ]

            else:
                raise RuntimeError(
                    "Invalid audio codec. "
                    "Supported values are "
                    "mp3, m4a and opus."
                )

            update_job(
                job_id,

                stage="DOWNLOADING",

                message=(
                    "Starting audio download."
                ),
            )

            with yt_dlp.YoutubeDL(
                options
            ) as ydl:

                result = ydl.download(
                    [url]
                )

                if result not in (
                    None,
                    0,
                ):
                    raise RuntimeError(
                        "yt-dlp returned a non-zero "
                        "download result: "
                        + str(result)
                    )

        # ----------------------------------------------------
        # VIDEO
        # ----------------------------------------------------

        elif mode == "video":

            selected = find_format(
                info,
                requested_format_id,
            )

            if not selected:
                raise RuntimeError(
                    "The selected video format "
                    "is no longer available."
                )

            vcodec = selected.get(
                "vcodec"
            )

            if (
                not vcodec
                or vcodec == "none"
            ):
                raise RuntimeError(
                    "The selected format is "
                    "not a video format."
                )

            update_job(
                job_id,

                stage="FORMAT_VALIDATION",

                message=(
                    "Video format validated: "
                    + str(
                        selected.get(
                            "format_id"
                        )
                    )
                ),
            )

            # ------------------------------------------------
            # SELECT AUDIO WHEN VIDEO IS VIDEO-ONLY
            # ------------------------------------------------

            if (
                not selected.get("acodec")
                or selected.get("acodec")
                == "none"
            ):

                audio = choose_audio_for_video(
                    info,
                    selected,
                )

                if not audio:
                    raise RuntimeError(
                        "No compatible audio "
                        "format was found."
                    )

                final_format = (
                    str(
                        selected["format_id"]
                    )
                    + "+"
                    + str(
                        audio["format_id"]
                    )
                )

                update_job(
                    job_id,

                    stage="FORMAT_VALIDATION",

                    message=(
                        "Video-only format selected. "
                        "Compatible audio format "
                        + str(
                            audio["format_id"]
                        )
                        + " will be merged."
                    ),
                )

            else:
                final_format = str(
                    selected["format_id"]
                )

            cookie_file, _ = (
                prepare_cookie_file()
            )

            output_template = (
                str(output_dir)
                + "/%(title).180B.%(ext)s"
            )

            options = {
                "quiet": True,

                "no_warnings": True,

                "noplaylist": True,

                "cookiefile": cookie_file,

                "format": final_format,

                "outtmpl": output_template,

                "progress_hooks": [
                    make_progress_hook(
                        job_id
                    )
                ],

                "retries": 5,

                "fragment_retries": 5,

                "continuedl": True,

                "overwrites": False,

                "concurrent_fragment_downloads": 4,

                "merge_output_format": "mp4",
            }

            update_job(
                job_id,

                stage="DOWNLOADING",

                message=(
                    "Starting video download."
                ),
            )

            with yt_dlp.YoutubeDL(
                options
            ) as ydl:

                result = ydl.download(
                    [url]
                )

                if result not in (
                    None,
                    0,
                ):
                    raise RuntimeError(
                        "yt-dlp returned a non-zero "
                        "download result: "
                        + str(result)
                    )

        else:
            raise RuntimeError(
                "Invalid download mode: "
                + str(mode)
            )

        # ----------------------------------------------------
        # FINALIZING
        # ----------------------------------------------------

        update_job(
            job_id,

            status="processing",

            stage="FINALIZING",

            message=(
                "Download finished. "
                "Locating the final output file."
            ),

            progress=100,
        )

        # Give FFmpeg/postprocessors a tiny moment
        # to finish filesystem operations.
        time.sleep(0.25)

        files = []

        if output_dir.exists():

            for p in output_dir.iterdir():

                if not p.is_file():
                    continue

                if p.name.endswith(".part"):
                    continue

                if p.name.endswith(".ytdl"):
                    continue

                files.append(p)

        if not files:
            raise RuntimeError(
                "yt-dlp reported completion, "
                "but no final output file was found "
                "inside the job output directory."
            )

        files.sort(
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        final_file = files[0]

        if final_file.stat().st_size <= 0:
            raise RuntimeError(
                "The output file was created, "
                "but its size is zero bytes."
            )

        # ----------------------------------------------------
        # SUCCESS
        # ----------------------------------------------------

        update_job(
            job_id,

            status="completed",

            stage="COMPLETED",

            message=(
                "Download completed successfully."
            ),

            progress=100,

            downloaded_bytes=(
                final_file.stat().st_size
            ),

            total_bytes=(
                final_file.stat().st_size
            ),

            filename=final_file.name,

            file_path=str(final_file),

            completed_at=time.time(),

            error_type=None,

            error=None,

            error_details=None,
        )

        # IMPORTANT:
        #
        # DO NOT DELETE output_dir here.
        #
        # /download/file/{job_id} still needs
        # the completed file.
        #

    except yt_dlp.utils.DownloadError as exc:

        error_text = str(exc).strip()

        if not error_text:
            error_text = (
                "yt-dlp returned an unknown "
                "download error."
            )

        update_job(
            job_id,

            status="error",

            stage="YTDLP_ERROR",

            message=(
                "yt-dlp reported an error."
            ),

            error_type="YTDLPError",

            error=error_text,

            error_details=(
                traceback.format_exc()
            ),
        )

    except Exception as exc:

        error_text = str(exc).strip()

        if not error_text:
            error_text = (
                "The download worker failed "
                "without an error message."
            )

        update_job(
            job_id,

            status="error",

            stage="DOWNLOAD_ERROR",

            message=(
                "Download processing failed."
            ),

            error_type=type(exc).__name__,

            error=error_text,

            error_details=(
                traceback.format_exc()
            ),
        )

    finally:

        delete_cookie_file(
            cookie_file
        )

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # Do NOT remove output_dir here.
        #
        # A completed job needs the directory and
        # final file for /download/file/{job_id}.
        #
        # Only incomplete/error jobs are cleaned up.
        # ----------------------------------------------------

        final_job = get_job(job_id)

        if final_job:
            if final_job.get("status") == "error":

                try:
                    shutil.rmtree(
                        output_dir,
                        ignore_errors=True,
                    )
                except Exception:
                    pass


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():
    return {
        "name": "Video Downloader for CSR API",
        "status": "online",
        "version": "2.1.0",
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    def command_version(command):

        path = shutil.which(command)

        if not path:
            return {
                "available": False,
                "path": None,
            }

        try:
            result = __import__(
                "subprocess"
            ).run(
                [command, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            output = (
                result.stdout
                or result.stderr
            ).strip()

            return {
                "available": (
                    result.returncode == 0
                ),

                "path": path,

                "version": (
                    output.splitlines()[0]
                    if output
                    else None
                ),
            }

        except Exception as exc:

            return {
                "available": False,
                "path": path,
                "error": str(exc),
            }

    cookie_value, cookie_name = (
        get_cookie_value()
    )

    return {
        "status": "ok",

        "components": {
            "yt_dlp": command_version(
                "yt-dlp"
            ),

            "ffmpeg": command_version(
                "ffmpeg"
            ),

            "ffprobe": command_version(
                "ffprobe"
            ),

            "deno": command_version(
                "deno"
            ),
        },

        "youtube_cookies": {
            "configured": bool(
                cookie_value
            ),

            "key_detected": cookie_name,
        },

        "jobs": {
            "active": count_active_jobs(),
        },
    }


def count_active_jobs():

    active_states = {
        "queued",
        "processing",
        "downloading",
    }

    with jobs_lock:

        return sum(
            1
            for job in jobs.values()
            if job.get("status")
            in active_states
        )


# ============================================================
# INFO
# ============================================================

@app.post("/info")
def info(request: VideoInfoRequest):

    url = validate_url(
        request.url
    )

    try:

        video_info, cookie_source = (
            extract_info(url)
        )

        video_formats, audio_formats = (
            build_formats(video_info)
        )

        return {
            "success": True,

            "title": video_info.get(
                "title"
            ),

            "uploader": video_info.get(
                "uploader"
            ),

            "channel": video_info.get(
                "channel"
            ),

            "duration": video_info.get(
                "duration"
            ),

            "duration_string": video_info.get(
                "duration_string"
            ),

            "thumbnail": video_info.get(
                "thumbnail"
            ),

            "webpage_url": video_info.get(
                "webpage_url"
            ),

            "extractor": video_info.get(
                "extractor_key"
            ),

            "video_formats": video_formats,

            "audio_formats": audio_formats,

            "diagnostics": {
                "youtube_cookies_configured": (
                    cookie_source is not None
                ),
            },
        }

    except RuntimeError as exc:

        raise HTTPException(
            status_code=500,

            detail={
                "error_type":
                    "CookieConfigurationError",

                "stage":
                    "COOKIE_CONFIGURATION",

                "message":
                    str(exc),
            },
        )

    except yt_dlp.utils.DownloadError as exc:

        raise HTTPException(
            status_code=422,

            detail={
                "error_type":
                    "YTDLPError",

                "stage":
                    "VIDEO_INFORMATION",

                "message":
                    str(exc),
            },
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,

            detail={
                "error_type":
                    type(exc).__name__,

                "stage":
                    "VIDEO_INFORMATION",

                "message":
                    str(exc),
            },
        )


# ============================================================
# START DOWNLOAD JOB
# ============================================================

@app.post("/download")
def start_download(
    request: DownloadRequest
):

    url = validate_url(
        request.url
    )

    if request.mode not in (
        "video",
        "audio",
    ):

        raise HTTPException(
            status_code=400,

            detail={
                "error_type":
                    "InvalidMode",

                "stage":
                    "REQUEST_VALIDATION",

                "message": (
                    "mode must be "
                    "'video' or 'audio'."
                ),
            },
        )

    if not request.format_id:

        raise HTTPException(
            status_code=400,

            detail={
                "error_type":
                    "MissingFormat",

                "stage":
                    "REQUEST_VALIDATION",

                "message":
                    "format_id is required.",
            },
        )

    cleanup_old_jobs()

    job_id = create_job()

    request_data = {
        "url": url,

        "mode": request.mode,

        "format_id": request.format_id,

        "quality": request.quality,

        "audio_codec": request.audio_codec,

        "audio_bitrate": request.audio_bitrate,
    }

    # Mark this BEFORE creating the thread so
    # the client immediately knows the worker is
    # being launched.

    update_job(
        job_id,

        status="processing",

        stage="STARTING_WORKER",

        message=(
            "Starting download worker."
        ),
    )

    try:

        thread = threading.Thread(
            target=download_worker,

            args=(
                job_id,
                request_data,
            ),

            daemon=True,

            name=(
                "csr-download-"
                + job_id[:12]
            ),
        )

        thread.start()

        update_job(
            job_id,

            message=(
                "Download worker started. "
                "Waiting for media processing."
            ),
        )

    except Exception as exc:

        update_job(
            job_id,

            status="error",

            stage="WORKER_START_ERROR",

            message=(
                "The server could not start "
                "the download worker."
            ),

            error_type=type(exc).__name__,

            error=str(exc),

            error_details=(
                traceback.format_exc()
            ),
        )

        raise HTTPException(
            status_code=500,

            detail={
                "error_type":
                    "WorkerStartError",

                "stage":
                    "WORKER_START_ERROR",

                "message":
                    str(exc),
            },
        )

    return {
        "success": True,

        "job_id": job_id,

        "status": "processing",

        "stage": "STARTING_WORKER",

        "message": (
            "Download worker started."
        ),
    }


# ============================================================
# JOB STATUS
# ============================================================

@app.get("/download/status/{job_id}")
def download_status(
    job_id: str
):

    job = get_job(
        job_id
    )

    if not job:

        raise HTTPException(
            status_code=404,

            detail={
                "error_type":
                    "JobNotFound",

                "stage":
                    "JOB_STATUS",

                "message":
                    "Download job was not found.",
            },
        )

    return {
        "success": True,

        "job": job,
    }


# ============================================================
# DOWNLOAD FILE
# ============================================================

@app.get("/download/file/{job_id}")
def download_file(
    job_id: str
):

    job = get_job(
        job_id
    )

    if not job:

        raise HTTPException(
            status_code=404,

            detail={
                "error_type":
                    "JobNotFound",

                "stage":
                    "FILE_DOWNLOAD",

                "message":
                    "Download job was not found.",
            },
        )

    if job["status"] != "completed":

        raise HTTPException(
            status_code=409,

            detail={
                "error_type":
                    "DownloadNotReady",

                "stage":
                    "FILE_DOWNLOAD",

                "message":
                    "The download is not completed yet.",
            },
        )

    path = job.get(
        "file_path"
    )

    if (
        not path
        or not os.path.isfile(path)
    ):

        raise HTTPException(
            status_code=404,

            detail={
                "error_type":
                    "FileMissing",

                "stage":
                    "FILE_DOWNLOAD",

                "message": (
                    "The completed file is "
                    "no longer available on "
                    "the server."
                ),
            },
        )

    return FileResponse(
        path=path,

        filename=job.get(
            "filename",
            "download",
        ),

        media_type=(
            "application/octet-stream"
        ),
    )


# ============================================================
# SERVER
# ============================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.environ.get(
            "PORT",
            "10000",
        )
    )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
    )
