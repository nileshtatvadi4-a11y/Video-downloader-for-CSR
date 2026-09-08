# CSR Video Downloader Server

Backend API for the CSR Video Downloader.

## Stack

- Python
- FastAPI
- yt-dlp
- yt-dlp-ejs
- FFmpeg
- FFprobe
- Deno
- Docker
- Render

## Health Check

GET /health

The health endpoint verifies:

- yt-dlp
- FFmpeg
- FFprobe
- Deno
