import os
import shutil
import subprocess
import tempfile

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import yt_dlp


app = FastAPI(
    title="Video Downloader for CSR API",
    version="1.2.0",
)


class VideoInfoRequest(BaseModel):
    url: str


# --------------------------------------------------
# COOKIE SUPPORT
# --------------------------------------------------

COOKIE_ENV_NAMES = [
    "Cookies",
    "YOUTUBE_COOKIE",
    "COOKIES",
]


def get_cookie_value():
    for name in COOKIE_ENV_NAMES:
        value = os.environ.get(name)

        if value and value.strip():
            return value.strip(), name

    return None, None


def prepare_cookie_file():
    """
    Creates a temporary Netscape-format cookie file
    from a Render environment variable.

    Returns:
        (path, source_name)
    """

    cookie_value, source_name = get_cookie_value()

    if not cookie_value:
        return None, None

    # Render environment variables may contain escaped
    # newlines depending on how they were entered.
    cookie_value = cookie_value.replace("\\r\\n", "\n")
    cookie_value = cookie_value.replace("\\n", "\n")
    cookie_value = cookie_value.replace("\r\n", "\n")
    cookie_value = cookie_value.replace("\r", "\n")

    # Validate that this at least looks like a Netscape
    # cookie file.
    first_line = cookie_value.lstrip().split("\n", 1)[0].strip()

    if first_line not in (
        "# HTTP Cookie File",
        "# Netscape HTTP Cookie File",
    ):
        raise RuntimeError(
            "The configured cookie environment variable does not "
            "contain a valid Netscape/Mozilla cookie file. "
            "The first line must be '# HTTP Cookie File' or "
            "'# Netscape HTTP Cookie File'."
        )

    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        suffix=".txt",
        delete=False,
    )

    try:
        temp_file.write(cookie_value)
        temp_file.flush()
        temp_file.close()

        return temp_file.name, source_name

    except Exception:
        try:
            temp_file.close()
        except Exception:
            pass

        try:
            os.unlink(temp_file.name)
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


# --------------------------------------------------
# COMMAND CHECK
# --------------------------------------------------

def check_command(command):
    path = shutil.which(command)

    if not path:
        return {
            "available": False,
            "path": None,
            "version": None,
            "error": f"{command} was not found.",
        }

    version_arguments = {
        "yt-dlp": ["--version"],
        "ffmpeg": ["-version"],
        "ffprobe": ["-version"],
        "deno": ["--version"],
    }

    arguments = version_arguments.get(
        command,
        ["--version"],
    )

    try:
        result = subprocess.run(
            [command] + arguments,
            capture_output=True,
            text=True,
            timeout=10,
        )

        output = (
            result.stdout
            or result.stderr
        ).strip()

        if not output:
            return {
                "available": result.returncode == 0,
                "path": path,
                "version": None,
                "error": (
                    "The command returned no "
                    "version information."
                ),
            }

        first_line = output.splitlines()[0].strip()

        return {
            "available": result.returncode == 0,
            "path": path,
            "version": first_line,
        }

    except subprocess.TimeoutExpired:
        return {
            "available": False,
            "path": path,
            "version": None,
            "error": (
                "The command timed out while "
                "checking its version."
            ),
        }

    except Exception as exc:
        return {
            "available": False,
            "path": path,
            "version": None,
            "error": str(exc),
        }


# --------------------------------------------------
# VIDEO INFORMATION
# --------------------------------------------------

def get_video_info(url):
    cookie_file = None

    try:
        cookie_file, cookie_source = prepare_cookie_file()

        ydl_options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "extract_flat": False,

            # Make yt-dlp use the temporary cookie file.
            "cookiefile": cookie_file,

            # Keep extraction reasonably robust.
            "retries": 3,
            "fragment_retries": 3,

            # Don't accidentally use a playlist.
            "noplaylist": True,
        }

        with yt_dlp.YoutubeDL(ydl_options) as ydl:
            info = ydl.extract_info(
                url,
                download=False,
            )

        return info, cookie_source

    finally:
        delete_cookie_file(cookie_file)


