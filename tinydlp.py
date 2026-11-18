"""
Tiny-dlp v3.0 — Navaja suiza de descargas con bootstrapper de dependencias.

Novedades v3.0:
  * Protocolo tinydlp:// + IPC por archivo para instancia única.
  * API HTTP local (127.0.0.1:8484) con /add, /status, /pause, /resume, /ping.
  * Auto-organización de descargas por tipo (Música / Vídeos / Artista).
  * Detección multi-URL desde el portapapeles.
  * Historial de últimas 10 URLs copiadas, persistente.
  * Cookies desde navegador, límite de velocidad y metadata embebida.
  * Notificaciones con acciones (win11toast) y botón de instalación de dependencias.

Novedades v2.0:
  * Descarga automática de yt-dlp.exe y ffmpeg.exe en el primer arranque.
  * Selector de calidad (audio kbps / vídeo resolución).
  * Subtítulos automáticos embebidos (multi-idioma).
  * SponsorBlock integrado.
  * Selector de playlists con checkboxes.
  * Cola persistente (sobrevive reinicios).
  * Hotkey global Ctrl+Alt+D con pynput.

Autor: MarkBN · Licencia: MIT
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import webbrowser
import zipfile
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from http.server import BaseHTTPRequestHandler, HTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import Empty, Queue
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

import pyperclip
import pystray
from PIL import Image, ImageDraw

try:
    from pynput import keyboard as pynput_keyboard
    PYNPUT_AVAILABLE = True
except Exception:
    PYNPUT_AVAILABLE = False
    pynput_keyboard = None  # type: ignore

try:
    import winreg  # type: ignore
    WINREG_AVAILABLE = True
except Exception:
    winreg = None  # type: ignore
    WINREG_AVAILABLE = False

# ============================================================
# 1. CONSTANTES
# ============================================================

APP_NAME = "Tiny-dlp"
APP_VERSION = "3.0.0"
CREDITS_URL = "https://www.instagram.com/markbn115/?hl=es"

CONFIG_DIR = Path.home() / ".tinydlp"
CONFIG_FILE = CONFIG_DIR / "config.json"
HISTORY_FILE = CONFIG_DIR / "history.json"
QUEUE_FILE = CONFIG_DIR / "queue.json"
RECENT_URLS_FILE = CONFIG_DIR / "recent_urls.json"
IPC_URL_FILE = CONFIG_DIR / "ipc_url.txt"
LOG_FILE = CONFIG_DIR / "tinydlp.log"
BIN_DIR = CONFIG_DIR / "bin"

PROTOCOL_SCHEME = "tinydlp"
# Puerto del guard de instancia única. Alto y poco común para no chocar con servicios.
INSTANCE_PORT = 8486

DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / "Tiny-dlp"

YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"

COLOR_BG = "#0F0F0F"
COLOR_SURFACE = "#1A1A1A"
COLOR_HOVER = "#2A2A2A"
COLOR_ACCENT = "#1DB954"
COLOR_TEXT = "#FFFFFF"
COLOR_SUBTLE = "#B3B3B3"
COLOR_WARN = "#FF9F43"
COLOR_ERROR = "#FF4B6B"
COLOR_INFO = "#3B82F6"

DEFAULT_HOSTS = (
    "youtube.com", "youtu.be", "music.youtube.com",
    "soundcloud.com", "vimeo.com", "dailymotion.com",
    "twitch.tv", "twitter.com", "x.com",
    "tiktok.com", "instagram.com", "facebook.com",
    "reddit.com", "bandcamp.com",
)

# ============================================================
# 2. LOGGING
# ============================================================

def setup_logging() -> logging.Logger:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(APP_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fh = RotatingFileHandler(
        LOG_FILE, maxBytes=512 * 1024, backupCount=3, encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(threadName)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(sh)
    return logger


logger = setup_logging()


# ============================================================
# 3. ENUMS Y DATACLASSES
# ============================================================

class DownloadMode(str, Enum):
    ASK = "ask"
    AUDIO = "audio"
    VIDEO = "video"
    BOTH = "both"


class TaskState(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class DownloadTask:
    id: str
    url: str
    kind: str
    state: str = TaskState.QUEUED.value
    title: str = ""
    progress: float = 0.0
    speed: float = 0.0
    eta: float = 0.0
    total_bytes: int = 0
    downloaded_bytes: int = 0
    output_path: str = ""
    error: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event)
    created_at: float = field(default_factory=time.time)

    def to_history(self) -> dict:
        # asdict() deep-copia cancel_event (lock no picklable): serializamos a mano.
        return {k: v for k, v in self.__dict__.items() if k != "cancel_event"}


@dataclass
class Config:
    download_dir: str = str(DEFAULT_DOWNLOAD_DIR)
    mode: str = DownloadMode.ASK.value
    poll_interval: float = 0.7
    max_concurrent: int = 2
    detection_paused: bool = False
    auto_open_folder: bool = False
    window_x: Optional[int] = None
    window_y: Optional[int] = None
    hosts: list[str] = field(default_factory=lambda: list(DEFAULT_HOSTS))
    # v2.0
    deps_prompted: bool = False
    audio_quality: str = "192"      # 128 | 192 | 320
    video_quality: str = "best"     # 720 | 1080 | 4k | best
    subtitles: bool = False
    subtitle_langs: str = "es,en"
    sponsorblock: bool = False
    hotkey_enabled: bool = True
    # v3.0
    auto_organize: bool = True
    api_port: int = 8484          # 0 = API deshabilitada
    cookies_browser: str = ""     # "" | chrome | firefox | edge | brave
    rate_limit: str = ""          # "" = sin límite; "500K", "2M"
    embed_metadata: bool = True
    audio_format: str = "mp3"     # mp3 | m4a | flac | wav | opus
    video_format: str = "mp4"     # mp4 | mkv | webm

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_FILE.exists():
            return cls()
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            cfg = cls()
            for key, val in data.items():
                if hasattr(cfg, key):
                    try:
                        setattr(cfg, key, val)
                    except Exception:
                        pass
            if not isinstance(cfg.hosts, list) or not cfg.hosts:
                cfg.hosts = list(DEFAULT_HOSTS)
            # Normaliza tipos: configs viejas o editadas a mano pueden traer strings.
            try:
                cfg.api_port = int(cfg.api_port)
            except Exception:
                cfg.api_port = 8484
            if cfg.cookies_browser not in ("", "chrome", "firefox", "edge", "brave"):
                cfg.cookies_browser = ""
            cfg.auto_organize = bool(cfg.auto_organize)
            cfg.embed_metadata = bool(cfg.embed_metadata)
            cfg.rate_limit = str(cfg.rate_limit or "").strip()
            cfg.audio_format = str(cfg.audio_format or "mp3").lower()
            if cfg.audio_format not in ("mp3", "m4a", "flac", "wav", "opus"):
                cfg.audio_format = "mp3"
            cfg.video_format = str(cfg.video_format or "mp4").lower()
            if cfg.video_format not in ("mp4", "mkv", "webm"):
                cfg.video_format = "mp4"
            return cfg
        except Exception as exc:
            logger.warning("Config corrupta, usando defaults: %s", exc)
            return cls()

    def save(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(
                json.dumps(asdict(self), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.error("No se pudo guardar config: %s", exc)


# ============================================================
# 4. UTILIDADES
# ============================================================

def format_bytes(n: float) -> str:
    if n <= 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def format_eta(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


def find_ffmpeg() -> Optional[Path]:
    candidates = [
        Path(getattr(sys, "_MEIPASS", "")) / "ffmpeg.exe",
        Path(os.path.abspath(".")) / "ffmpeg.exe",
        BIN_DIR / "ffmpeg.exe",
        BIN_DIR / "ffmpeg",
    ]
    for c in candidates:
        if c.exists():
            return c
    which = shutil.which("ffmpeg")
    return Path(which) if which else None


def find_ytdlp_binary() -> Optional[Path]:
    for name in ("yt-dlp.exe", "yt-dlp"):
        p = BIN_DIR / name
        if p.exists():
            return p
    return None


def open_path_native(path: Path | str) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        logger.debug("open_path falló: %s", exc)


def parse_rate_limit(value: str) -> int | None:
    """Convierte '500K' / '2M' a bytes; devuelve None si está vacío o es inválido."""
    if not value:
        return None
    m = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*([kmg]?)\s*", value, re.IGNORECASE)
    if not m:
        logger.warning("rate_limit inválido: %s", value)
        return None
    number = float(m.group(1).replace(",", "."))
    mult = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[m.group(2).lower()]
    return int(number * mult)


AUDIO_EXTS = (".mp3", ".flac", ".ogg", ".m4a", ".opus", ".wav")
VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov")


def _sanitize_component(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned[:80] or "Sin título"


def _resolve_output_file(task: DownloadTask) -> Path | None:
    """Devuelve el archivo final real (yt-dlp a veces reporta la extensión previa al postproceso)."""
    if not task.output_path:
        return None
    raw = Path(task.output_path)
    if raw.is_file():
        return raw
    stem = raw.with_suffix("")
    for ext in (AUDIO_EXTS + VIDEO_EXTS):
        candidate = stem.with_suffix(ext)
        if candidate.is_file():
            return candidate
    return None


def _move_unique(src: Path, dest_dir: Path) -> Path:
    """Mueve src a dest_dir evitando sobrescribir; devuelve la ruta final."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / src.name
    n = 1
    while target.exists():
        target = dest_dir / f"{src.stem} ({n}){src.suffix}"
        n += 1
    if target.resolve() == src.resolve():
        return src
    shutil.move(str(src), str(target))
    logger.info("Organizado: %s → %s", src.name, target.parent)
    return target


def organize_download(task: DownloadTask, base: Path) -> Path:
    """Mueve el archivo terminado a Música/ o Vídeos/ (y artista si hay ' - '); devuelve la ruta final."""
    src = _resolve_output_file(task)
    if src is None:
        logger.debug("Organizar: archivo no encontrado para %s", task.url)
        return Path(task.output_path) if task.output_path else base

    # En modo "ambos" también hay un vídeo con el mismo nombre base.
    companions: list[Path] = []
    if task.kind == "both":
        for ext in VIDEO_EXTS:
            candidate = src.with_suffix(ext)
            if candidate.is_file() and candidate != src:
                companions.append(candidate)

    ext = src.suffix.lower()
    title = task.title or src.stem
    if ext in AUDIO_EXTS:
        dest_dir = base / "Música"
        if " - " in title:
            artist, _song = title.split(" - ", 1)
            artist = _sanitize_component(artist)
            if artist:
                dest_dir = dest_dir / artist
    elif ext in VIDEO_EXTS:
        dest_dir = base / "Vídeos"
    else:
        return src

    try:
        target = _move_unique(src, dest_dir)
        task.output_path = str(target)
        for video in companions:
            try:
                _move_unique(video, base / "Vídeos")
            except Exception as exc:
                logger.warning("No se pudo organizar %s: %s", video, exc)
        return target
    except Exception as exc:
        logger.warning("No se pudo organizar %s: %s", src, exc)
        return src


# ============================================================
# 4b. PROTOCOLO tinydlp:// E INSTANCIA ÚNICA
# ============================================================

def _protocol_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable)}" "%1"'
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    launcher = pythonw if pythonw.exists() else python
    return f'"{launcher}" "{Path(__file__).resolve()}" "%1"'


def is_protocol_registered() -> bool:
    """True si HKCU ya tiene un comando para tinydlp://."""
    if sys.platform != "win32" or not WINREG_AVAILABLE:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            rf"Software\Classes\{PROTOCOL_SCHEME}\shell\open\command",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "")
            return bool(value)
    except OSError:
        return False
    except Exception:
        return False


def register_protocol() -> bool:
    """Registra tinydlp:// en HKCU (solo el usuario actual; nunca HKLM)."""
    if sys.platform != "win32" or not WINREG_AVAILABLE:
        logger.warning("Protocolo tinydlp://: no soportado en esta plataforma")
        return False
    try:
        base = rf"Software\Classes\{PROTOCOL_SCHEME}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:Tiny-dlp Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\DefaultIcon") as key:
            icon = Path(getattr(sys, "_MEIPASS", ".")) / "icon.ico"
            value = str(icon) if icon.exists() else sys.executable
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{value}",0')
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, base + r"\shell\open\command",
        ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, _protocol_command())
        logger.info("Protocolo tinydlp:// registrado (HKCU): %s", _protocol_command())
        return True
    except Exception as exc:
        logger.warning("No se pudo registrar tinydlp://: %s", exc)
        return False


def parse_protocol_arg(argv: list[str]) -> tuple[str, str] | None:
    """Extrae (url, kind) de un argumento tinydlp://add?url=...; None si no aplica."""
    for raw in argv[1:]:
        value = (raw or "").strip().strip('"')
        if not value.lower().startswith(PROTOCOL_SCHEME + ":"):
            continue
        try:
            parsed = urlparse(value)
            if parsed.netloc and parsed.netloc.lower() not in ("add", ""):
                continue
            params = parse_qs(parsed.query)
            url = (params.get("url") or [""])[0].strip()
            kind = ((params.get("kind") or ["ask"])[0] or "ask").strip().lower()
            if kind not in ("audio", "video", "both", "ask"):
                kind = "ask"
            if not url:
                continue
            return url, kind
        except Exception as exc:
            logger.warning("Argumento tinydlp:// inválido: %s", exc)
    return None


