import os
import shutil
import subprocess

from fastapi import FastAPI

app = FastAPI(
    title="CSR Video Downloader API",
    version="1.0.0",
)


def check_command(command):
    path = shutil.which(command)

    if not path:
        return {
            "available": False,
            "path": None,
            "version": None,
        }

    try:
        result = subprocess.run(
            [command, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        output = (result.stdout or result.stderr).strip()
        first_line = output.splitlines()[0] if output else None

        return {
            "available": result.returncode == 0,
            "path": path,
            "version": first_line,
        }

    except Exception as exc:
        return {
            "available": False,
            "path": path,
            "version": None,
            "error": str(exc),
        }


@app.get("/")
def root():
    return {
        "name": "CSR Video Downloader API",
        "status": "online",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "components": {
            "yt_dlp": check_command("yt-dlp"),
            "ffmpeg": check_command("ffmpeg"),
            "ffprobe": check_command("ffprobe"),
            "deno": check_command("deno"),
        },
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "10000"))

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
    )