# --------------------------------------------------
# FORMAT LIST
# --------------------------------------------------

def build_format_list(info):
    formats = info.get("formats") or []

    video_formats = {}
    audio_formats = {}

    for fmt in formats:
        format_id = fmt.get("format_id")

        if not format_id:
            continue

        height = fmt.get("height")
        width = fmt.get("width")
        fps = fmt.get("fps")
        ext = fmt.get("ext")
        acodec = fmt.get("acodec")
        vcodec = fmt.get("vcodec")
        abr = fmt.get("abr")
        tbr = fmt.get("tbr")
        filesize = (
            fmt.get("filesize")
            or fmt.get("filesize_approx")
        )

        # VIDEO
        if (
            vcodec
            and vcodec != "none"
            and height
        ):
            key = (
                height,
                ext,
                acodec != "none",
            )

            if key not in video_formats:
                video_formats[key] = {
                    "format_id": format_id,
                    "height": height,
                    "width": width,
                    "fps": fps,
                    "ext": ext,
                    "has_audio": (
                        acodec != "none"
                    ),
                    "video_codec": vcodec,
                    "audio_codec": acodec,
                    "filesize": filesize,
                    "tbr": tbr,
                }

        # AUDIO
        elif (
            acodec
            and acodec != "none"
        ):
            bitrate = abr or tbr

            if bitrate:
                key = (
                    round(bitrate),
                    ext,
                )

                if key not in audio_formats:
                    audio_formats[key] = {
                        "format_id": format_id,
                        "bitrate_kbps": round(bitrate),
                        "ext": ext,
                        "audio_codec": acodec,
                        "filesize": filesize,
                    }

    video_list = sorted(
        video_formats.values(),
        key=lambda item: (
            item.get("height") or 0,
            item.get("tbr") or 0,
        ),
        reverse=True,
    )

    audio_list = sorted(
        audio_formats.values(),
        key=lambda item:
            item.get("bitrate_kbps") or 0,
        reverse=True,
    )

    return video_list, audio_list


# --------------------------------------------------
# ROOT
# --------------------------------------------------

@app.get("/")
def root():
    return {
        "name": "Video Downloader for CSR API",
        "status": "online",
        "version": "1.2.0",
    }


# --------------------------------------------------
# HEALTH
# --------------------------------------------------

@app.get("/health")
def health():
    components = {
        "yt_dlp": check_command("yt-dlp"),
        "ffmpeg": check_command("ffmpeg"),
        "ffprobe": check_command("ffprobe"),
        "deno": check_command("deno"),
    }

    cookie_value, cookie_source = get_cookie_value()

    cookie_status = {
        "configured": bool(cookie_value),
        "source": cookie_source,
    }

    all_available = all(
        component["available"]
        for component in components.values()
    )

    return {
        "status": (
            "ok"
            if all_available
            else "degraded"
        ),
        "components": components,
        "youtube_cookies": cookie_status,
    }


# --------------------------------------------------
# INFO
# --------------------------------------------------

@app.post("/info")
def info(request: VideoInfoRequest):
    url = request.url.strip()

    if not url:
        raise HTTPException(
            status_code=400,
            detail={
                "error_type": "InvalidURL",
                "stage": "URL_VALIDATION",
                "message": (
                    "The URL cannot be empty."
                ),
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

    try:
        video_info, cookie_source = get_video_info(
            url
        )

        video_formats, audio_formats = (
            build_format_list(video_info)
        )

        return {
            "success": True,

            "title": video_info.get("title"),

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
                "error_type": "CookieConfigurationError",
                "stage": "COOKIE_CONFIGURATION",
                "message": str(exc),
            },
        )

    except yt_dlp.utils.DownloadError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error_type": "YTDLPError",
                "stage": "VIDEO_INFORMATION",
                "message": str(exc),
            },
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": "ServerError",
                "stage": "VIDEO_INFORMATION",
                "message": str(exc),
            },
        )


# --------------------------------------------------
# START SERVER
# --------------------------------------------------

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