def write_ipc_url(url: str, kind: str = "ask") -> bool:
    """Deja la URL para que la instancia en ejecución la recoja en <=500 ms."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        IPC_URL_FILE.write_text(
            json.dumps({"url": url, "kind": kind}), encoding="utf-8",
        )
        logger.info("IPC: URL enviada a la instancia activa (%s)", kind)
        return True
    except Exception as exc:
        logger.warning("IPC no disponible: %s", exc)
        return False


class InstanceGuard:
    """Instancia única con un socket local exclusivo (sin mutex ni archivos huérfanos)."""

    def __init__(self, port: int = INSTANCE_PORT) -> None:
        self._port = port
        self._sock: socket.socket | None = None
        self.acquired = False

    def acquire(self) -> bool:
        """Intenta tomar el puerto de instancia; False si otro proceso lo tiene."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # En Windows SO_REUSEADDR permitiría doble bind: no lo usamos.
            if sys.platform != "win32":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", self._port))
            sock.listen(4)
            self._sock = sock
            self.acquired = True
            threading.Thread(
                target=self._serve, daemon=True, name="instance-guard",
            ).start()
            return True
        except OSError:
            return False
        except Exception as exc:
            logger.warning("InstanceGuard: %s", exc)
            return False

    def _serve(self) -> None:
        while self._sock is not None:
            try:
                conn, _addr = self._sock.accept()
            except OSError:
                return
            try:
                conn.settimeout(0.5)
                if conn.recv(32).strip() == b"TINYDLP?":
                    conn.sendall(b"TINYDLP!")
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    @staticmethod
    def is_tinydlp_running(port: int = INSTANCE_PORT) -> bool:
        """Pregunta al guard si la instancia es realmente Tiny-dlp."""
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.6) as sock:
                sock.sendall(b"TINYDLP?")
                sock.settimeout(0.6)
                return sock.recv(32).strip() == b"TINYDLP!"
        except Exception:
            return False

    def release(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None
        self.acquired = False


# ============================================================
# 5. NOTIFICACIONES
# ============================================================

class Notifier:
    def __init__(self) -> None:
        self._win11 = False
        self._plyer = False
        try:
            from win11toast import toast  # type: ignore
            self._win11 = True
            self._toast = toast
        except Exception:
            pass
        if not self._win11:
            try:
                from plyer import notification  # type: ignore
                self._plyer = True
                self._notify = notification.notify
            except Exception:
                pass

    def notify(
        self,
        title: str,
        message: str,
        on_click: Optional[Callable] = None,
        actions: list[tuple[str, Callable[[], None]]] | None = None,
    ) -> None:
        """Muestra una notificación; con win11toast soporta botones de acción."""
        actions = actions or []
        try:
            if self._win11:
                kwargs: dict = {}
                if actions:
                    dispatch: dict[str, Callable[[], None]] = {}
                    buttons: list[dict] = []
                    for idx, (label, callback) in enumerate(actions):
                        key = f"action-{idx}"
                        dispatch[key] = callback
                        buttons.append({
                            "activationType": "foreground",
                            "arguments": key,
                            "content": label,
                        })

                    def _on_action(args) -> None:
                        if isinstance(args, dict):
                            key = str(args.get("arguments") or "")
                        elif isinstance(args, str):
                            key = args
                        else:
                            key = ""
                        callback = dispatch.get(key)
                        if callback is None:
                            return
                        try:
                            callback()
                        except Exception as exc:
                            logger.warning("Acción de notificación falló: %s", exc)

                    kwargs["buttons"] = buttons
                    kwargs["on_click"] = _on_action
                elif on_click:
                    kwargs["on_click"] = on_click

                def _fire() -> None:
                    # win11toast bloquea hasta que el toast se descarta: hilo aparte.
                    try:
                        self._toast(title, message, **kwargs)
                    except Exception as exc:
                        logger.debug("Toast falló: %s", exc)

                threading.Thread(
                    target=_fire, daemon=True, name="toast",
                ).start()
                return
            if self._plyer:
                self._notify(
                    title=title, message=message, app_name=APP_NAME, timeout=5,
                )
                if actions:
                    logger.info(
                        "Notificación con %d acciones sin soporte (plyer): %s",
                        len(actions), title,
                    )
                return
        except Exception as exc:
            logger.debug("Notificación falló: %s", exc)
        if actions:
            logger.info(
                "Notificación con acciones sin backend: %s — %s", title, message,
            )
        logger.info("[notify] %s — %s", title, message)


# ============================================================
# 6. ICONOS
# ============================================================

def make_tray_icon(state: str = "idle", size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    palette = {
        "idle": (110, 110, 110, 255),
        "paused": (90, 90, 90, 255),
        "pending": (30, 215, 96, 255),
        "downloading": (255, 165, 0, 255),
        "error": (255, 75, 107, 255),
    }
    bg = palette.get(state, palette["idle"])
    d.ellipse((2, 2, size - 2, size - 2), fill=bg)
    cx, cy = size // 2, size // 2
    if state == "paused":
        d.rectangle((cx - 10, cy - 12, cx - 4, cy + 12), fill=(0, 0, 0))
        d.rectangle((cx + 4, cy - 12, cx + 10, cy + 12), fill=(0, 0, 0))
    elif state == "error":
        d.line((cx - 12, cy - 12, cx + 12, cy + 12), fill=(0, 0, 0), width=5)
        d.line((cx + 12, cy - 12, cx - 12, cy + 12), fill=(0, 0, 0), width=5)
    else:
        d.polygon(
            [(cx - 10, cy - 14), (cx + 10, cy - 14), (cx + 10, cy + 2),
             (cx + 16, cy + 2), (cx, cy + 18), (cx - 16, cy + 2),
             (cx - 10, cy + 2)],
            fill=(0, 0, 0),
        )
    return img


# ============================================================
# 7. BOOTSTRAPPER DE DEPENDENCIAS
# ============================================================

class DependencyManager:
    """Descarga yt-dlp.exe y ffmpeg.exe desde repos oficiales."""

    def __init__(self) -> None:
        BIN_DIR.mkdir(parents=True, exist_ok=True)

    def check(self) -> dict[str, bool]:
        return {
            "yt-dlp": self._has_ytdlp(),
            "ffmpeg": find_ffmpeg() is not None,
        }

    def _has_ytdlp(self) -> bool:
        if find_ytdlp_binary() is not None:
            return True
        try:
            import yt_dlp  # noqa: F401
            return True
        except ImportError:
            return False

    def download_ytdlp(self, on_progress: Optional[Callable[[str, float], None]] = None) -> bool:
        target = BIN_DIR / "yt-dlp.exe"
        try:
            logger.info("Descargando yt-dlp desde %s", YTDLP_URL)
            self._download_file(YTDLP_URL, target, on_progress, "yt-dlp")
            ok = target.exists() and target.stat().st_size > 1_000_000
            logger.info("yt-dlp descargado: %s (%d bytes)", target, target.stat().st_size if target.exists() else 0)
            return ok
        except Exception as exc:
            logger.exception("Falló descarga de yt-dlp: %s", exc)
            return False

    def download_ffmpeg(self, on_progress: Optional[Callable[[str, float], None]] = None) -> bool:
        zip_path = BIN_DIR / "_ffmpeg_tmp.zip"
        try:
            logger.info("Descargando ffmpeg desde %s", FFMPEG_URL)
            self._download_file(FFMPEG_URL, zip_path, on_progress, "ffmpeg")
            extracted = 0
            with zipfile.ZipFile(zip_path) as zf:
                for member in zf.namelist():
                    base = member.replace("\\", "/").split("/")[-1]
                    if base in ("ffmpeg.exe", "ffprobe.exe"):
                        with zf.open(member) as src:
                            dst_path = BIN_DIR / base
                            with open(dst_path, "wb") as dst:
                                shutil.copyfileobj(src, dst)
                            extracted += 1
            logger.info("Extraídos %d binarios de ffmpeg", extracted)
            return (BIN_DIR / "ffmpeg.exe").exists()
        except Exception as exc:
            logger.exception("Falló descarga de ffmpeg: %s", exc)
            return False
        finally:
            if zip_path.exists():
                try:
                    zip_path.unlink()
                except Exception:
                    pass

    def _download_file(
        self,
        url: str,
        dest: Path,
        on_progress: Optional[Callable[[str, float], None]],
        name: str,
    ) -> None:
        def _hook(blocks: int, block_size: int, total: int) -> None:
            if on_progress and total > 0:
                try:
                    on_progress(name, blocks * block_size / total)
                except Exception:
                    pass

        tmp = Path(str(dest) + ".part")
        try:
            urllib.request.urlretrieve(url, tmp, _hook)
            tmp.replace(dest)
        except Exception:
            if tmp.exists():
                try:
                    tmp.unlink()
                except Exception:
                    pass
            raise


# ============================================================
# 8. WIZARD DE PRIMER ARRANQUE
# ============================================================

class FirstRunWizard:
    """Ventana de configuración inicial (descarga de dependencias)."""

    def __init__(self, deps: DependencyManager) -> None:
        import customtkinter as ctk
        self._ctk = ctk
        self._deps = deps
        self._completed = False

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")

        self.root = ctk.CTk()
        self.root.title(f"{APP_NAME} — Configuración inicial")
        self.root.geometry("540x440")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._skip)

        icon = Path(getattr(sys, "_MEIPASS", ".")) / "icon.ico"
        if icon.exists():
            try:
                self.root.iconbitmap(str(icon))
            except Exception:
                pass

        self._build()

    def _build(self) -> None:
        ctk = self._ctk
        ctk.CTkLabel(
            self.root, text="Bienvenido a Tiny-dlp",
            font=("Segoe UI", 20, "bold"),
        ).pack(pady=(24, 6))

        ctk.CTkLabel(
            self.root,
            text="Necesitamos descargar dos dependencias oficiales.\n"
                 "Se guardarán en ~/.tinydlp/bin/ (solo la primera vez).",
            font=("Segoe UI", 11), text_color=COLOR_SUBTLE,
            justify="center",
        ).pack(pady=(0, 20))

        status = ctk.CTkFrame(self.root, fg_color=COLOR_SURFACE, corner_radius=10)
        status.pack(fill="x", padx=30, pady=6)

        self._lbl_ytdlp = ctk.CTkLabel(
            status, text="", anchor="w", font=("Segoe UI", 11),
        )
        self._lbl_ytdlp.pack(fill="x", padx=14, pady=(12, 6))

        self._lbl_ffmpeg = ctk.CTkLabel(
            status, text="", anchor="w", font=("Segoe UI", 11),
        )
        self._lbl_ffmpeg.pack(fill="x", padx=14, pady=(0, 12))

        self._progress = ctk.CTkProgressBar(self.root, width=460, height=8)
        self._progress.set(0)

        self._lbl_progress = ctk.CTkLabel(
            self.root, text="", font=("Segoe UI", 10), text_color=COLOR_SUBTLE,
        )
        self._lbl_progress.pack(pady=(8, 4))

        btn_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        btn_frame.pack(pady=10)

        self._btn_start = ctk.CTkButton(
            btn_frame, text="Descargar dependencias",
            width=220, height=42, font=("Segoe UI", 12, "bold"),
            fg_color=COLOR_ACCENT, hover_color="#17A845",
            text_color="#000000",
            command=self._start_download,
        )
        self._btn_start.pack(side="left", padx=6)

        self._btn_skip = ctk.CTkButton(
            btn_frame, text="Saltar (usar Python)", width=170, height=42,
            fg_color="transparent", hover_color=COLOR_HOVER,
            text_color=COLOR_SUBTLE, font=("Segoe UI", 11),
            command=self._skip,
        )
        self._btn_skip.pack(side="left", padx=6)

        self._refresh_status()

    def _refresh_status(self) -> None:
        s = self._deps.check()
        self._lbl_ytdlp.configure(
            text=("✓  yt-dlp listo" if s["yt-dlp"] else "…  yt-dlp pendiente"),
            text_color=(COLOR_ACCENT if s["yt-dlp"] else COLOR_WARN),
        )
        self._lbl_ffmpeg.configure(
            text=("✓  ffmpeg listo" if s["ffmpeg"] else "…  ffmpeg pendiente (~80 MB)"),
            text_color=(COLOR_ACCENT if s["ffmpeg"] else COLOR_WARN),
        )

    def _start_download(self) -> None:
        self._btn_start.configure(state="disabled")
        self._btn_skip.configure(state="disabled")
        self._progress.pack(pady=(8, 4), padx=30)
        threading.Thread(target=self._do_download, daemon=True).start()

    def _do_download(self) -> None:
        try:
            s = self._deps.check()
            if not s["yt-dlp"]:
                self._set_status("Descargando yt-dlp…")
                self._deps.download_ytdlp(self._on_progress)
                self._safe(lambda: self._refresh_status())

            if not s["ffmpeg"]:
                self._set_status("Descargando ffmpeg… (puede tardar)")
                self._deps.download_ffmpeg(self._on_progress)
                self._safe(lambda: self._refresh_status())

            self._safe(self._on_done)
        except Exception as exc:
            logger.exception("Error en wizard: %s", exc)
            self._safe(lambda: self._lbl_progress.configure(
                text=f"Error: {str(exc)[:80]}", text_color=COLOR_ERROR,
            ))
            self._safe(lambda: self._btn_start.configure(state="normal"))
            self._safe(lambda: self._btn_skip.configure(state="normal"))

    def _on_progress(self, name: str, fraction: float) -> None:
        self._safe(lambda: self._progress.set(fraction))
        self._safe(lambda: self._lbl_progress.configure(
            text=f"{name}: {int(fraction * 100)}%",
        ))

    def _set_status(self, text: str) -> None:
        self._safe(lambda: self._lbl_progress.configure(text=text))

    def _safe(self, fn) -> None:
        try:
            self.root.after(0, fn)
        except Exception:
            pass

    def _on_done(self) -> None:
        self._refresh_status()
        self._lbl_progress.configure(
            text="✓  Dependencias listas. Iniciando Tiny-dlp…",
            text_color=COLOR_ACCENT,
        )
        self._completed = True
        self.root.after(1000, self.root.destroy)

    def _skip(self) -> None:
        self._completed = True
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> bool:
        self.root.mainloop()
        return self._completed


# ============================================================
# 9. DETECCIÓN DE URLs
# ============================================================

class UrlDetector:
    def __init__(self, hosts: list[str]) -> None:
        self._hosts = tuple(hosts)
        pattern = r"https?://(?:www\.|m\.|music\.)?(?:" + \
                  "|".join(re.escape(h) for h in self._hosts) + \
                  r")/[^\s<>\"'`]+"
        self._re = re.compile(pattern, re.IGNORECASE)

    def find(self, text: str) -> Optional[str]:
        if not text:
            return None
        m = self._re.search(text)
        return m.group(0).rstrip(".,;:") if m else None

    def find_all(self, text: str) -> list[str]:
        """Devuelve todas las URLs válidas del texto, sin duplicados y en orden."""
        if not text:
            return []
        found: list[str] = []
        seen: set[str] = set()
        for match in self._re.findall(text):
            url = match.rstrip(".,;:")
            if url and url not in seen:
                seen.add(url)
                found.append(url)
        return found

    def update_hosts(self, hosts: list[str]) -> None:
        self.__init__(hosts)   # type: ignore


# ============================================================
# 10. HISTORIAL
# ============================================================

class HistoryManager:
    MAX_ENTRIES = 500

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            if HISTORY_FILE.exists():
                data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._entries = data[-self.MAX_ENTRIES:]
        except Exception as exc:
            logger.warning("Historial corrupto: %s", exc)
            self._entries = []

    def add(self, task: DownloadTask) -> None:
        with self._lock:
            entry = task.to_history()
            entry.pop("id", None)
            self._entries.append(entry)
            if len(self._entries) > self.MAX_ENTRIES:
                self._entries = self._entries[-self.MAX_ENTRIES:]
            self._save()

    def _save(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            HISTORY_FILE.write_text(
                json.dumps(self._entries, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.error("No se pudo guardar historial: %s", exc)

    def recent(self, n: int = 10) -> list[dict]:
        with self._lock:
            return list(reversed(self._entries[-n:]))

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._save()


class RecentUrlsStore:
    """Últimas 10 URLs copiadas, persistidas para sobrevivir reinicios."""

    MAX_ENTRIES = 10

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._urls: deque[str] = deque(maxlen=self.MAX_ENTRIES)
        self._load()

    def _load(self) -> None:
        try:
            if RECENT_URLS_FILE.exists():
                data = json.loads(RECENT_URLS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for url in data[-self.MAX_ENTRIES:]:
                        if isinstance(url, str) and url:
                            self._urls.append(url)
        except Exception as exc:
            logger.warning("recent_urls corrupto: %s", exc)

    def _save(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            RECENT_URLS_FILE.write_text(
                json.dumps(list(self._urls), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("No se pudo guardar recent_urls: %s", exc)

    def add(self, url: str) -> None:
        """Añade al frente (sin duplicados), recortando a 10 entradas."""
        if not url:
            return
        with self._lock:
            if url in self._urls:
                self._urls.remove(url)
            self._urls.append(url)
            self._save()

    def urls(self) -> list[str]:
        """Más recientes primero."""
        with self._lock:
            return list(reversed(self._urls))

    def clear(self) -> None:
        with self._lock:
            self._urls.clear()
            self._save()


# ============================================================
# 11. DESCARGA
# ============================================================

class DownloadCancelled(Exception):
    pass


class Downloader:
    def __init__(
        self,
        config: Config,
        ffmpeg_path: Optional[Path],
        on_progress: Callable[[DownloadTask], None],
        on_state: Callable[[DownloadTask], None],
    ) -> None:
        self._config = config
        self._ffmpeg = ffmpeg_path
        self._on_progress = on_progress
        self._on_state = on_state
        self._ytdlp_bin = find_ytdlp_binary()
        if self._ytdlp_bin is not None:
            self._mode = "subprocess"
            logger.info("Motor: yt-dlp binario (%s)", self._ytdlp_bin)
            return
        try:
            import yt_dlp  # noqa: F401
            self._mode = "python"
            logger.info("Motor: yt-dlp módulo Python")
        except ImportError:
            self._mode = "missing"
            logger.error("yt-dlp NO disponible")

    @property
    def available(self) -> bool:
        return self._mode != "missing"

    def download(self, task: DownloadTask) -> None:
        try:
            if self._mode == "python":
                self._download_python(task)
            elif self._mode == "subprocess":
                self._download_subprocess(task)
            else:
                raise RuntimeError("yt-dlp no disponible")
        except DownloadCancelled:
            task.state = TaskState.CANCELLED.value
            self._on_state(task)
        except Exception as exc:
            task.state = TaskState.ERROR.value
            task.error = str(exc)[:200]
            self._on_state(task)
            logger.exception("Error descargando %s: %s", task.url, exc)

    # -------- helpers de formato --------

    def _video_format(self) -> str:
        """Selector de formato según contenedor (MP4/MKV/WEBM) y resolución."""
        q = self._config.video_quality
        container = self._config.video_format
        h = {"720": 720, "1080": 1080, "4k": 2160}.get(q)
        if container == "mp4":
            if q == "best":
                return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            return (
                f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]"
                f"/best[height<={h}][ext=mp4]/best"
            )
        if container == "webm":
            if q == "best":
                return (
                    "bestvideo[ext=webm]+bestaudio[ext=webm]"
                    "/best[ext=webm]/best"
                )
            return (
                f"bestvideo[height<={h}][ext=webm]+bestaudio[ext=webm]"
                f"/best[height<={h}][ext=webm]/best"
            )
        if q == "best":
            return "bestvideo+bestaudio/best"
        return f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"

    def _subtitle_opts(self) -> dict:
        if not self._config.subtitles:
            return {}
        langs = [s.strip() for s in self._config.subtitle_langs.split(",") if s.strip()]
        return {
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": langs or ["es", "en"],
            "embedsubtitles": True,
        }

    def _sponsorblock_postprocessors(self) -> list[dict]:
        if not self._config.sponsorblock:
            return []
        cats = ["sponsor", "selfpromo", "interaction"]
        return [{
            "key": "SponsorBlock",
            "categories": cats,
        }, {
            "key": "ModifyChapters",
            "remove_sponsor_segments": cats,
        }]

    # -------- python mode --------

    def _build_opts(self, task: DownloadTask) -> dict:
        out_dir = Path(self._config.download_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        opts: dict = {
            "outtmpl": str(out_dir / "%(title).180B [%(id)s].%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "retries": 3,
            "fragment_retries": 3,
            "continuedl": True,
            "progress_hooks": [self._make_hook(task)],
            "postprocessor_hooks": [self._make_pp_hook(task)],
            "consoletitle": False,
            "overwrites": False,
        }
        if self._ffmpeg:
            opts["ffmpeg_location"] = str(self._ffmpeg.parent)

        if self._config.cookies_browser:
            opts["cookiesfrombrowser"] = (self._config.cookies_browser,)

        rate = parse_rate_limit(self._config.rate_limit)
        if rate:
            opts["ratelimit"] = rate

        if task.kind in ("audio", "both"):
            audio_fmt = self._config.audio_format
            if task.kind == "both":
                opts["format"] = self._video_format()
                opts["merge_output_format"] = self._config.video_format
            else:
                opts["format"] = "bestaudio/best"
            audio_pp: dict = {
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_fmt,
            }
            if audio_fmt in ("mp3", "m4a", "opus"):
                audio_pp["preferredquality"] = self._config.audio_quality
            if task.kind == "both":
                opts["keepvideo"] = True
            postprocessors: list[dict] = [audio_pp]
            if self._config.embed_metadata and audio_fmt != "wav":
                opts["writethumbnail"] = True
                postprocessors += [
                    {"key": "FFmpegMetadata"},
                    {"key": "EmbedThumbnail"},
                ]
            opts["postprocessors"] = postprocessors
        else:
            opts["format"] = self._video_format()
            opts["merge_output_format"] = self._config.video_format

        opts.update(self._subtitle_opts())
        pps = opts.get("postprocessors", []) + self._sponsorblock_postprocessors()
        if pps:
            opts["postprocessors"] = pps
        return opts

    def _make_hook(self, task: DownloadTask):
        def hook(d: dict) -> None:
            if task.cancel_event.is_set():
                raise DownloadCancelled("Cancelado")
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                task.total_bytes = int(total)
                task.downloaded_bytes = int(done)
                task.progress = (done / total) if total > 0 else 0.0
                task.speed = float(d.get("speed") or 0)
                task.eta = float(d.get("eta") or 0)
                if not task.title and d.get("info_dict"):
                    task.title = d["info_dict"].get("title", "")
                task.state = TaskState.DOWNLOADING.value
                self._on_progress(task)
            elif d.get("status") == "finished":
                task.progress = 1.0
                task.state = TaskState.PROCESSING.value
                self._on_state(task)
        return hook

    def _make_pp_hook(self, task: DownloadTask):
        def pp_hook(d: dict) -> None:
            if task.cancel_event.is_set():
                raise DownloadCancelled("Cancelado")
            if d.get("status") == "started":
                task.state = TaskState.PROCESSING.value
                self._on_state(task)
            elif d.get("status") == "finished":
                task.state = TaskState.DONE.value
                info = d.get("info_dict") or {}
                fp = info.get("filepath") or info.get("_filename") or ""
                if fp:
                    task.output_path = str(fp)
                self._on_state(task)
        return pp_hook

    def _cleanup_intermediates(self, task: DownloadTask) -> None:
        """Elimina streams intermedios .fNNN.* que --keep-video deja en modo 'ambos'."""
        if task.kind != "both" or not task.output_path:
            return
        out = Path(task.output_path)
        stem = out.with_suffix("").name
        try:
            for junk in out.parent.iterdir():
                if junk.is_file() and junk.name.startswith(stem + ".f"):
                    junk.unlink()
                    logger.debug("Intermedio eliminado: %s", junk.name)
        except Exception as exc:
            logger.debug("No se pudieron limpiar intermedios: %s", exc)

    def _download_python(self, task: DownloadTask) -> None:
        import yt_dlp  # type: ignore
        with yt_dlp.YoutubeDL(self._build_opts(task)) as ydl:
            info = ydl.extract_info(task.url, download=True)
            if not task.title and info:
                task.title = info.get("title", "")
            self._cleanup_intermediates(task)
            task.state = TaskState.DONE.value
            self._on_state(task)

    # -------- subprocess mode --------

    def _download_subprocess(self, task: DownloadTask) -> None:
        out_dir = Path(self._config.download_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(self._ytdlp_bin), "--newline", "--no-warnings",
            "-o", str(out_dir / "%(title).180B [%(id)s].%(ext)s"),
            "--retries", "3",
        ]
        if self._ffmpeg:
            cmd += ["--ffmpeg-location", str(self._ffmpeg.parent)]

        if task.kind in ("audio", "both"):
            if task.kind == "both":
                cmd += ["-f", self._video_format(),
                        "--merge-output-format", self._config.video_format,
                        "--keep-video"]
            cmd += ["-x", "--audio-format", self._config.audio_format]
            if self._config.audio_format in ("mp3", "m4a", "opus"):
                cmd += ["--audio-quality", self._config.audio_quality + "K"]
            if self._config.embed_metadata and self._config.audio_format != "wav":
                cmd += ["--embed-thumbnail", "--embed-metadata", "--add-metadata"]
        else:
            cmd += ["-f", self._video_format(),
                    "--merge-output-format", self._config.video_format]

        if self._config.cookies_browser:
            cmd += ["--cookies-from-browser", self._config.cookies_browser]

        if parse_rate_limit(self._config.rate_limit):
            cmd += ["--limit-rate", self._config.rate_limit.strip()]

        if self._config.subtitles:
            cmd += ["--write-subs", "--write-auto-subs",
                    "--sub-langs", self._config.subtitle_langs,
                    "--embed-subs"]

        if self._config.sponsorblock:
            cmd += ["--sponsorblock-remove", "sponsor,selfpromo,interaction"]

        cmd.append(task.url)

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        task.state = TaskState.DOWNLOADING.value
        self._on_state(task)
        last_update = 0.0
        try:
            for line in proc.stdout or []:
                if task.cancel_event.is_set():
                    proc.terminate()
                    raise DownloadCancelled("Cancelado")
                line = line.strip()
                if "[download]" in line and "%" in line:
                    try:
                        part = line.split("%", 1)[0].split()[-1]
                        task.progress = float(part) / 100.0
                    except Exception:
                        pass
                    now = time.time()
                    if now - last_update > 0.4:
                        last_update = now
                        self._on_progress(task)
                elif "Destination:" in line:
                    try:
                        task.output_path = line.split("Destination:", 1)[1].strip()
                        if not task.title:
                            task.title = re.sub(
                                r" \[[^\]]+\]$", "",
                                Path(task.output_path).stem,
                            )
                    except Exception:
                        pass
                elif "Merging formats into" in line:
                    try:
                        merged = line.split("Merging formats into", 1)[1].strip().strip('"')
                        task.output_path = merged
                        if not task.title:
                            task.title = re.sub(r" \[[^\]]+\]$", "", Path(merged).stem)
                    except Exception:
                        pass
            ret = proc.wait(timeout=10)
            if ret != 0:
                raise RuntimeError(f"yt-dlp terminó con código {ret}")
            self._cleanup_intermediates(task)
            task.state = TaskState.DONE.value
            task.progress = 1.0
            self._on_state(task)
        finally:
            if proc.poll() is None:
                proc.terminate()

    # -------- expansión de playlists --------

    def expand_playlist(self, url: str) -> list[dict]:
        """Devuelve lista de {url, title, duration} si la URL es una playlist."""
        try:
            if self._mode == "python":
                import yt_dlp  # type: ignore
                opts = {"quiet": True, "extract_flat": "in_playlist", "skip_download": True}
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
            elif self._mode == "subprocess":
                cmd = [str(self._ytdlp_bin), "--flat-playlist", "--dump-json", url]
                out = subprocess.check_output(
                    cmd, stderr=subprocess.DEVNULL, text=True, timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                entries = [json.loads(line) for line in out.splitlines() if line.strip()]
                return [
                    {
                        "url": e.get("url") or e.get("webpage_url", ""),
                        "title": e.get("title", "?"),
                        "duration": e.get("duration") or 0,
                    }
                    for e in entries
                ]
            else:
                return []

            if not info:
                return []
            if "entries" not in info:
                return [{
                    "url": url,
                    "title": info.get("title", "?"),
                    "duration": info.get("duration") or 0,
                }]
            items = []
            for e in info.get("entries") or []:
                if not e:
                    continue
                items.append({
                    "url": e.get("url") or e.get("webpage_url", ""),
                    "title": e.get("title", "?"),
                    "duration": e.get("duration") or 0,
                })
            return [i for i in items if i["url"]]
        except Exception as exc:
            logger.warning("No se pudo expandir playlist: %s", exc)
            return []


# ============================================================
# 12. COLA DE DESCARGAS
# ============================================================

class DownloadQueue:
    def __init__(
        self,
        config: Config,
        downloader: Downloader,
        history: HistoryManager,
        notifier: Notifier,
        on_change: Callable[[], None],
    ) -> None:
        self._config = config
        self._downloader = downloader
        self._history = history
        self._notifier = notifier
        self._on_change = on_change

        self._lock = threading.RLock()
        self._queue: Queue[DownloadTask] = Queue()
        self._tasks: dict[str, DownloadTask] = {}
        self._workers: list[threading.Thread] = []
        self._running = True
        self._paused = False

        self._start_workers()
        self._restore_pending()

    def _start_workers(self) -> None:
        n = max(1, self._config.max_concurrent)
        for i in range(n):
            t = threading.Thread(
                target=self._worker_loop, daemon=True, name=f"dl-worker-{i}",
            )
            t.start()
            self._workers.append(t)
        logger.info("Cola con %d workers", n)

    def _restore_pending(self) -> None:
        if not QUEUE_FILE.exists():
            return
        try:
            entries = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
            for e in entries:
                try:
                    task = DownloadTask(
                        id=e["id"], url=e["url"], kind=e["kind"],
                    )
                    self._queue.put(task)
                    with self._lock:
                        self._tasks[task.id] = task
                except Exception:
                    continue
            if entries:
                logger.info("Restauradas %d descargas pendientes", len(entries))
        except Exception as exc:
            logger.debug("No se pudo restaurar queue: %s", exc)

    def _save_pending(self) -> None:
        with self._lock:
            pending = [
                t.to_history() for t in self._tasks.values()
                if t.state == TaskState.QUEUED.value
            ]
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            QUEUE_FILE.write_text(
                json.dumps(pending, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("No se pudo guardar queue: %s", exc)

    def _worker_loop(self) -> None:
        while self._running:
            try:
                task = self._queue.get(timeout=0.5)
            except Empty:
                continue
            if task is None:
                break
            with self._lock:
                if self._paused:
                    self._queue.put(task)
                    time.sleep(0.3)
                    continue
                self._tasks[task.id] = task
            try:
                logger.info("[%s] %s -> %s", task.id[:6], task.kind, task.url)
                self._downloader.download(task)
            finally:
                if task.state == TaskState.DONE.value and self._config.auto_organize:
                    try:
                        organize_download(task, Path(self._config.download_dir))
                    except Exception as exc:
                        logger.warning("Auto-organizar falló: %s", exc)
                self._history.add(task)
                self._notify_done(task)
                self._queue.task_done()
                with self._lock:
                    self._tasks.pop(task.id, None)
                self._save_pending()
                self._on_change()

    def _notify_done(self, task: DownloadTask) -> None:
        if task.state == TaskState.DONE.value:
            folder = (
                Path(task.output_path).parent
                if task.output_path
                else Path(self._config.download_dir)
            )
            actions: list[tuple[str, Callable[[], None]]] = [
                ("📂 Abrir carpeta", lambda: open_path_native(folder)),
                ("🔁 Descargar otra vez", lambda: self.retry(task)),
            ]
            self._notifier.notify(
                "Tiny-dlp — Descarga completa",
                task.title or task.url,
                actions=actions,
            )
            if self._config.auto_open_folder and task.output_path:
                open_path_native(Path(task.output_path).parent)
        elif task.state == TaskState.ERROR.value:
            self._notifier.notify("Tiny-dlp — Error", task.error or "No se pudo descargar")

    def retry(self, task: DownloadTask) -> str:
        """Reencola una tarea nueva con la misma URL y tipo."""
        new_task = DownloadTask(
            id=uuid.uuid4().hex[:10], url=task.url, kind=task.kind,
        )
        self.enqueue(new_task)
        return new_task.id

    # -------- API pública --------

    def enqueue(self, task: DownloadTask) -> None:
        with self._lock:
            if any(
                t.url == task.url and t.kind == task.kind
                for t in self._tasks.values()
            ):
                logger.info("Ya en cola: %s (%s)", task.url, task.kind)
                return
            self._tasks[task.id] = task
        self._queue.put(task)
        self._save_pending()
        self._on_change()

    def enqueue_many(self, tasks: list[DownloadTask]) -> None:
        for t in tasks:
            self.enqueue(t)

    def find_by_url(self, url: str, kind: str | None = None) -> DownloadTask | None:
        """Busca una tarea activa por URL (y opcionalmente tipo) para deduplicar."""
        with self._lock:
            for task in self._tasks.values():
                if task.url == url and (kind is None or task.kind == kind):
                    return task
        return None

    def cancel(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.cancel_event.set()

    def pause(self) -> None:
        with self._lock:
            self._paused = True
        self._on_change()

    def resume(self) -> None:
        with self._lock:
            self._paused = False
        self._on_change()

    @property
    def paused(self) -> bool:
        return self._paused

    def snapshot(self) -> list[DownloadTask]:
        with self._lock:
            return list(self._tasks.values())

    def active_count(self) -> int:
        with self._lock:
            return sum(
                1 for t in self._tasks.values()
                if t.state in (TaskState.DOWNLOADING.value, TaskState.PROCESSING.value)
            )

    def shutdown(self) -> None:
        self._running = False
        self._save_pending()
        for _ in self._workers:
            self._queue.put(None)   # type: ignore[arg-type]
        for t in self._workers:
            t.join(timeout=3.0)


class _LocalHTTPServer(HTTPServer):
    # En Windows SO_REUSEADDR permitiría secuestrar el puerto de otra app.
    allow_reuse_address = sys.platform != "win32"


class LocalApiServer:
    """API HTTP local (solo 127.0.0.1) para encolar descargas desde scripts."""

    def __init__(self, app: "TinyDLPApp", port: int = 8484) -> None:
        self._app = app
        self._port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int:
        return self._port

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def start(self) -> bool:
        """Arranca el servidor; si el puerto está ocupado loguea y sigue sin API."""
        if self._port <= 0:
            logger.info("Local API: deshabilitada en config (api_port=0)")
            return False
        if self._server is not None:
            return True
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args) -> None:  # noqa: A003
                return

            def do_GET(self) -> None:  # noqa: N802
                api._handle(self)

        try:
            server = _LocalHTTPServer(("127.0.0.1", self._port), Handler)
        except OSError as exc:
            logger.warning(
                "Local API: puerto %d ocupado (%s); sigo sin API",
                self._port, exc,
            )
            return False
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.5},
            daemon=True, name="local-api",
        )
        self._thread.start()
        logger.info("Local API: %s (habilitada)", self.url)
        return True

    def stop(self) -> None:
        if self._server is None:
            return
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception as exc:
            logger.debug("Local API stop: %s", exc)
        self._server = None
        logger.info("Local API: detenida")

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        route = (parsed.path or "/").rstrip("/") or "/"
        params = parse_qs(parsed.query)
        try:
            if route == "/ping":
                self._send(handler, {"ok": True, "version": APP_VERSION})
            elif route == "/add":
                url = (params.get("url") or [""])[0].strip()
                if not url:
                    self._send(handler, {"status": "error", "error": "missing url"}, 400)
                    return
                kind = ((params.get("kind") or [""])[0] or "audio").strip().lower()
                if kind not in ("audio", "video", "both"):
                    kind = "audio"
                task_id = self._app.enqueue_url(url, kind)
                logger.info("API: /add %s (%s) → %s", url, kind, task_id)
                self._send(handler, {"status": "queued", "id": task_id or ""})
            elif route == "/status":
                tasks = [
                    {
                        "id": t.id,
                        "url": t.url,
                        "state": t.state,
                        "progress": round(t.progress, 4),
                        "title": t.title,
                    }
                    for t in self._app.queue.snapshot()
                ]
                self._send(handler, {
                    "ok": True,
                    "paused": self._app.queue.paused,
                    "tasks": tasks,
                })
            elif route == "/pause":
                self._app.queue.pause()
                self._send(handler, {"status": "paused"})
            elif route == "/resume":
                self._app.queue.resume()
                self._send(handler, {"status": "resumed"})
            else:
                self._send(handler, {"status": "error", "error": "not found"}, 404)
        except Exception as exc:
            logger.warning("API: error en %s: %s", handler.path, exc)
            try:
                self._send(handler, {"status": "error", "error": str(exc)[:120]}, 500)
            except Exception:
                pass

    def _send(self, handler: BaseHTTPRequestHandler, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(code)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(body)


# ============================================================
# 13. HOTKEY GLOBAL
# ============================================================

class HotkeyManager:
    def __init__(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        self._listener = None

    def start(self) -> None:
        if not PYNPUT_AVAILABLE:
            logger.info("pynput no disponible — hotkey desactivado")
            return
        if self._listener is not None:
            return
        try:
            combo = pynput_keyboard.HotKey.parse("<ctrl>+<alt>+d")
            hotkey = pynput_keyboard.HotKey(combo, self._on_hotkey)

            def for_canonical(f):
                return lambda k: f(self._listener.canonical(k))

            self._listener = pynput_keyboard.Listener(
                on_press=for_canonical(hotkey.press),
                on_release=for_canonical(hotkey.release),
            )
            self._listener.start()
            logger.info("Hotkey Ctrl+Alt+D registrado")
        except Exception as exc:
            logger.warning("No se pudo registrar hotkey: %s", exc)
            self._listener = None

    def _on_hotkey(self) -> None:
        try:
            self._callback()
        except Exception as exc:
            logger.error("Hotkey callback: %s", exc)

    def stop(self) -> None:
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None


# ============================================================
# 14. INTERCEPTOR DE PORTAPAPELES
# ============================================================

class ClipboardInterceptor:
    def __init__(
        self,
        config: Config,
        detector: UrlDetector,
        on_url: Callable[[str], None],
        on_multiple_urls: Callable[[list[str]], None] | None = None,
        on_protocol: Callable[[str, str], None] | None = None,
    ) -> None:
        self._config = config
        self._detector = detector
        self._on_url = on_url
        self._on_multiple_urls = on_multiple_urls
        self._on_protocol = on_protocol
        self._running = False
        self._paused = config.detection_paused
        self._thread: Optional[threading.Thread] = None
        self._last_text = ""
        self._seen: set[str] = set()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="clipboard")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def pause(self) -> None:
        self._paused = True
        self._config.detection_paused = True

    def resume(self) -> None:
        self._paused = False
        self._config.detection_paused = False

    @property
    def paused(self) -> bool:
        return self._paused

    def force_check(self) -> Optional[str]:
        """Fuerza una lectura del portapapeles (usado por el hotkey)."""
        try:
            text = pyperclip.paste()
            return self._detector.find(text) if text else None
        except Exception:
            return None

    def _loop(self) -> None:
        # Tick corto para no retrasar el IPC ni con poll_interval alto (máx. 0.5 s).
        last_clip = 0.0
        last_ipc = 0.0
        while self._running:
            now = time.monotonic()
            if now - last_ipc >= 0.5:
                last_ipc = now
                self._consume_ipc()
            if not self._paused and now - last_clip >= self._config.poll_interval:
                last_clip = now
                self._check_clipboard()
            time.sleep(0.25)

    def _check_clipboard(self) -> None:
        try:
            text = pyperclip.paste()
            if not text or text == self._last_text:
                return
            self._last_text = text
            urls = self._detector.find_all(text)
            if not urls:
                return
            fresh = [u for u in urls if u not in self._seen]
            if not fresh:
                return
            for url in fresh:
                self._seen.add(url)
            if len(self._seen) > 200:
                self._seen.clear()
                self._seen.update(fresh)
            if len(fresh) == 1:
                logger.info("URL detectada: %s", fresh[0])
                try:
                    self._on_url(fresh[0])
                except Exception as exc:
                    logger.exception("Callback URL: %s", exc)
            else:
                logger.info("%d URLs detectadas en el portapapeles", len(fresh))
                if self._on_multiple_urls is not None:
                    try:
                        self._on_multiple_urls(fresh)
                    except Exception as exc:
                        logger.exception("Callback multi-URL: %s", exc)
                else:
                    self._on_url(fresh[0])
        except Exception:
            pass

    def _consume_ipc(self) -> None:
        """Recoge tinydlp:// dejado por otra instancia; se llama cada 500 ms."""
        if not IPC_URL_FILE.exists():
            return
        try:
            data = json.loads(IPC_URL_FILE.read_text(encoding="utf-8"))
            IPC_URL_FILE.unlink(missing_ok=True)
            url = str(data.get("url") or "").strip()
            kind = str(data.get("kind") or "ask").strip().lower()
            if not url:
                return
            logger.info("IPC: URL recibida (%s)", kind)
            if self._on_protocol is not None:
                self._on_protocol(url, kind)
            else:
                self._on_url(url)
        except Exception as exc:
            logger.warning("IPC: no se pudo procesar: %s", exc)
            try:
                IPC_URL_FILE.unlink(missing_ok=True)
            except Exception:
                pass


# ============================================================
# 15. TRAY
# ============================================================

class TrayController:
    def __init__(self, app: "TinyDLPApp") -> None:
        self._app = app
        self._icon: Optional[pystray.Icon] = None
        self._last_update = 0.0

    def run(self) -> None:
        self._icon = pystray.Icon(
            APP_NAME, icon=make_tray_icon("idle"),
            title=APP_NAME, menu=self._build_menu(),
        )
        self._icon.run()

    def stop(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass

    def refresh(self) -> None:
        if not self._icon:
            return
        try:
            self._icon.menu = self._build_menu()
            self._update_icon_state()
        except Exception:
            pass

    def _update_icon_state(self) -> None:
        now = time.time()
        if now - self._last_update < 0.5:
            return
        self._last_update = now
        if not self._icon:
            return
        try:
            app = self._app
            active = app.queue.active_count()
            pending = app.pending_url is not None
            if app.interceptor.paused:
                state, title = "paused", "Tiny-dlp — detección pausada"
            elif active > 0:
                state = "downloading"
                dl = [t for t in app.queue.snapshot() if t.state in (
                    TaskState.DOWNLOADING.value, TaskState.PROCESSING.value,
                )]
                if dl:
                    t = dl[0]
                    title = f"⬇ {int(t.progress*100)}% — {(t.title or t.url)[:40]}"
                else:
                    title = f"⬇ {active} descargando"
            elif pending:
                state, title = "pending", "🔗 Enlace detectado"
            else:
                state, title = "idle", "Tiny-dlp — esperando enlaces"
            self._icon.icon = make_tray_icon(state)
            self._icon.title = title
        except Exception:
            pass

    def _audio_menu_items(self) -> list:
        app = self._app
        labels = (("MP3", "mp3"), ("M4A", "m4a"), ("FLAC", "flac"),
                  ("WAV", "wav"), ("OPUS", "opus"))
        out = [
            pystray.MenuItem(
                label,
                (lambda *_a, v=value: app.set_audio_format(v)),
                radio=True,
                checked=(lambda _item, v=value: app.config.audio_format == v),
            )
            for label, value in labels
        ]
        out.append(pystray.Menu.SEPARATOR)
        for kbps in ("128", "192", "320"):
            out.append(pystray.MenuItem(
                f"{kbps} kbps",
                (lambda *_a, v=kbps: app.set_audio_quality(v)),
                radio=True,
                checked=(lambda _item, v=kbps: app.config.audio_quality == v),
            ))
        return out

    def _video_menu_items(self) -> list:
        app = self._app
        labels = (("MP4", "mp4"), ("MKV", "mkv"), ("WEBM", "webm"))
        out = [
            pystray.MenuItem(
                label,
                (lambda *_a, v=value: app.set_video_format(v)),
                radio=True,
                checked=(lambda _item, v=value: app.config.video_format == v),
            )
            for label, value in labels
        ]
        out.append(pystray.Menu.SEPARATOR)
        res = (("720p", "720"), ("1080p", "1080"),
               ("4K", "4k"), ("Mejor", "best"))
        for label, value in res:
            out.append(pystray.MenuItem(
                label,
                (lambda *_a, v=value: app.set_video_quality(v)),
                radio=True,
                checked=(lambda _item, v=value: app.config.video_quality == v),
            ))
        return out

    def _build_menu(self) -> pystray.Menu:
        app = self._app
        items: list = []

        if app.pending_url:
            items.append(pystray.MenuItem(f"🔗 {app.pending_url[:45]}…", None, enabled=False))
            items.append(pystray.MenuItem("🎵 Solo audio", lambda: app.trigger_download("audio")))
            items.append(pystray.MenuItem("🎬 Vídeo", lambda: app.trigger_download("video")))
            items.append(pystray.MenuItem("🎵🎬 Ambos", lambda: app.trigger_download("both")))
            items.append(pystray.MenuItem("📋 Elegir items (playlist)", app.expand_and_choose))
            items.append(pystray.MenuItem("❌ Descartar", app.discard_pending))
            items.append(pystray.Menu.SEPARATOR)

        if app.interceptor.paused:
            items.append(pystray.MenuItem("▶ Reanudar detección", app.resume_detection))
        else:
            items.append(pystray.MenuItem("⏸ Pausar detección", app.pause_detection))

        recent = app.recent_urls.urls()
        recent_items: list = []
        for url in recent:
            recent_items.append(pystray.MenuItem(
                url[:48],
                lambda *_a, u=url: app.use_recent_url(u),
            ))
        if not recent_items:
            recent_items.append(pystray.MenuItem("(vacío)", None, enabled=False))
        items.append(pystray.MenuItem("🔗 Últimas URLs copiadas", pystray.Menu(*recent_items)))

        tasks = app.queue.snapshot()
        if tasks:
            items.append(pystray.Menu.SEPARATOR)
            items.append(pystray.MenuItem("Descargas", None, enabled=False))
            for t in tasks[:5]:
                items.append(pystray.MenuItem(
                    self._task_label(t),
                    (lambda tid=t.id: app.queue.cancel(tid)),
                ))
            if len(tasks) > 5:
                items.append(pystray.MenuItem(f"…y {len(tasks)-5} más", None, enabled=False))

        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("🖥️ Abrir panel", lambda: app.root.root.after(0, app.show_ui), default=True))
        items.append(pystray.MenuItem("📂 Carpeta descargas", app.open_download_dir))
        items.append(pystray.MenuItem("⚙️ Ajustes", lambda: app.root.root.after(0, app.root.open_settings)))
        items.append(pystray.MenuItem(
            f"🎯 Modo: { {'ask': 'Preguntar', 'audio': 'Audio', 'video': 'Vídeo', 'both': 'Ambos'}.get(app.config.mode, '?') }",
            app.cycle_mode,
        ))
        audio_detail = (
            f"{app.config.audio_quality}k"
            if app.config.audio_format in ("mp3", "m4a", "opus") else "lossless"
        )
        items.append(pystray.MenuItem(
            f"🎚️ Audio: {app.config.audio_format.upper()} {audio_detail}",
            pystray.Menu(*self._audio_menu_items()),
        ))
        items.append(pystray.MenuItem(
            f"📺 Vídeo: {app.config.video_format.upper()} {app.config.video_quality}",
            pystray.Menu(*self._video_menu_items()),
        ))
        items.append(pystray.MenuItem(
            f"💬 Subtítulos: {'ON' if app.config.subtitles else 'OFF'}",
            app.toggle_subtitles,
        ))
        items.append(pystray.MenuItem(
            f"⏭️ SponsorBlock: {'ON' if app.config.sponsorblock else 'OFF'}",
            app.toggle_sponsorblock,
        ))
        if app.queue.paused:
            items.append(pystray.MenuItem("▶ Reanudar descargas", app.queue.resume))
        else:
            items.append(pystray.MenuItem("⏸ Pausar descargas", app.queue.pause))
        items.append(pystray.MenuItem("⬇️ Actualizar yt-dlp", app.update_ytdlp))
        items.append(pystray.MenuItem(
            "🧩 Instalar dependencias", lambda: app.run_dependency_install(),
        ))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("❤️ Créditos", app.open_credits))
        items.append(pystray.MenuItem("❌ Salir", app.quit))
        return pystray.Menu(*items)

    def _task_label(self, t: DownloadTask) -> str:
        pct = int(t.progress * 100)
        name = (t.title or t.url)[:32]
        if t.state == TaskState.DOWNLOADING.value:
            speed = f" · {format_bytes(t.speed)}/s" if t.speed > 0 else ""
            eta = f" · ETA {format_eta(t.eta)}" if t.eta > 0 else ""
            return f"⬇ {pct}% {name}{speed}{eta}"
        if t.state == TaskState.PROCESSING.value:
            return f"⚙️ Procesando {name}"
        if t.state == TaskState.QUEUED.value:
            return f"⏳ {name}"
        return name


# ============================================================
# 16. MINI UI
# ============================================================

class MiniUI:
    AUDIO_FORMATS = ("MP3", "M4A", "FLAC", "WAV", "OPUS")
    VIDEO_FORMATS = ("MP4", "MKV", "WEBM")
    VIDEO_RES = {"720p": "720", "1080p": "1080", "4K": "4k", "Mejor": "best"}
    MODE_LABELS = {"ask": "Preguntar", "audio": "Audio",
                   "video": "Vídeo", "both": "Ambos"}

    def __init__(self, app: "TinyDLPApp") -> None:
        import customtkinter as ctk
        self._ctk = ctk
        self._app = app
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")

        self.root = ctk.CTk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(fg_color=COLOR_BG)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)

        self._W = 560
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self._H = min(740, max(560, screen_h - 68))
        x = app.config.window_x or (screen_w - self._W - 40)
        y = app.config.window_y or (screen_h - self._H - 40)
        x = max(8, min(x, screen_w - self._W - 8))
        y = max(8, min(y, screen_h - self._H - 48))
        self.root.geometry(f"{self._W}x{self._H}+{x}+{y}")

        self._settings_window = None
        self._playlist_window = None
        self._build_ui()
        self.root.bind("<Configure>", self._on_configure)
        self.root.bind("<Escape>", lambda e: self.hide())

    def _build_ui(self) -> None:
        ctk = self._ctk
        config = self._app.config

        # -------- Header --------
        header = ctk.CTkFrame(self.root, fg_color="transparent", height=54)
        header.pack(fill="x", padx=16, pady=(12, 4))
        header.pack_propagate(False)
        header.bind("<Button-1>", self._start_move)
        header.bind("<B1-Motion>", self._do_move)

        ctk.CTkLabel(
            header, text="Tiny-dlp", font=("Segoe UI", 19, "bold"),
            text_color=COLOR_TEXT, anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            header, text=f"v{APP_VERSION}", font=("Segoe UI", 9),
            text_color=COLOR_SUBTLE, fg_color=COLOR_SURFACE,
            corner_radius=6, width=46, height=18,
        ).pack(side="left", padx=(8, 0))
        self.lbl_status = ctk.CTkLabel(
            header, text="● Activa", font=("Segoe UI", 10),
            text_color=COLOR_ACCENT,
        )
        self.lbl_status.pack(side="left", padx=(12, 0))

        ctk.CTkButton(
            header, text="✕", width=32, height=30,
            fg_color="transparent", hover_color="#3A1F26",
            text_color=COLOR_SUBTLE, font=("Segoe UI", 12),
            command=self.hide,
        ).pack(side="right", padx=(4, 0))
        ctk.CTkButton(
            header, text="⚙️", width=32, height=30,
            fg_color="transparent", hover_color=COLOR_HOVER,
            text_color=COLOR_SUBTLE, font=("Segoe UI", 12),
            command=self.open_settings,
        ).pack(side="right")

        # -------- Modo (segmentado, sin ciclos) --------
        mode_row = ctk.CTkFrame(self.root, fg_color="transparent")
        mode_row.pack(fill="x", padx=16, pady=(2, 6))
        ctk.CTkLabel(
            mode_row, text="Modo", font=("Segoe UI", 10, "bold"),
            text_color=COLOR_SUBTLE, width=42, anchor="w",
        ).pack(side="left")
        self.mode_var = ctk.StringVar(
            value=self.MODE_LABELS.get(config.mode, "Preguntar"),
        )
        self.mode_seg = ctk.CTkSegmentedButton(
            mode_row, values=list(self.MODE_LABELS.values()),
            variable=self.mode_var, command=self._on_mode_selected,
            height=30, font=("Segoe UI", 11),
            selected_color=COLOR_ACCENT, selected_hover_color="#17A845",
            unselected_color=COLOR_SURFACE, unselected_hover_color=COLOR_HOVER,
            text_color=COLOR_TEXT,
        )
        self.mode_seg.pack(side="left", fill="x", expand=True)

        # -------- Formato y calidad --------
        prefs = ctk.CTkFrame(self.root, fg_color=COLOR_SURFACE, corner_radius=12)
        prefs.pack(fill="x", padx=16, pady=(0, 6))

        prefs_head = ctk.CTkFrame(prefs, fg_color="transparent")
        prefs_head.pack(fill="x", padx=12, pady=(8, 0))
        ctk.CTkLabel(
            prefs_head, text="Formato y calidad",
            font=("Segoe UI", 11, "bold"), text_color=COLOR_TEXT,
        ).pack(side="left")
        ctk.CTkLabel(
            prefs_head, text="se guarda al instante",
            font=("Segoe UI", 9), text_color=COLOR_SUBTLE,
        ).pack(side="right")

        grid = ctk.CTkFrame(prefs, fg_color="transparent")
        grid.pack(fill="x", padx=10, pady=(4, 8))

        menu_style = dict(
            height=28, font=("Segoe UI", 10), corner_radius=7,
            fg_color=COLOR_HOVER, button_color=COLOR_HOVER,
            button_hover_color="#3A3A3A", text_color=COLOR_TEXT,
            dropdown_fg_color=COLOR_SURFACE,
            dropdown_hover_color=COLOR_HOVER,
            dropdown_text_color=COLOR_TEXT,
        )

        ctk.CTkLabel(
            grid, text="Audio", width=58, anchor="w",
            font=("Segoe UI", 10), text_color=COLOR_SUBTLE,
        ).grid(row=0, column=0, sticky="w", padx=(2, 6), pady=3)
        self._audio_fmt_var = ctk.StringVar(value=config.audio_format.upper())
        self.audio_fmt_menu = ctk.CTkOptionMenu(
            grid, values=list(self.AUDIO_FORMATS),
            variable=self._audio_fmt_var, command=self._on_audio_format,
            width=92, **menu_style,
        )
        self.audio_fmt_menu.grid(row=0, column=1, sticky="w", pady=3)

        audio_lossless = config.audio_format in ("flac", "wav")
        self._audio_kbps_var = ctk.StringVar(value=f"{config.audio_quality} kbps")
        self.audio_kbps_menu = ctk.CTkOptionMenu(
            grid, values=["128 kbps", "192 kbps", "320 kbps"],
            variable=self._audio_kbps_var, command=self._on_audio_kbps,
            width=112, **menu_style,
        )
        self.audio_kbps_menu.grid(row=0, column=2, sticky="e", pady=3)
        if audio_lossless:
            self.audio_kbps_menu.configure(state="disabled")

        grid.grid_columnconfigure(1, weight=1)
        grid.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(
            grid, text="Vídeo", width=58, anchor="w",
            font=("Segoe UI", 10), text_color=COLOR_SUBTLE,
        ).grid(row=1, column=0, sticky="w", padx=(2, 6), pady=3)
        self._video_fmt_var = ctk.StringVar(value=config.video_format.upper())
        self.video_fmt_menu = ctk.CTkOptionMenu(
            grid, values=list(self.VIDEO_FORMATS),
            variable=self._video_fmt_var, command=self._on_video_format,
            width=92, **menu_style,
        )
        self.video_fmt_menu.grid(row=1, column=1, sticky="w", pady=3)
        res_label = next(
            (label for label, value in self.VIDEO_RES.items()
             if value == config.video_quality),
            "Mejor",
        )
        self._video_res_var = ctk.StringVar(value=res_label)
        self.video_res_menu = ctk.CTkOptionMenu(
            grid, values=list(self.VIDEO_RES.keys()),
            variable=self._video_res_var, command=self._on_video_res,
            width=112, **menu_style,
        )
        self.video_res_menu.grid(row=1, column=2, sticky="e", pady=3)

        # -------- Pausas rápidas --------
        quick = ctk.CTkFrame(self.root, fg_color="transparent")
        quick.pack(fill="x", padx=16, pady=(0, 4))
        self.btn_detect = ctk.CTkButton(
            quick, text="Pausar detección", height=30,
            fg_color=COLOR_SURFACE, hover_color=COLOR_HOVER,
            text_color=COLOR_TEXT, font=("Segoe UI", 10, "bold"),
            corner_radius=8, command=self._toggle_detection,
        )
        self.btn_detect.pack(side="left", expand=True, fill="x", padx=(0, 3))
        self.btn_queue = ctk.CTkButton(
            quick, text="Pausar descargas", height=30,
            fg_color=COLOR_SURFACE, hover_color=COLOR_HOVER,
            text_color=COLOR_TEXT, font=("Segoe UI", 10, "bold"),
            corner_radius=8, command=self._toggle_queue,
        )
        self.btn_queue.pack(side="left", expand=True, fill="x", padx=(3, 0))

        # -------- Body --------
        self.body = ctk.CTkScrollableFrame(
            self.root, fg_color="transparent", corner_radius=0,
        )
        self.body.pack(fill="both", expand=True, padx=16, pady=(4, 6))

        # Pending card
        self.pending_frame = ctk.CTkFrame(
            self.body, fg_color=COLOR_SURFACE, corner_radius=12,
            border_width=1, border_color="#2E5A3A",
        )
        pending_head = ctk.CTkFrame(self.pending_frame, fg_color="transparent")
        pending_head.pack(fill="x", padx=12, pady=(10, 0))
        ctk.CTkLabel(
            pending_head, text="Enlace detectado",
            font=("Segoe UI", 11, "bold"), text_color=COLOR_ACCENT,
        ).pack(side="left")
        ctk.CTkLabel(
            pending_head, text="elige arriba el formato de salida",
            font=("Segoe UI", 9), text_color=COLOR_SUBTLE,
        ).pack(side="right")
        self.pending_label = ctk.CTkLabel(
            self.pending_frame, text="", font=("Segoe UI", 10),
            text_color=COLOR_TEXT, anchor="w", wraplength=480, justify="left",
        )
        self.pending_label.pack(fill="x", padx=12, pady=(2, 8))

        self.pending_actions = ctk.CTkFrame(
            self.pending_frame, fg_color="transparent",
        )
        self.pending_actions.pack(fill="x", padx=10, pady=(0, 10))
        self.pending_actions.grid_columnconfigure((0, 1, 2), weight=1)

        self.btn_pending_audio = ctk.CTkButton(
            self.pending_actions, text="Audio", height=34,
            fg_color=COLOR_ACCENT, hover_color="#17A845",
            text_color="#04140A", font=("Segoe UI", 10, "bold"),
            corner_radius=8, command=lambda: self._pending_action("audio"),
        )
        self.btn_pending_audio.grid(row=0, column=0, sticky="ew", padx=3, pady=2)
        self.btn_pending_video = ctk.CTkButton(
            self.pending_actions, text="Vídeo", height=34,
            fg_color=COLOR_WARN, hover_color="#E08A2E",
            text_color="#1A1000", font=("Segoe UI", 10, "bold"),
            corner_radius=8, command=lambda: self._pending_action("video"),
        )
        self.btn_pending_video.grid(row=0, column=1, sticky="ew", padx=3, pady=2)
        ctk.CTkButton(
            self.pending_actions, text="Ambos", height=34,
            fg_color=COLOR_INFO, hover_color="#2F6FD0",
            text_color="#FFFFFF", font=("Segoe UI", 10, "bold"),
            corner_radius=8, command=lambda: self._pending_action("both"),
        ).grid(row=0, column=2, sticky="ew", padx=3, pady=2)
        ctk.CTkButton(
            self.pending_actions, text="Elegir items de playlist", height=30,
            fg_color=COLOR_HOVER, hover_color="#3A3A3A",
            text_color=COLOR_TEXT, font=("Segoe UI", 10),
            corner_radius=8, command=lambda: self._pending_action("playlist"),
        ).grid(row=1, column=0, columnspan=2, sticky="ew", padx=3, pady=2)
        ctk.CTkButton(
            self.pending_actions, text="✕ Descartar", height=30,
            fg_color="transparent", hover_color="#3A1F26",
            text_color=COLOR_SUBTLE, font=("Segoe UI", 10),
            corner_radius=8, command=lambda: self._pending_action("discard"),
        ).grid(row=1, column=2, sticky="ew", padx=3, pady=2)

        self.pending_summary = ctk.CTkLabel(
            self.pending_frame, text="", anchor="w",
            font=("Segoe UI", 9), text_color=COLOR_SUBTLE,
            wraplength=490, justify="left",
        )
        self.pending_summary.pack(fill="x", padx=13, pady=(2, 9))

        # Downloads
        self.dl_section = ctk.CTkFrame(self.body, fg_color="transparent")
        self.dl_section.pack(fill="x", pady=(6, 0))
        dl_head = ctk.CTkFrame(self.dl_section, fg_color="transparent")
        dl_head.pack(fill="x", padx=2)
        ctk.CTkLabel(
            dl_head, text="Descargas activas", font=("Segoe UI", 12, "bold"),
            text_color=COLOR_TEXT, anchor="w",
        ).pack(side="left")
        self.lbl_dl_count = ctk.CTkLabel(
            dl_head, text="0", font=("Segoe UI", 10),
            text_color=COLOR_SUBTLE, anchor="e",
        )
        self.lbl_dl_count.pack(side="right")
        self.dl_list = ctk.CTkFrame(self.dl_section, fg_color="transparent")
        self.dl_list.pack(fill="x", pady=(4, 0))

        # History
        self.hist_section = ctk.CTkFrame(self.body, fg_color="transparent")
        self.hist_section.pack(fill="x", pady=(10, 0))
        hist_head = ctk.CTkFrame(self.hist_section, fg_color="transparent")
        hist_head.pack(fill="x", padx=2)
        ctk.CTkLabel(
            hist_head, text="Historial reciente", font=("Segoe UI", 12, "bold"),
            text_color=COLOR_TEXT, anchor="w",
        ).pack(side="left")
        ctk.CTkButton(
            hist_head, text="Limpiar", width=66, height=22,
            fg_color="transparent", hover_color=COLOR_HOVER,
            text_color=COLOR_SUBTLE, font=("Segoe UI", 9),
            command=self._clear_history,
        ).pack(side="right")
        self.hist_list = ctk.CTkFrame(self.hist_section, fg_color="transparent")
        self.hist_list.pack(fill="x", pady=(4, 0))

        # Footer
        footer = ctk.CTkFrame(self.root, fg_color=COLOR_SURFACE, corner_radius=12)
        footer.pack(fill="x", padx=16, pady=(0, 12))
        for label, cmd in (
            ("Carpeta", self._app.open_download_dir),
            ("Ajustes", self.open_settings),
            ("Créditos", self._app.open_credits),
        ):
            ctk.CTkButton(
                footer, text=label, height=30,
                fg_color="transparent", hover_color=COLOR_HOVER,
                text_color=COLOR_TEXT, font=("Segoe UI", 10),
                corner_radius=8, command=cmd,
            ).pack(side="left", expand=True, fill="x", padx=3, pady=5)

        self.refresh()

    def _start_move(self, event) -> None:
        self._drag_x, self._drag_y = event.x, event.y

    def _do_move(self, event) -> None:
        nx = self.root.winfo_x() + (event.x - self._drag_x)
        ny = self.root.winfo_y() + (event.y - self._drag_y)
        self.root.geometry(f"+{nx}+{ny}")

    def _on_configure(self, event) -> None:
        if event.widget is self.root:
            self._app.config.window_x = self.root.winfo_x()
            self._app.config.window_y = self.root.winfo_y()

    def _pending_action(self, kind: str) -> None:
        if kind == "discard":
            self._app.discard_pending()
        elif kind == "playlist":
            self._app.expand_and_choose()
        else:
            self._app.trigger_download(kind)

    def _toggle_detection(self) -> None:
        if self._app.interceptor.paused:
            self._app.resume_detection()
        else:
            self._app.pause_detection()
        self.refresh()

    def _toggle_queue(self) -> None:
        if self._app.queue.paused:
            self._app.queue.resume()
        else:
            self._app.queue.pause()
        self.refresh()

    def _on_mode_selected(self, value: str) -> None:
        mode = next(
            (key for key, label in self.MODE_LABELS.items() if label == value),
            "ask",
        )
        self.mode_var.set(value)
        self._app.config.mode = mode
        self._app.config.save()
        logger.info("Modo de descarga: %s", mode)
        self._app._safe_tray()

    def _on_audio_format(self, value: str) -> None:
        fmt = value.lower()
        self._app.config.audio_format = fmt
        self._app.config.save()
        lossless = fmt in ("flac", "wav")
        self.audio_kbps_menu.configure(state="disabled" if lossless else "normal")
        logger.info("Formato de audio: %s", fmt)
        self._app._safe_tray()
        self.refresh()

    def _on_audio_kbps(self, value: str) -> None:
        self._app.config.audio_quality = value.split()[0]
        self._app.config.save()
        logger.info("Calidad de audio: %s kbps", self._app.config.audio_quality)
        self._app._safe_tray()

    def _on_video_format(self, value: str) -> None:
        self._app.config.video_format = value.lower()
        self._app.config.save()
        logger.info("Formato de vídeo: %s", self._app.config.video_format)
        self._app._safe_tray()
        self.refresh()

    def _on_video_res(self, value: str) -> None:
        self._app.config.video_quality = self.VIDEO_RES.get(value, "best")
        self._app.config.save()
        logger.info("Calidad de vídeo: %s", self._app.config.video_quality)
        self._app._safe_tray()

    def _sync_prefs(self) -> None:
        """Refleja en los selectores cambios hechos desde Ajustes o el tray."""
        c = self._app.config
        self.mode_var.set(self.MODE_LABELS.get(c.mode, "Preguntar"))
        self._audio_fmt_var.set(c.audio_format.upper())
        self._audio_kbps_var.set(f"{c.audio_quality} kbps")
        self.audio_kbps_menu.configure(
            state="disabled" if c.audio_format in ("flac", "wav") else "normal",
        )
        self._video_fmt_var.set(c.video_format.upper())
        res_label = next(
            (label for label, value in self.VIDEO_RES.items()
             if value == c.video_quality),
            "Mejor",
        )
        self._video_res_var.set(res_label)

    def _clear_history(self) -> None:
        self._app.history.clear()
        self.refresh()

    def _retry_history(self, entry: dict) -> None:
        url = entry.get("url", "")
        if not url:
            return
        self._app.enqueue_url(url, entry.get("kind") or "audio")
        self.refresh()

    # -------- Ajustes --------

    def open_settings(self) -> None:
        if self._settings_window is not None:
            try:
                self._settings_window.lift()
                return
            except Exception:
                self._settings_window = None
        self._settings_window = SettingsWindow(self._app, on_close=self._on_settings_close)
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass

    def _on_settings_close(self) -> None:
        self._settings_window = None
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        self.refresh()

    # -------- Refresco --------

    def refresh(self) -> None:
        ctk = self._ctk
        app = self._app
        c = app.config
        if app.interceptor.paused:
            self.lbl_status.configure(text="● Pausada", text_color=COLOR_WARN)
            self.btn_detect.configure(text="Reanudar detección")
        else:
            self.lbl_status.configure(text="● Activa", text_color=COLOR_ACCENT)
            self.btn_detect.configure(text="Pausar detección")
        self.btn_queue.configure(
            text="Reanudar descargas" if app.queue.paused else "Pausar descargas",
        )
        try:
            snapshot = (
                c.mode, c.audio_format, c.audio_quality,
                c.video_format, c.video_quality,
            )
            if snapshot != getattr(self, "_prefs_snapshot", None):
                self._sync_prefs()
                self._prefs_snapshot = snapshot
        except Exception:
            pass

        audio_detail = (
            f"{c.audio_quality}k"
            if c.audio_format in ("mp3", "m4a", "opus") else "lossless"
        )
        self.btn_pending_audio.configure(
            text=f"Audio · {c.audio_format.upper()} {audio_detail}",
        )
        video_res_label = next(
            (label for label, value in self.VIDEO_RES.items()
             if value == c.video_quality),
            "Mejor",
        )
        self.btn_pending_video.configure(
            text=f"Vídeo · {c.video_format.upper()} {video_res_label}",
        )
        self.pending_summary.configure(
            text=(
                f"«Ambos» descarga audio {c.audio_format.upper()} {audio_detail} "
                f"+ vídeo {c.video_format.upper()} hasta {video_res_label}."
            ),
        )

        if app.pending_url:
            self.pending_label.configure(text=app.pending_url[:110])
            if not self.pending_frame.winfo_ismapped():
                self.pending_frame.pack(
                    fill="x", padx=0, pady=(0, 8), before=self.dl_section,
                )
        else:
            self.pending_frame.pack_forget()

        for w in self.dl_list.winfo_children():
            w.destroy()
        order = {
            TaskState.DOWNLOADING.value: 0,
            TaskState.PROCESSING.value: 1,
            TaskState.QUEUED.value: 2,
        }
        tasks = sorted(
            app.queue.snapshot(),
            key=lambda t: (order.get(t.state, 3), t.created_at),
        )
        self.lbl_dl_count.configure(text=str(len(tasks)))
        if not tasks:
            ctk.CTkLabel(
                self.dl_list, text="Sin descargas en curso — copia un enlace para empezar",
                text_color=COLOR_SUBTLE, font=("Segoe UI", 10),
            ).pack(fill="x", padx=6, pady=10)
        else:
            for t in tasks:
                self._render_task(t)

        for w in self.hist_list.winfo_children():
            w.destroy()
        recent = app.history.recent(10)
        if not recent:
            ctk.CTkLabel(
                self.hist_list, text="Sin historial todavía",
                text_color=COLOR_SUBTLE, font=("Segoe UI", 10),
            ).pack(fill="x", padx=6, pady=10)
        else:
            for entry in recent:
                self._render_history(entry)

    def _render_task(self, t: DownloadTask) -> None:
        ctk = self._ctk
        card = ctk.CTkFrame(self.dl_list, fg_color=COLOR_SURFACE, corner_radius=10)
        card.pack(fill="x", pady=3)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(
            top, text=(t.title or t.url)[:52], anchor="w",
            font=("Segoe UI", 10, "bold"), text_color=COLOR_TEXT,
        ).pack(side="left", fill="x", expand=True)

        state_text = {
            TaskState.DOWNLOADING.value: f"{int(t.progress * 100)}%",
            TaskState.PROCESSING.value: "ffmpeg",
            TaskState.QUEUED.value: "En cola",
            TaskState.ERROR.value: "Error",
        }.get(t.state, t.state)
        state_color = {
            TaskState.DOWNLOADING.value: COLOR_ACCENT,
            TaskState.PROCESSING.value: COLOR_INFO,
            TaskState.QUEUED.value: COLOR_SUBTLE,
            TaskState.ERROR.value: COLOR_ERROR,
        }.get(t.state, COLOR_SUBTLE)
        ctk.CTkLabel(
            top, text=state_text, font=("Segoe UI", 9, "bold"),
            text_color=state_color, width=52, anchor="e",
        ).pack(side="left", padx=(6, 4))

        ctk.CTkButton(
            top, text="✕", width=22, height=22,
            fg_color="transparent", hover_color="#3A1F26",
            text_color=COLOR_SUBTLE, font=("Segoe UI", 9),
            command=lambda: self._app.queue.cancel(t.id),
        ).pack(side="right")

        if t.state == TaskState.DOWNLOADING.value:
            sub = f"{format_bytes(t.downloaded_bytes)} / {format_bytes(t.total_bytes)}"
            if t.speed > 0:
                sub += f"  ·  {format_bytes(t.speed)}/s"
            if t.eta > 0:
                sub += f"  ·  ETA {format_eta(t.eta)}"
        elif t.state == TaskState.PROCESSING.value:
            sub = "Procesando con ffmpeg (metadata, portada…)"
        elif t.state == TaskState.ERROR.value:
            sub = t.error or "Error desconocido"
        else:
            sub = "En espera en la cola"
        ctk.CTkLabel(
            card, text=sub, anchor="w", font=("Segoe UI", 9),
            text_color=COLOR_SUBTLE,
        ).pack(fill="x", padx=10)

        bar = ctk.CTkProgressBar(
            card, height=5, corner_radius=3,
            progress_color=state_color, fg_color=COLOR_HOVER,
        )
        bar.set(max(0.0, min(1.0, t.progress)))
        bar.pack(fill="x", padx=10, pady=(4, 9))

    def _render_history(self, entry: dict) -> None:
        ctk = self._ctk
        state = entry.get("state", "")
        icon = {"done": "✓", "error": "✗", "cancelled": "⊘"}.get(state, "•")
        color = {"done": COLOR_ACCENT, "error": COLOR_ERROR,
                 "cancelled": COLOR_SUBTLE}.get(state, COLOR_SUBTLE)
        row = ctk.CTkFrame(self.hist_list, fg_color="transparent")
        row.pack(fill="x", pady=1)
        ctk.CTkLabel(
            row, text=icon, width=18, font=("Segoe UI", 11, "bold"), text_color=color,
        ).pack(side="left")
        path = entry.get("output_path", "")
        if path:
            ctk.CTkButton(
                row, text="Abrir", width=42, height=22,
                fg_color="transparent", hover_color=COLOR_HOVER,
                text_color=COLOR_SUBTLE, font=("Segoe UI", 9),
                command=lambda p=path: self._app.open_path(p),
            ).pack(side="right")
        if entry.get("url"):
            ctk.CTkButton(
                row, text="Repetir", width=52, height=22,
                fg_color="transparent", hover_color=COLOR_HOVER,
                text_color=COLOR_SUBTLE, font=("Segoe UI", 9),
                command=lambda e=entry: self._retry_history(e),
            ).pack(side="right")
        ctk.CTkLabel(
            row, text=(entry.get("title") or entry.get("url", ""))[:48], anchor="w",
            font=("Segoe UI", 10), text_color=COLOR_TEXT,
        ).pack(side="left", fill="x", expand=True, padx=(2, 0))

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.refresh()

    def hide(self) -> None:
        self.root.withdraw()

    def destroy(self) -> None:
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass


# ============================================================
# 17. VENTANA DE AJUSTES
# ============================================================

class SettingsWindow:
    def __init__(self, app: "TinyDLPApp", on_close: Optional[Callable] = None) -> None:
        import customtkinter as ctk
        self._ctk = ctk
        self._app = app
        self._on_close = on_close

        self.root = ctk.CTkToplevel()
        self.root.title("Ajustes — Tiny-dlp")
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        w = 520
        h = max(520, min(680, screen_h - 96))
        x = max(8, (screen_w - w) // 2)
        y = max(8, min(48, screen_h - h - 48))
        try:
            panel_x = app.root.root.winfo_x()
            if panel_x > w:
                x = max(8, min(x, panel_x - w - 12))
        except Exception:
            pass
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.configure(fg_color=COLOR_BG)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

        self._build()

    def _build(self) -> None:
        ctk = self._ctk
        c = self._app.config

        head = ctk.CTkFrame(self.root, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(14, 6))
        ctk.CTkLabel(
            head, text="Ajustes", font=("Segoe UI", 19, "bold"),
            text_color=COLOR_TEXT, anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            head, text=f"Tiny-dlp v{APP_VERSION}", font=("Segoe UI", 9),
            text_color=COLOR_SUBTLE,
        ).pack(side="right")

        self.tabs = ctk.CTkTabview(
            self.root, fg_color=COLOR_SURFACE, height=520,
            segmented_button_selected_color=COLOR_ACCENT,
            segmented_button_selected_hover_color="#17A845",
            segmented_button_unselected_color=COLOR_SURFACE,
            segmented_button_unselected_hover_color=COLOR_HOVER,
            text_color=COLOR_TEXT, corner_radius=12,
        )
        self.tabs.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        def build_tab(name: str, pad: int = 0):
            tab = self.tabs.add(name)
            scroll = ctk.CTkScrollableFrame(
                tab, fg_color="transparent", corner_radius=0,
            )
            scroll.pack(fill="both", expand=True, padx=pad, pady=0)
            return scroll

        def section(parent, title: str) -> None:
            ctk.CTkLabel(
                parent, text=title, font=("Segoe UI", 11, "bold"),
                text_color=COLOR_ACCENT, anchor="w",
            ).pack(fill="x", pady=(12, 4))

        def hint(parent, text: str) -> None:
            ctk.CTkLabel(
                parent, text=text, font=("Segoe UI", 9),
                text_color=COLOR_SUBTLE, anchor="w",
            ).pack(fill="x")

        # ================= Descargas =================
        dl = build_tab("Descargas", pad=2)

        section(dl, "Carpeta de destino")
        dir_row = ctk.CTkFrame(dl, fg_color="transparent")
        dir_row.pack(fill="x", pady=2)
        self._lbl_dir = ctk.CTkLabel(
            dir_row, text=c.download_dir[:44] + ("…" if len(c.download_dir) > 44 else ""),
            anchor="w", font=("Segoe UI", 10), text_color=COLOR_SUBTLE,
        )
        self._lbl_dir.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            dir_row, text="Cambiar", width=78, height=28,
            font=("Segoe UI", 10), command=self._pick_dir,
        ).pack(side="right")

        section(dl, "Organización")
        self._organize_var = ctk.BooleanVar(value=c.auto_organize)
        ctk.CTkCheckBox(
            dl, text="Organizar por tipo: Música/ y Vídeos/ (con artista)",
            variable=self._organize_var, command=self._on_organize,
            font=("Segoe UI", 10),
        ).pack(fill="x", pady=4)
        self._auto_open_var = ctk.BooleanVar(value=c.auto_open_folder)
        ctk.CTkCheckBox(
            dl, text="Abrir la carpeta al terminar cada descarga",
            variable=self._auto_open_var, command=self._on_auto_open,
            font=("Segoe UI", 10),
        ).pack(fill="x", pady=4)

        section(dl, "Rendimiento")
        hint(dl, "Descargas simultáneas (requiere reinicio)")
        self._workers_var = ctk.IntVar(value=c.max_concurrent)
        ctk.CTkSlider(
            dl, from_=1, to=5, number_of_steps=4,
            variable=self._workers_var, command=self._on_workers,
        ).pack(fill="x", pady=(6, 2))
        self._lbl_workers = ctk.CTkLabel(
            dl, text=f"{c.max_concurrent} workers",
            font=("Segoe UI", 9), text_color=COLOR_SUBTLE, anchor="w",
        )
        self._lbl_workers.pack(fill="x")

        section(dl, "Detección del portapapeles")
        hint(dl, "Intervalo de escaneo (segundos)")
        self._poll_var = ctk.DoubleVar(value=c.poll_interval)
        ctk.CTkSlider(
            dl, from_=0.3, to=2.0, number_of_steps=17,
            variable=self._poll_var, command=self._on_poll,
        ).pack(fill="x", pady=(6, 2))
        self._lbl_poll = ctk.CTkLabel(
            dl, text=f"{c.poll_interval:.1f} s",
            font=("Segoe UI", 9), text_color=COLOR_SUBTLE, anchor="w",
        )
        self._lbl_poll.pack(fill="x")

        # ================= Calidad =================
        cal = build_tab("Calidad", pad=2)

        section(cal, "Audio")
        hint(cal, "Formato de salida")
        self._audio_fmt_var = ctk.StringVar(value=c.audio_format.upper())
        self._audio_fmt_seg = ctk.CTkSegmentedButton(
            cal, values=["MP3", "M4A", "FLAC", "WAV", "OPUS"],
            variable=self._audio_fmt_var, command=self._on_audio_format,
            font=("Segoe UI", 10),
        )
        self._audio_fmt_seg.pack(fill="x", pady=(2, 8))

        hint(cal, "Bitrate para MP3 / M4A / OPUS")
        self._audio_var = ctk.StringVar(value=c.audio_quality)
        self._audio_kbps_seg = ctk.CTkSegmentedButton(
            cal, values=["128", "192", "320"], variable=self._audio_var,
            command=self._on_audio_quality, font=("Segoe UI", 10),
        )
        self._audio_kbps_seg.pack(fill="x", pady=(2, 4))
        if c.audio_format in ("flac", "wav"):
            self._audio_kbps_seg.configure(state="disabled")

        self._metadata_var = ctk.BooleanVar(value=c.embed_metadata)
        ctk.CTkCheckBox(
            cal, text="Embeber thumbnail y metadata en audio",
            variable=self._metadata_var, command=self._on_metadata,
            font=("Segoe UI", 10),
        ).pack(fill="x", pady=(6, 4))

        section(cal, "Vídeo")
        hint(cal, "Contenedor de salida")
        self._video_fmt_var = ctk.StringVar(value=c.video_format.upper())
        ctk.CTkSegmentedButton(
            cal, values=["MP4", "MKV", "WEBM"], variable=self._video_fmt_var,
            command=self._on_video_format, font=("Segoe UI", 10),
        ).pack(fill="x", pady=(2, 8))

        hint(cal, "Resolución máxima")
        res_label = {"720": "720p", "1080": "1080p", "4k": "4K", "best": "Mejor"}.get(
            c.video_quality, "Mejor",
        )
        self._video_var = ctk.StringVar(value=res_label)
        ctk.CTkSegmentedButton(
            cal, values=["720p", "1080p", "4K", "Mejor"], variable=self._video_var,
            command=self._on_video_quality, font=("Segoe UI", 10),
        ).pack(fill="x", pady=(2, 4))

        section(cal, "Subtítulos")
        self._subs_var = ctk.BooleanVar(value=c.subtitles)
        ctk.CTkCheckBox(
            cal, text="Descargar subtítulos automáticos y embeberlos",
            variable=self._subs_var, command=self._on_subs,
            font=("Segoe UI", 10),
        ).pack(fill="x", pady=4)
        lang_row = ctk.CTkFrame(cal, fg_color="transparent")
        lang_row.pack(fill="x", pady=4)
        ctk.CTkLabel(
            lang_row, text="Idiomas:", font=("Segoe UI", 10), text_color=COLOR_SUBTLE,
        ).pack(side="left")
        self._lang_entry = ctk.CTkEntry(
            lang_row, height=28, placeholder_text="es,en",
        )
        self._lang_entry.insert(0, c.subtitle_langs)
        self._lang_entry.pack(side="left", fill="x", expand=True, padx=8)
        self._lang_entry.bind("<KeyRelease>", self._on_langs)

        section(cal, "SponsorBlock")
        self._sb_var = ctk.BooleanVar(value=c.sponsorblock)
        ctk.CTkCheckBox(
            cal, text="Saltar patrocinios, autopromos e interacciones",
            variable=self._sb_var, command=self._on_sb,
            font=("Segoe UI", 10),
        ).pack(fill="x", pady=4)

        self._quality_summary = ctk.CTkLabel(
            cal, text="", anchor="w", font=("Segoe UI", 9, "bold"),
            text_color=COLOR_ACCENT, wraplength=420, justify="left",
        )
        self._quality_summary.pack(fill="x", pady=(12, 4))
        self._update_quality_summary()

        # ================= Red =================
        net = build_tab("Red", pad=2)

        section(net, "Cookies desde navegador")
        hint(net, "Necesario para contenido con inicio de sesión o edad restringida")
        cookie_labels = {
            "No usar": "", "chrome": "chrome", "firefox": "firefox",
            "edge": "edge", "brave": "brave",
        }
        current_cookie = next(
            (label for label, value in cookie_labels.items() if value == c.cookies_browser),
            "No usar",
        )
        self._cookie_var = ctk.StringVar(value=current_cookie)
        ctk.CTkOptionMenu(
            net, values=list(cookie_labels.keys()), variable=self._cookie_var,
            command=self._on_cookies, height=30, font=("Segoe UI", 10),
            fg_color=COLOR_HOVER, button_color=COLOR_HOVER,
            dropdown_fg_color=COLOR_SURFACE,
        ).pack(fill="x", pady=(4, 4))

        section(net, "Límite de velocidad")
        self._rate_entry = ctk.CTkEntry(
            net, height=30,
            placeholder_text="Ej: 500K, 2M. Vacío = sin límite",
        )
        if c.rate_limit:
            self._rate_entry.insert(0, c.rate_limit)
        self._rate_entry.pack(fill="x", pady=4)
        self._rate_entry.bind("<KeyRelease>", self._on_rate_limit)

        section(net, "API local")
        self._api_var = ctk.BooleanVar(value=c.api_port > 0)
        ctk.CTkCheckBox(
            net, text="Activar API en 127.0.0.1 (solo loopback)",
            variable=self._api_var, command=self._on_api_toggle,
            font=("Segoe UI", 10),
        ).pack(fill="x", pady=4)
        api_row = ctk.CTkFrame(net, fg_color="transparent")
        api_row.pack(fill="x", pady=4)
        ctk.CTkLabel(
            api_row, text="Puerto:", font=("Segoe UI", 10), text_color=COLOR_SUBTLE,
        ).pack(side="left")
        self._api_port_entry = ctk.CTkEntry(api_row, width=90, height=28)
        self._api_port_entry.insert(0, str(c.api_port or 8484))
        self._api_port_entry.pack(side="left", padx=8)
        ctk.CTkButton(
            api_row, text="Aplicar", width=78, height=28,
            font=("Segoe UI", 10), command=self._apply_api_port,
        ).pack(side="left")
        hint(net, "Endpoints: /add  /status  /pause  /resume  /ping")

        section(net, "Protocolo tinydlp://")
        proto_row = ctk.CTkFrame(net, fg_color="transparent")
        proto_row.pack(fill="x", pady=4)
        ctk.CTkButton(
            proto_row, text="Registrar manejador de URLs", width=190, height=30,
            font=("Segoe UI", 10), command=self._register_protocol,
        ).pack(side="left")
        self._lbl_proto = ctk.CTkLabel(
            proto_row,
            text="✓ registrado" if is_protocol_registered() else "no registrado",
            font=("Segoe UI", 9), text_color=COLOR_SUBTLE,
        )
        self._lbl_proto.pack(side="left", padx=8)
        hint(net, "Los enlaces del navegador se encolan con tinydlp://add?url=…")

        # ================= Sistema =================
        sys_tab = build_tab("Sistema", pad=2)

        section(sys_tab, "Dependencias")
        hint(sys_tab, "yt-dlp y ffmpeg se guardan en ~/.tinydlp/bin (sin inflar el .exe)")
        deps_row = ctk.CTkFrame(sys_tab, fg_color="transparent")
        deps_row.pack(fill="x", pady=6)
        self._lbl_deps = ctk.CTkLabel(
            deps_row, text="…", font=("Segoe UI", 10), text_color=COLOR_SUBTLE,
            anchor="w",
        )
        self._lbl_deps.pack(side="left", fill="x", expand=True)
        self._btn_deps = ctk.CTkButton(
            deps_row, text="Instalar", width=100, height=30,
            font=("Segoe UI", 10), command=self._install_deps,
        )
        self._btn_deps.pack(side="right")
        self._refresh_deps()

        section(sys_tab, "Atajo global")
        self._hotkey_var = ctk.BooleanVar(value=c.hotkey_enabled)
        ctk.CTkCheckBox(
            sys_tab, text="Ctrl+Alt+D analiza el portapapeles al instante",
            variable=self._hotkey_var, command=self._on_hotkey,
            font=("Segoe UI", 10),
        ).pack(fill="x", pady=4)

        section(sys_tab, "Mantenimiento")
        maint = ctk.CTkFrame(sys_tab, fg_color="transparent")
        maint.pack(fill="x", pady=4)
        ctk.CTkButton(
            maint, text="Datos", width=110, height=30,
            font=("Segoe UI", 10), fg_color=COLOR_HOVER,
            command=self._open_data_dir,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            maint, text="Ver log", width=110, height=30,
            font=("Segoe UI", 10), fg_color=COLOR_HOVER,
            command=self._open_log,
        ).pack(side="left")
        hint(sys_tab, f"Datos: {CONFIG_DIR}")

        # Botones
        btn_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        btn_frame.pack(side="bottom", fill="x", padx=18, pady=(0, 12))
        ctk.CTkButton(
            btn_frame, text="Guardar y cerrar", height=38,
            fg_color=COLOR_ACCENT, hover_color="#17A845", text_color="#04140A",
            font=("Segoe UI", 12, "bold"), corner_radius=10,
            command=self._save_close,
        ).pack(side="right")

        # Reserva el pie antes de que la tabview consuma el alto restante.
        self.tabs.pack_forget()
        self.tabs.pack(fill="both", expand=True, padx=16, pady=(0, 8))

    # -------- handlers --------

    def _pick_dir(self) -> None:
        from tkinter import filedialog
        self.root.attributes("-topmost", False)
        try:
            chosen = filedialog.askdirectory(
                parent=self.root, title="Carpeta de descargas",
                initialdir=self._app.config.download_dir, mustexist=True,
            )
        finally:
            self.root.attributes("-topmost", True)
        if chosen:
            self._app.config.download_dir = chosen
            self._lbl_dir.configure(
                text=chosen[:48] + ("…" if len(chosen) > 48 else ""),
            )

    def _on_audio_quality(self, value: str) -> None:
        self._audio_var.set(value)
        self._app.config.audio_quality = value
        self._update_quality_summary()
        logger.info("Calidad de audio: %s kbps", value)

    def _on_audio_format(self, value: str) -> None:
        fmt = value.lower()
        self._audio_fmt_var.set(value)
        self._app.config.audio_format = fmt
        lossless = fmt in ("flac", "wav")
        self._audio_kbps_seg.configure(state="disabled" if lossless else "normal")
        self._update_quality_summary()
        logger.info("Formato de audio: %s", fmt)

    def _on_video_quality(self, value: str) -> None:
        mapping = {"720p": "720", "1080p": "1080", "4K": "4k", "Mejor": "best"}
        self._video_var.set(value)
        self._app.config.video_quality = mapping.get(value, "best")
        self._update_quality_summary()
        logger.info("Calidad de vídeo: %s", self._app.config.video_quality)

    def _on_video_format(self, value: str) -> None:
        self._video_fmt_var.set(value)
        self._app.config.video_format = value.lower()
        self._update_quality_summary()
        logger.info("Formato de vídeo: %s", self._app.config.video_format)

    def _update_quality_summary(self) -> None:
        try:
            c = self._app.config
            audio = (
                f"{c.audio_format.upper()} {c.audio_quality} kbps"
                if c.audio_format in ("mp3", "m4a", "opus")
                else f"{c.audio_format.upper()} (sin pérdida)"
            )
            res = {"720": "720p", "1080": "1080p",
                   "4k": "4K", "best": "la mejor"}.get(c.video_quality, "la mejor")
            self._quality_summary.configure(
                text=f"Salida: {audio}   ·   {c.video_format.upper()} hasta {res}",
            )
        except Exception:
            pass

    def _on_subs(self) -> None:
        self._app.config.subtitles = bool(self._subs_var.get())

    def _on_langs(self, _e) -> None:
        self._app.config.subtitle_langs = self._lang_entry.get().strip() or "es,en"

    def _on_sb(self) -> None:
        self._app.config.sponsorblock = bool(self._sb_var.get())
        logger.info("SponsorBlock: %s", "ON" if self._app.config.sponsorblock else "OFF")

    def _on_cookies(self, choice: str) -> None:
        mapping = {
            "No usar": "", "chrome": "chrome", "firefox": "firefox",
            "edge": "edge", "brave": "brave",
        }
        self._app.config.cookies_browser = mapping.get(choice, "")
        logger.info("Cookies: %s", self._app.config.cookies_browser or "no")

    def _on_rate_limit(self, _event=None) -> None:
        raw = self._rate_entry.get().strip()
        if raw and parse_rate_limit(raw) is None:
            logger.warning("Límite de velocidad inválido: %s", raw)
            self._app.config.rate_limit = ""
            return
        self._app.config.rate_limit = raw
        logger.info("Límite de velocidad: %s", raw or "sin límite")

    def _on_organize(self) -> None:
        self._app.config.auto_organize = bool(self._organize_var.get())
        logger.info("Auto-organizar: %s", "ON" if self._app.config.auto_organize else "OFF")

    def _on_metadata(self) -> None:
        self._app.config.embed_metadata = bool(self._metadata_var.get())
        logger.info("Embeber metadata: %s", "ON" if self._app.config.embed_metadata else "OFF")

    def _on_api_toggle(self) -> None:
        if self._api_var.get():
            try:
                port = int(self._api_port_entry.get().strip())
            except ValueError:
                port = 8484
            self._app.set_api_enabled(True, port if 1 <= port <= 65535 else 8484)
        else:
            self._app.set_api_enabled(False)

    def _apply_api_port(self) -> None:
        raw = self._api_port_entry.get().strip()
        try:
            port = int(raw)
        except ValueError:
            port = 8484
        if not (1 <= port <= 65535):
            port = 8484
        self._api_port_entry.delete(0, "end")
        self._api_port_entry.insert(0, str(port))
        self._api_var.set(True)
        self._app.set_api_enabled(True, port)

    def _register_protocol(self) -> None:
        ok = register_protocol()
        self._lbl_proto.configure(
            text="✓ registrado" if ok else "✗ no registrado",
            text_color=COLOR_ACCENT if ok else COLOR_ERROR,
        )
        if not ok:
            logger.warning("No se pudo registrar el protocolo tinydlp://")

    def _refresh_deps(self) -> None:
        try:
            status = DependencyManager().check()
            self._lbl_deps.configure(
                text=("yt-dlp: " + ("✓" if status["yt-dlp"] else "✗")
                      + "   ffmpeg: " + ("✓" if status["ffmpeg"] else "✗")),
            )
        except Exception as exc:
            logger.debug("refresh deps: %s", exc)

    def _install_deps(self) -> None:
        self._btn_deps.configure(state="disabled", text="Instalando…")

        def _progress(name: str, fraction: float) -> None:
            self.root.after(0, lambda: self._lbl_deps.configure(
                text=f"{name}: {int(fraction * 100)}%",
            ))

        def _done(_ok: bool) -> None:
            def _ui() -> None:
                self._btn_deps.configure(state="normal", text="Instalar")
                self._refresh_deps()
            try:
                self.root.after(0, _ui)
            except Exception:
                pass

        self._app.run_dependency_install(on_progress=_progress, on_done=_done)

    def _on_workers(self, value: float) -> None:
        self._app.config.max_concurrent = int(value)
        self._lbl_workers.configure(text=f"{int(value)} workers (requiere reinicio)")

    def _on_poll(self, value: float) -> None:
        self._app.config.poll_interval = float(value)
        try:
            self._lbl_poll.configure(text=f"{float(value):.1f} s")
        except Exception:
            pass

    def _on_auto_open(self) -> None:
        self._app.config.auto_open_folder = bool(self._auto_open_var.get())
        logger.info(
            "Abrir carpeta al terminar: %s",
            "ON" if self._app.config.auto_open_folder else "OFF",
        )

    def _on_hotkey(self) -> None:
        enabled = bool(self._hotkey_var.get())
        self._app.config.hotkey_enabled = enabled
        try:
            if enabled:
                self._app._hotkey.start()
            else:
                self._app._hotkey.stop()
        except Exception as exc:
            logger.warning("Hotkey toggle: %s", exc)
        logger.info("Hotkey Ctrl+Alt+D: %s", "ON" if enabled else "OFF")

    def _open_data_dir(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        open_path_native(CONFIG_DIR)

    def _open_log(self) -> None:
        if LOG_FILE.exists():
            open_path_native(LOG_FILE)
        else:
            self.notify_missing_log()

    def notify_missing_log(self) -> None:
        """Avisa si aún no hay log en disco."""
        logger.info("Log todavía no existe: %s", LOG_FILE)
        open_path_native(CONFIG_DIR)

    def _save_close(self) -> None:
        self._app.config.save()
        self._close()

    def _close(self) -> None:
        try:
            self.root.destroy()
        except Exception:
            pass
        if self._on_close:
            try:
                self._on_close()
            except Exception:
                pass


# ============================================================
# 18. DIÁLOGO DE SELECCIÓN DE PLAYLIST
# ============================================================

class PlaylistDialog:
    def __init__(
        self,
        parent,
        items: list[dict],
        on_accept: Callable[[list[dict]], None],
    ) -> None:
        import customtkinter as ctk
        self._ctk = ctk
        self._items = items
        self._on_accept = on_accept

        self.root = ctk.CTkToplevel(parent)
        self.root.title("Seleccionar items")
        self.root.geometry("520x560")
        self.root.attributes("-topmost", True)
        self.root.configure(fg_color=COLOR_BG)

        ctk.CTkLabel(
            self.root, text=f"Playlist — {len(items)} elementos",
            font=("Segoe UI", 16, "bold"),
        ).pack(pady=(14, 4), padx=16, anchor="w")

        ctk.CTkLabel(
            self.root, text="Selecciona los que quieras descargar:",
            font=("Segoe UI", 11), text_color=COLOR_SUBTLE,
        ).pack(padx=16, anchor="w")

        # Botones select all/none
        sel_row = ctk.CTkFrame(self.root, fg_color="transparent")
        sel_row.pack(fill="x", padx=16, pady=(8, 4))
        ctk.CTkButton(
            sel_row, text="Todos", width=80, height=26,
            font=("Segoe UI", 10), command=lambda: self._select_all(True),
        ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(
            sel_row, text="Ninguno", width=80, height=26,
            font=("Segoe UI", 10), command=lambda: self._select_all(False),
        ).pack(side="left")

        self._scroll = ctk.CTkScrollableFrame(self.root, fg_color=COLOR_SURFACE, corner_radius=8)
        self._scroll.pack(fill="both", expand=True, padx=16, pady=6)

        self._vars: list[tuple[dict, "ctk.BooleanVar"]] = []
        for item in items:
            var = ctk.BooleanVar(value=True)
            cb = ctk.CTkCheckBox(
                self._scroll,
                text=f"{item.get('title', '?')[:60]}",
                variable=var, font=("Segoe UI", 10),
            )
            cb.pack(fill="x", padx=8, pady=3)
            self._vars.append((item, var))

        # Actions
        actions = ctk.CTkFrame(self.root, fg_color="transparent")
        actions.pack(fill="x", padx=16, pady=(4, 12))
        ctk.CTkButton(
            actions, text="Cancelar", width=100, height=34,
            fg_color="transparent", hover_color=COLOR_HOVER,
            text_color=COLOR_SUBTLE, command=self.root.destroy,
        ).pack(side="right", padx=4)
        ctk.CTkButton(
            actions, text="Descargar seleccionados", height=34,
            fg_color=COLOR_ACCENT, hover_color="#17A845", text_color="#000000",
            font=("Segoe UI", 11, "bold"), command=self._accept,
        ).pack(side="right", padx=4)

    def _select_all(self, value: bool) -> None:
        for _, var in self._vars:
            var.set(value)

    def _accept(self) -> None:
        chosen = [item for item, var in self._vars if var.get()]
        try:
            self.root.destroy()
        except Exception:
            pass
        self._on_accept(chosen)


# ============================================================
# 19. ORQUESTADOR
# ============================================================

class TinyDLPApp:
    def __init__(
        self,
        pending_boot: tuple[str, str] | None = None,
        guard: "InstanceGuard | None" = None,
    ) -> None:
        self.config = Config.load()
        Path(self.config.download_dir).mkdir(parents=True, exist_ok=True)

        self.notifier = Notifier()
        self.history = HistoryManager()
        self.recent_urls = RecentUrlsStore()
        self.detector = UrlDetector(self.config.hosts)
        self.ffmpeg = find_ffmpeg()
        if not self.ffmpeg:
            logger.warning("ffmpeg no encontrado")

        self.downloader = Downloader(
            config=self.config,
            ffmpeg_path=self.ffmpeg,
            on_progress=lambda t: self._on_download_progress(t),
            on_state=lambda t: self._on_download_state(t),
        )
        if not self.downloader.available:
            self.notifier.notify(
                "Tiny-dlp — motor no disponible",
                "Reinicia la app para completar la descarga de dependencias.",
            )

        self.queue = DownloadQueue(
            config=self.config,
            downloader=self.downloader,
            history=self.history,
            notifier=self.notifier,
            on_change=self._on_queue_change,
        )

        self.pending_url: Optional[str] = None
        self.interceptor = ClipboardInterceptor(
            config=self.config,
            detector=self.detector,
            on_url=self._on_url_detected,
            on_multiple_urls=self._on_multiple_urls,
            on_protocol=self._on_protocol_url,
        )

        self.tray = TrayController(self)
        # Arrancamos el tray antes de construir la UI para que el icono aparezca <2 s.
        threading.Thread(target=self.tray.run, daemon=True, name="tray").start()
        self.root = MiniUI(self)
        self._running = True
        self._guard = guard
        self._pending_boot = pending_boot
        self.api: LocalApiServer | None = None

        # API local (si está habilitada en config; falla en silencio si el puerto está ocupado).
        if self.config.api_port > 0:
            self.start_api()
        else:
            logger.info("Local API: deshabilitada")

        # Hotkey
        self._hotkey = HotkeyManager(self._on_global_hotkey)
        if self.config.hotkey_enabled:
            self._hotkey.start()

    # -------- callbacks --------

    def _on_url_detected(self, url: str) -> None:
        self.recent_urls.add(url)
        self.pending_url = url
        self.notifier.notify(
            "Tiny-dlp — enlace detectado",
            "Click para elegir Audio, Vídeo o Ambos.",
            on_click=lambda *_: self._safe_ui(self.show_ui),
        )
        mode = self.config.mode
        if mode == DownloadMode.AUDIO.value:
            self.trigger_download("audio")
        elif mode == DownloadMode.VIDEO.value:
            self.trigger_download("video")
        elif mode == DownloadMode.BOTH.value:
            self.trigger_download("both")
        else:
            self._safe_tray()

    def _on_multiple_urls(self, urls: list[str]) -> None:
        """Ofrece encolar en bloque cuando el portapapeles trae varios enlaces."""
        for url in urls:
            self.recent_urls.add(url)
        logger.info("Multi-URL: %d enlaces detectados", len(urls))
        self._safe_tray()
        items = [{"url": url, "title": url} for url in urls]
        self._safe_ui(lambda: PlaylistDialog(
            self.root.root, items, self._on_multi_selected,
        ))

    def _on_multi_selected(self, items: list[dict]) -> None:
        if not items:
            return
        kind = self._default_kind()
        for item in items:
            self.enqueue_url(item.get("url", ""), kind)
        self.notifier.notify(
            "Tiny-dlp", f"Encoladas {len(items)} URLs ({kind})",
        )
        self._safe_tray()
        self._safe_ui_refresh()

    def _on_protocol_url(self, url: str, kind: str = "ask") -> None:
        """Atiende tinydlp://add (IPC o arranque con argumento)."""
        if not url:
            return
        logger.info("Protocolo tinydlp:// → %s (%s)", url, kind)
        self.recent_urls.add(url)
        if kind == "ask":
            mode = self.config.mode
            if mode == DownloadMode.ASK.value:
                self.pending_url = url
                self.notifier.notify(
                    "Tiny-dlp — enlace recibido",
                    "Elige Audio, Vídeo o Ambos desde el tray.",
                )
                self._safe_tray()
                self._safe_ui_refresh()
                return
            kind = mode
        self.enqueue_url(url, kind)
        self.notifier.notify("Tiny-dlp", f"Encolado desde tinydlp:// ({kind})")
        self._safe_tray()
        self._safe_ui_refresh()

    def _default_kind(self) -> str:
        mode = self.config.mode
        return mode if mode in ("audio", "video", "both") else "audio"

    def use_recent_url(self, url: str) -> None:
        """Marca como pendiente una URL del historial reciente (menú del tray)."""
        self.pending_url = url
        self._safe_tray()
        self._safe_ui_refresh()

    def _on_global_hotkey(self) -> None:
        url = self.interceptor.force_check()
        if url:
            logger.info("Hotkey → URL detectada: %s", url)
            self._safe_ui(lambda: self._on_url_detected(url))
        else:
            self.notifier.notify("Tiny-dlp", "No hay URL válida en el portapapeles")

    def trigger_download(self, kind: str) -> None:
        if not self.pending_url:
            return
        self.queue.enqueue(DownloadTask(
            id=self._new_task_id(), url=self.pending_url, kind=kind,
        ))
        self.pending_url = None
        self._safe_tray()
        self._safe_ui_refresh()

    def _tasks_for(self, url: str, kind: str) -> list[DownloadTask]:
        """Crea la tarea de descarga; 'both' produce audio y vídeo en una sola pasada."""
        return [DownloadTask(id=self._new_task_id(), url=url, kind=kind)]

    def expand_and_choose(self) -> None:
        if not self.pending_url:
            return
        url = self.pending_url
        self._safe_ui(lambda: self._run_playlist_expansion(url))

    def _run_playlist_expansion(self, url: str) -> None:
        self.notifier.notify("Tiny-dlp", "Analizando playlist…")
        def _worker():
            items = self.downloader.expand_playlist(url)
            if not items or len(items) < 2:
                self._safe_ui(lambda: self.notifier.notify(
                    "Tiny-dlp", "No es una playlist o tiene un solo elemento",
                ))
                return
            self._safe_ui(lambda: PlaylistDialog(
                self.root.root, items, self._on_playlist_selected,
            ))
        threading.Thread(target=_worker, daemon=True).start()

    def _on_playlist_selected(self, items: list[dict]) -> None:
        if not items:
            return
        kind = self.config.mode if self.config.mode in ("audio", "video", "both") else "video"
        tasks: list[DownloadTask] = []
        for item in items:
            tasks.extend(self._tasks_for(item["url"], kind))
        self.queue.enqueue_many(tasks)
        self.pending_url = None
        self.notifier.notify(
            "Tiny-dlp", f"Encolados {len(tasks)} items de la playlist",
        )
        self._safe_tray()
        self._safe_ui_refresh()

    def discard_pending(self) -> None:
        self.pending_url = None
        self._safe_tray()
        self._safe_ui_refresh()

    def _on_download_progress(self, task: DownloadTask) -> None:
        self._safe_tray()
        self._safe_ui_refresh()

    def _on_download_state(self, task: DownloadTask) -> None:
        self._safe_tray()
        self._safe_ui_refresh()

    def _on_queue_change(self) -> None:
        self._safe_tray()
        self._safe_ui_refresh()

    # -------- thread safety --------

    def _safe_tray(self) -> None:
        try:
            self.tray.refresh()
        except Exception:
            pass

    def _safe_ui(self, fn) -> None:
        try:
            self.root.root.after(0, fn)
        except Exception:
            pass

    def _safe_ui_refresh(self) -> None:
        """Coalesce bursts de progreso en un solo refresco de la UI."""
        if getattr(self, "_refresh_scheduled", False):
            return
        self._refresh_scheduled = True

        def _run() -> None:
            self._refresh_scheduled = False
            self.root.refresh()

        self._safe_ui(_run)

    def _new_task_id(self) -> str:
        import uuid
        return uuid.uuid4().hex[:10]

    # -------- API local, protocolo y dependencias --------

    def enqueue_url(self, url: str, kind: str = "audio") -> str | None:
        """Encola una URL de forma idempotente (por URL + tipo) y devuelve el id."""
        if not url:
            return None
        if kind not in ("audio", "video", "both"):
            kind = self._default_kind()
        tasks = self._tasks_for(url, kind)
        new_tasks = []
        for task in tasks:
            existing = self.queue.find_by_url(url, task.kind)
            if existing is not None:
                logger.info("Ya en cola: %s (%s)", url, task.kind)
                continue
            new_tasks.append(task)
        for task in new_tasks:
            self.queue.enqueue(task)
        self._safe_ui_refresh()
        if new_tasks:
            return new_tasks[0].id
        existing = self.queue.find_by_url(url)
        return existing.id if existing else None

    def start_api(self) -> None:
        """Arranca la API local según config.api_port (0 = deshabilitada)."""
        if (
            self.api is not None and self.api.running
            and self.api.port == self.config.api_port
        ):
            return
        self.stop_api()
        server = LocalApiServer(self, self.config.api_port)
        self.api = server if server.start() else None

    def stop_api(self) -> None:
        if self.api is None:
            return
        try:
            self.api.stop()
        except Exception as exc:
            logger.debug("stop_api: %s", exc)
        self.api = None

    def set_api_enabled(self, enabled: bool, port: int = 8484) -> None:
        """Activa o desactiva la API local en caliente y persiste el cambio."""
        if enabled:
            self.config.api_port = int(port) if port else 8484
            self.start_api()
        else:
            self.config.api_port = 0
            self.stop_api()
        self.config.save()

    def run_dependency_install(
        self,
        on_progress: Callable[[str, float], None] | None = None,
        on_done: Callable[[bool], None] | None = None,
    ) -> None:
        """Descarga yt-dlp/ffmpeg en segundo plano (botón Instalar dependencias)."""

        def _worker() -> None:
            ok = False
            try:
                deps = DependencyManager()
                status = deps.check()
                self.notifier.notify("Tiny-dlp", "Instalando dependencias…")
                if not status["yt-dlp"]:
                    deps.download_ytdlp(on_progress)
                if not status["ffmpeg"]:
                    deps.download_ffmpeg(on_progress)
                status = deps.check()
                ok = all(status.values())
                logger.info(
                    "Dependencias: yt-dlp=%s ffmpeg=%s",
                    "OK" if status["yt-dlp"] else "falta",
                    "OK" if status["ffmpeg"] else "falta",
                )
                if ok:
                    self.notifier.notify(
                        "Tiny-dlp",
                        "Dependencias listas. Reinicia si acabas de instalar yt-dlp.",
                    )
                else:
                    self.notifier.notify(
                        "Tiny-dlp", "No se pudieron instalar todas las dependencias",
                    )
            except Exception as exc:
                logger.warning("Instalación de dependencias falló: %s", exc)
            finally:
                if on_done is not None:
                    try:
                        on_done(ok)
                    except Exception:
                        pass

        threading.Thread(
            target=_worker, daemon=True, name="deps-install",
        ).start()

    # -------- acciones --------

    def pause_detection(self) -> None:
        self.interceptor.pause()
        self.config.save()
        self._safe_tray()
        self._safe_ui_refresh()

    def resume_detection(self) -> None:
        self.interceptor.resume()
        self.config.save()
        self._safe_tray()
        self._safe_ui_refresh()

    def cycle_mode(self) -> None:
        order = [DownloadMode.ASK, DownloadMode.AUDIO,
                 DownloadMode.VIDEO, DownloadMode.BOTH]
        current = DownloadMode(self.config.mode)
        self.config.mode = order[(order.index(current) + 1) % len(order)].value
        self.config.save()
        self._safe_tray()
        self._safe_ui_refresh()

    def cycle_audio_quality(self) -> None:
        order = ["128", "192", "320"]
        idx = order.index(self.config.audio_quality) if self.config.audio_quality in order else 1
        self.config.audio_quality = order[(idx + 1) % len(order)]
        self.config.save()
        self._safe_tray()

    def cycle_video_quality(self) -> None:
        order = ["720", "1080", "4k", "best"]
        idx = order.index(self.config.video_quality) if self.config.video_quality in order else 3
        self.config.video_quality = order[(idx + 1) % len(order)]
        self.config.save()
        self._safe_tray()

    def set_audio_format(self, fmt: str) -> None:
        if fmt not in ("mp3", "m4a", "flac", "wav", "opus"):
            return
        self.config.audio_format = fmt
        self.config.save()
        logger.info("Formato de audio: %s", fmt)
        self._safe_tray()
        self._safe_ui_refresh()

    def set_video_format(self, fmt: str) -> None:
        if fmt not in ("mp4", "mkv", "webm"):
            return
        self.config.video_format = fmt
        self.config.save()
        logger.info("Formato de vídeo: %s", fmt)
        self._safe_tray()
        self._safe_ui_refresh()

    def set_audio_quality(self, kbps: str) -> None:
        if kbps not in ("128", "192", "320"):
            return
        self.config.audio_quality = kbps
        self.config.save()
        logger.info("Calidad de audio: %s kbps", kbps)
        self._safe_tray()
        self._safe_ui_refresh()

    def set_video_quality(self, quality: str) -> None:
        if quality not in ("720", "1080", "4k", "best"):
            return
        self.config.video_quality = quality
        self.config.save()
        logger.info("Calidad de vídeo: %s", quality)
        self._safe_tray()
        self._safe_ui_refresh()

    def toggle_subtitles(self) -> None:
        self.config.subtitles = not self.config.subtitles
        self.config.save()
        self._safe_tray()

    def toggle_sponsorblock(self) -> None:
        self.config.sponsorblock = not self.config.sponsorblock
        self.config.save()
        self._safe_tray()

    def show_ui(self) -> None:
        self.root.show()

    def open_download_dir(self) -> None:
        p = Path(self.config.download_dir)
        p.mkdir(parents=True, exist_ok=True)
        open_path_native(p)

    def open_path(self, path: str) -> None:
        if path:
            open_path_native(path)

    def open_credits(self) -> None:
        try:
            webbrowser.open(CREDITS_URL, new=2)
        except Exception as exc:
            logger.error("No se pudo abrir créditos: %s", exc)

    def update_ytdlp(self) -> None:
        def _worker():
            try:
                self.notifier.notify("Tiny-dlp", "Actualizando yt-dlp…")
                deps = DependencyManager()
                if deps.download_ytdlp():
                    self.notifier.notify(
                        "Tiny-dlp", "yt-dlp actualizado. Reinicia la app.",
                    )
                else:
                    self.notifier.notify(
                        "Tiny-dlp", "Error actualizando yt-dlp",
                    )
            except Exception as exc:
                logger.exception("update_ytdlp: %s", exc)
        threading.Thread(target=_worker, daemon=True).start()

    def quit(self, *_args) -> None:
        if not self._running:
            return
        self._running = False
        logger.info("Cerrando Tiny-dlp…")
        try:
            self._hotkey.stop()
        except Exception:
            pass
        try:
            self.stop_api()
        except Exception:
            pass
        try:
            self.interceptor.stop()
        except Exception:
            pass
        try:
            self.queue.shutdown()
        except Exception:
            pass
        try:
            self.config.save()
        except Exception:
            pass
        try:
            self.tray.stop()
        except Exception:
            pass
        if self._guard is not None:
            try:
                self._guard.release()
            except Exception:
                pass
        self._safe_ui(self.root.destroy)

    def run(self) -> None:
        if not self._running:
            return
        self.interceptor.start()
        if self._pending_boot is not None:
            url, kind = self._pending_boot
            self._pending_boot = None
            # La instancia anterior no existía: esperamos a que tray/UI estén listos.
            self.root.root.after(2000, lambda: self._on_protocol_url(url, kind))
        try:
            self.root.root.mainloop()
        except KeyboardInterrupt:
            self.quit()


# ============================================================
# 20. MAIN
# ============================================================

def main() -> None:
    logger.info("=" * 50)
    logger.info("%s v%s arrancando", APP_NAME, APP_VERSION)
    logger.info("Python %s · %s", sys.version.split()[0], sys.platform)
    logger.info("ffmpeg: %s", find_ffmpeg() or "NO")
    logger.info("yt-dlp: %s", find_ytdlp_binary() or "módulo Python")
    logger.info("pynput: %s", "OK" if PYNPUT_AVAILABLE else "no")

    config = Config.load()

    # Instancia única: si ya corre Tiny-dlp, la URL va por IPC y este proceso sale.
    proto_arg = parse_protocol_arg(sys.argv)
    guard = InstanceGuard()
    if not guard.acquire():
        if InstanceGuard.is_tinydlp_running():
            logger.info("Tiny-dlp ya está en ejecución")
            if proto_arg:
                write_ipc_url(proto_arg[0], proto_arg[1])
            return
        logger.warning(
            "Puerto de instancia %d ocupado por otro proceso; continúo sin guard",
            INSTANCE_PORT,
        )

    # Protocolo tinydlp:// — una sola vez, solo HKCU.
    protocol_ok = is_protocol_registered()
    if sys.platform == "win32" and not protocol_ok:
        protocol_ok = register_protocol()

    deps = DependencyManager()
    status = deps.check()
    all_ok = all(status.values())

    # Wizard de primer arranque
    if not all_ok and not config.deps_prompted:
        try:
            wizard = FirstRunWizard(deps)
            wizard.run()
            config.deps_prompted = True
            config.save()
        except Exception as exc:
            logger.exception("Wizard falló: %s", exc)

    try:
        app = TinyDLPApp(
            pending_boot=proto_arg,
            guard=guard if guard.acquired else None,
        )
        logger.info(
            "Local API: %s",
            f"{app.api.url} (habilitada)" if app.api else "deshabilitada",
        )
        logger.info(
            "Protocolo tinydlp://: %s", "registrado" if protocol_ok else "no registrado",
        )
        logger.info("Cookies: %s", config.cookies_browser or "no")
        app.run()
    except Exception as exc:
        logger.exception("Fallo fatal: %s", exc)
        raise


if __name__ == "__main__":
    main()