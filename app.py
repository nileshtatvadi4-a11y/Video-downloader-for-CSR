import os
import shutil
import subprocess

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import yt_dlp

app = FastAPI(
    title="Video Downloader for CSR API",
    version="1.1.0",
)


class VideoInfoRequest(BaseModel):
    url: str


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

    arguments = version_arguments.get(command, ["--version"])

    try:
        result = subprocess.run(
            [command] + arguments,
            capture_output=True,
            text=True,
            timeout=10,
        )

        output = (result.stdout or result.stderr).strip()

        if not output:
            return {
                "available": result.returncode == 0,
                "path": path,
                "version": None,
                "error": "The command returned no version information.",
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
            "error": "The command timed out while checking its version.",
        }

    except Exception as exc:
        return {
            "available": False,
            "path": path,
            "version": None,
            "error": str(exc),
        }


def get_video_info(url):
    ydl_options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": False,
    }

    with yt_dlp.YoutubeDL(ydl_options) as ydl:
        return ydl.extract_info(url, download=False)


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
        filesize = fmt.get("filesize") or fmt.get("filesize_approx")

        if vcodec and vcodec != "none" and height:
            key = (height, ext, acodec != "none")

            if key not in video_formats:
                video_formats[key] = {
                    "format_id": format_id,
                    "height": height,
                    "width": width,
                    "fps": fps,
                    "ext": ext,
                    "has_audio": acodec != "none",
                    "video_codec": vcodec,
                    "audio_codec": acodec,
                    "filesize": filesize,
                    "tbr": tbr,
                }

        elif acodec and acodec != "none":
            bitrate = abr or tbr

            if bitrate:
                key = (round(bitrate), ext)

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
        key=lambda item: item.get("bitrate_kbps") or 0,
        reverse=True,
    )

    return video_list, audio_list


@app.get("/")
def root():
    return {
        "name": "Video Downloader for CSR API",
        "status": "online",
    }


@app.get("/health")
def health():
    components = {
        "yt_dlp": check_command("yt-dlp"),
        "ffmpeg": check_command("ffmpeg"),
        "ffprobe": check_command("ffprobe"),
        "deno": check_command("deno"),
    }

    all_available = all(
        component["available"]
        for component in components.values()
    )

    return {
        "status": "ok" if all_available else "degraded",
        "components": components,
    }


@app.post("/info")
def info(request: VideoInfoRequest):
    url = request.url.strip()

    if not url:
        raise HTTPException(
            status_code=400,
            detail={
                "error_type": "InvalidURL",
                "stage": "URL_VALIDATION",
                "message": "The URL cannot be empty.",
            },
        )

    try:
        video_info = get_video_info(url)

        video_formats, audio_formats = build_format_list(video_info)

        return {
            "success": True,
            "title": video_info.get("title"),
            "uploader": video_info.get("uploader"),
            "channel": video_info.get("channel"),
            "duration": video_info.get("duration"),
            "duration_string": video_info.get("duration_string"),
            "thumbnail": video_info.get("thumbnail"),
            "webpage_url": video_info.get("webpage_url"),
            "extractor": video_info.get("extractor_key"),
            "video_formats": video_formats,
            "audio_formats": audio_formats,
        }

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


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "10000"))

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
    )
