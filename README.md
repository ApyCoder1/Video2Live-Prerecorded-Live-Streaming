
# Video2Live – Prerecorded Live Streaming Platform

A Django-based live streaming platform that broadcasts prerecorded videos to YouTube Live using FFmpeg, Celery, and Redis. Upload videos, organize them into playlists, schedule streaming sessions, and manage your live broadcasts from a single dashboard.

## Features

- Video upload with thumbnails
- Automatic video processing using Celery
- Video metadata extraction using FFprobe
- Playlist creation and management
- Reorder videos within playlists
- Create and manage streaming sessions
- Broadcast prerecorded videos to YouTube Live using RTMP
- Start, stop, and skip streaming sessions
- Schedule live streaming sessions
- Manage YouTube streaming configurations
- Background task processing using Celery
- Redis message broker integration
- Django authentication and user-specific video management

## Technology Stack

- **Backend:** Python, Django 5.2
- **Background Tasks:** Celery 5.6
- **Message Broker:** Redis
- **Video Processing:** FFmpeg and FFprobe
- **REST API:** Django REST Framework
- **Task Scheduling:** django-celery-beat
- **Database:** Django-supported database configured in `config/settings.py`

## Project Structure

```text
Video2Live-Prerecorded-Live-Streaming/
│
├── config/
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py
│   ├── wsgi.py
│   └── celery.py
│
├── streaming/
│   ├── models.py
│   ├── views.py
│   ├── tasks.py
│   ├── urls.py
│   ├── admin.py
│   └── templates/
│
├── media/
│   ├── videos/
│   └── thumbnails/
│
├── manage.py
├── requirements.txt
└── README.md
```

*Note: The structure above is an example. Adjust it to match the actual files and folders in your repository.*

## Requirements

Before installing the project, ensure you have:

- Python 3.11 or 3.12
- Git
- Redis
- FFmpeg and FFprobe
- A YouTube channel with live streaming enabled

## Installation on Windows

### 1. Clone the Repository

Open Command Prompt or PowerShell:

```bash
git clone https://github.com/ApyCoder1/Video2Live-Prerecorded-Live-Streaming.git
cd Video2Live-Prerecorded-Live-Streaming
```

### 2. Create a Virtual Environment

```bash
py -3.12 -m venv venv
venv\Scripts\activate
```

Upgrade pip:

```bash
python -m pip install --upgrade pip
```

### 3. Install Python Dependencies

```bash
pip install -r requirements.txt
```

### 4. Install FFmpeg

Download an FFmpeg Windows build and add its `bin` directory to your system PATH.

Verify the installation:

```bash
ffmpeg -version
ffprobe -version
```

Both commands should display version information.

### 5. Configure Redis

Install Redis using a supported Windows-compatible distribution, Docker, or WSL.

Make sure Redis is running and accessible at:

```text
127.0.0.1:6379
```

Verify the connection:

```bash
redis-cli ping
```

Expected response:

```text
PONG
```

### 6. Configure Django and Celery

Open `config/settings.py` and verify your database, media settings, and Celery configuration.

Example Redis configuration:

```python
CELERY_BROKER_URL = "redis://127.0.0.1:6379/0"
CELERY_RESULT_BACKEND = "redis://127.0.0.1:6379/1"

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
```

Ensure your Celery application uses the Django settings module:

```python
DJANGO_SETTINGS_MODULE = "config.settings"
```

Keep your Django secret key and YouTube stream keys private. Do not commit credentials to GitHub.

### 7. Run Database Migrations

```bash
python manage.py check
python manage.py migrate
python manage.py createsuperuser
```

### 8. Start Redis

Start the Redis service using the installation method you selected.

Confirm that Redis is running before starting Celery.

### 9. Start the Celery Worker

Open a new terminal in the project directory:

```bash
venv\Scripts\activate
celery -A config worker --loglevel=info --pool=solo
```

The `--pool=solo` option is useful for local Celery development on Windows.

### 10. Start Celery Beat

If scheduled streaming tasks are configured, open another terminal:

```bash
venv\Scripts\activate
celery -A config beat --loglevel=info
```

### 11. Start the Django Server

Open another terminal:

```bash
venv\Scripts\activate
python manage.py runserver
```

Open the application:

http://127.0.0.1:8000/

## Installation on Ubuntu VPS

### 1. Install System Dependencies

```bash
sudo apt update

sudo apt install -y \
    python3 \
    python3-venv \
    python3-pip \
    git \
    ffmpeg \
    redis-server
```

Start Redis:

```bash
sudo systemctl enable --now redis-server
redis-cli ping
```

### 2. Clone the Repository

```bash
git clone https://github.com/ApyCoder1/Video2Live-Prerecorded-Live-Streaming.git

cd Video2Live-Prerecorded-Live-Streaming
```

### 3. Create the Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Apply Migrations

```bash
python manage.py check
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

Run `collectstatic` if your Django static-file settings are configured.

### 5. Start the Celery Worker

```bash
celery -A config worker --loglevel=info
```

### 6. Start Celery Beat

Only required when scheduled tasks are enabled:

```bash
celery -A config beat --loglevel=info
```

### 7. Start Django for Testing

```bash
python manage.py runserver 0.0.0.0:8000
```

For production, use a production application server such as Gunicorn behind Nginx, configure HTTPS, and run Django and Celery as managed services. Do not use Django's development server as your production web server.

## How to Use

### Step 1: Upload Videos

Sign in to the platform and upload your prerecorded videos. Add a title, description, and optional thumbnail.

Wait for background processing to finish before using a video in a playlist.

### Step 2: Create a Playlist

Create a playlist and select the processed videos you want to broadcast.

Arrange the videos in the required playback order.

### Step 3: Create a Streaming Session

Select your playlist and configure the streaming session.

Provide the YouTube RTMP ingest URL and stream key, or select an existing configured stream if supported by your setup.

### Step 4: Start Live Streaming

Start the streaming session from the dashboard.

Celery executes the background streaming task, and FFmpeg sends the prerecorded video to YouTube Live.

### Step 5: Manage the Broadcast

Use the available controls to stop the stream or skip the current video.

Check the session status and application logs if the stream does not start successfully.

## YouTube Live Configuration

The default RTMP ingest URL used by the supplied views is:

```text
rtmp://a.rtmp.youtube.com/live2
```

Check YouTube Studio for the correct ingest URL and protocol for your broadcast.

Configure the RTMP URL and stream key in your streaming session. Never publish a real stream key in your source code, README, screenshots, or public logs.

## Common Troubleshooting

### Celery Tasks Are Not Running

- Verify that Redis is running.
- Confirm the broker URL in `config/settings.py`.
- Start the worker with `celery -A config worker --loglevel=info`.
- Check the worker logs for import errors or task failures.

### FFmpeg or FFprobe Is Not Found

Install FFmpeg and ensure both executables are available through the system PATH.

```bash
ffmpeg -version
ffprobe -version
```

### Video Processing Fails

- Check the Celery worker logs.
- Verify the uploaded video file exists.
- Check media directory permissions.
- Ensure sufficient disk space is available.
- Confirm FFmpeg can read the uploaded file.

### YouTube Stream Does Not Start

- Verify the stream key and RTMP ingest URL.
- Confirm live streaming is enabled in YouTube Studio.
- Ensure the playlist contains valid, processed videos.
- Check FFmpeg and Celery logs.
- Verify that the VPS permits outbound connections to the streaming endpoint.

## Security Recommendations

- Set `DEBUG = False` in production.
- Use a strong Django `SECRET_KEY`.
- Configure `ALLOWED_HOSTS`.
- Enable HTTPS.
- Keep stream keys and credentials out of Git.
- Configure secure media storage and backups.
- Monitor disk usage and background worker logs.
- Run the application using a dedicated, non-root service account.

## Future Improvements

- Multi-platform streaming support
- Stream health monitoring
- Detailed streaming logs
- Automatic stream recovery
- Advanced scheduling and recurring playlists
- Multi-user stream management
- Stream analytics and reporting

## License

Add an appropriate license file before distributing this project.

## Author

**ApyCoder**

GitHub: https://github.com/ApyCoder1

Repository: https://github.com/ApyCoder1/Video2Live-Prerecorded-Live-Streaming
