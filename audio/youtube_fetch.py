import importlib.util
import subprocess
import sys
import os

MUSIC_DIR = "audiofiles"

VENV_DIR = ".venv"
PYTHON_EXEC = os.path.join(VENV_DIR, "Scripts" if os.name == "nt" else "bin", "python")
PIP_EXEC = os.path.join(VENV_DIR, "Scripts" if os.name == "nt" else "bin", "pip")

DEPENDENCIES = ["yt-dlp", "imageio[ffmpeg]"]


def is_installed(package):
    """Check if a package is installed inside the virtual environment."""
    try:
        result = subprocess.run(
            [PIP_EXEC, "show", package.split("[")[0]],  # Extract package name if using extras
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return result.returncode == 0
    except Exception:
        return False


def setup_venv():
    """Sets up a virtual environment and installs dependencies if needed."""
    if not os.path.exists(VENV_DIR):
        print("Creating virtual environment...")
        try:
            subprocess.run([sys.executable, "-m", "venv", VENV_DIR], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error creating venv: {e}")
            sys.exit(1)

    # Ensure pip is installed and updated
    try:
        subprocess.run([PYTHON_EXEC, "-m", "ensurepip", "--default-pip"], check=True)
        subprocess.run([PYTHON_EXEC, "-m", "pip", "install", "--upgrade", "pip"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error installing/upgrading pip: {e}")
        sys.exit(1)

    # Install dependencies if missing
    missing_dependencies = [pkg for pkg in DEPENDENCIES if not is_installed(pkg)]
    if missing_dependencies:
        print(f"Installing missing dependencies: {', '.join(missing_dependencies)}")
        try:
            subprocess.run([PYTHON_EXEC, "-m", "pip", "install"] + missing_dependencies, check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error installing dependencies: {e}")
            sys.exit(1)


def download_audio(youtube_url):
    """Downloads audio from a YouTube video and saves it as a WAV file named after the title."""
    import yt_dlp
    from imageio_ffmpeg import get_ffmpeg_exe
    import re

    # Ensure audiofiles directory exists
    os.makedirs("audiofiles", exist_ok=True)

    # Setup a temporary YDL instance to get video info
    with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
        try:
            info = ydl.extract_info(youtube_url, download=False)
            title = info.get("title", "audio").strip()
            # Sanitize title to be a valid filename
            safe_title = re.sub(r'[\\/*?:"<>|]', "_", title)
        except yt_dlp.utils.DownloadError as e:
            print(f"Error fetching video info: {e}")
            sys.exit(1)

    output_path = os.path.join("audiofiles", f"{safe_title}.%(ext)s")

    # Get FFmpeg path
    ffmpeg_path = get_ffmpeg_exe()
    if not os.path.exists(ffmpeg_path):
        print("FFmpeg not found. Please install it or ensure it's accessible.")
        sys.exit(1)

    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'wav',
        }],
        'postprocessor_args': ['-ar', '44100', '-ac', '2'],
        'outtmpl': output_path,
        'ffmpeg_location': ffmpeg_path
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            ydl.download([youtube_url])
        except yt_dlp.utils.DownloadError as e:
            print(f"Error downloading video: {e}")
            sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python youtube_fetch.py <YouTube_URL>")
        sys.exit(1)

    video_url = sys.argv[1]

    # Check if already running inside the virtual environment
    if os.path.exists(VENV_DIR) and sys.prefix == os.path.abspath(VENV_DIR):
        download_audio(video_url)
    else:
        # Step 1: Setup Virtual Environment & Install Dependencies
        setup_venv()

        # Step 2: Restart the script inside the virtual environment **only once**
        print("Restarting inside virtual environment...")
        subprocess.run([PYTHON_EXEC, __file__, video_url], check=True)
        sys.exit(0)
