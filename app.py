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


@app.get("/")
def root():
    return {
        "name": "CSR Video Downloader API",
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


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "10000"))

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
    )
