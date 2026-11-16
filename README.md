# Tiny-dlp 🚀

**La navaja suiza de descargas de internet** — ligera, silenciosa, siempre en la bandeja.

[![License: MIT](https://img.shields.io/badge/License-MIT-1DB954.svg)](LICENSE)

## ✨ Qué hace

Copia una URL de YouTube (o SoundCloud, Vimeo, TikTok, Instagram…), y Tiny-dlp la detecta automáticamente desde el portapapeles. Sin ventanas gigantes, sin popups, sin publicidad. Todo desde el icono de la bandeja del sistema.

## Características

- 🎯 **Interceptor de portapapeles pausable** — detecta URLs sin que copies y pegues manualmente
- 🎵 **3 modos de descarga**: Audio (MP3) · Vídeo (MP4) · Ambos
- ⚡ **Cola concurrente** con N workers configurables
- 📊 **Progreso en tiempo real** en el icono de la bandeja
- 🗂️ **Historial persistente** de 500 descargas
- 🖥️ **Mini UI opcional** (nunca se abre sola)
- 🔔 **Notificaciones nativas** con callback clickable
- 🎬 **Playlists completas** (yt-dlp expande automáticamente)
- 🔄 **Retry automático** con backoff
- 🧹 **Cancelación limpia** (elimina archivos `.part`)
- 🛡️ **Thread-safe** — 0 bloqueos de UI
- 🔧 **Auto-update de yt-dlp** desde el menú

## 📦 Instalación

### Opción A — Ejecutable
1. Descarga `Tiny-dlp.exe` de **Assets**
2. Ejecútalo. Crea `Downloads\Tiny-dlp\` automáticamente
3. Copia una URL de YouTube → aparecerá en la bandeja 🟢

### Opción B — Desde el código
```bash
git clone https://github.com/markbn15/tiny-dlp.git
```
```bash
cd tiny-dlp
pip install -r requirements.txt
python tinydlp.py
```
## Requisito opcional: ffmpeg

Para convertir audio o mergear vídeo+audio hace falta `ffmpeg.exe`. El `build.bat` lo descarga automáticamente, pero si corres desde código fuente puedes:

- Ponerlo en tu PATH
- O copiarlo a `~/.tinydlp/bin/ffmpeg.exe`

## ⌨️ Uso

1. Copia una URL de YouTube / SoundCloud / Vimeo / etc.
2. El icono se pone **verde** y aparece notificación
3. Click derecho en el icono → **🎵 Audio** / **🎬 Vídeo** / **🎵🎬 Ambos**
4. El progreso aparece en el tooltip del icono (`⬇ 45% — Título`)
5. Al terminar → notificación + entrada en historial

## 🎛️ Menú de bandeja

| Opción | Descripción |
|---|---|
| ⏸/▶ Pausar detección | Deja de vigilar el portapapeles |
| ⏸/▶ Pausar descargas | Los workers terminan la actual y se detienen |
| ⚙️ Modo | Cicla entre Preguntar / Audio / Vídeo / Ambos |
| 🖥️ Abrir panel | Muestra la UI con cola e historial |
| 📂 Carpeta | Abre la carpeta de descargas |
| ⬇️ Actualizar yt-dlp | Descarga la última versión standalone |

## 📁 Estructura

~/.tinydlp/
├── config.json # ajustes persistentes
├── history.json # últimas 500 descargas
├── tinydlp.log # log rotativo (512KB × 3)
└── bin/
├── ffmpeg.exe # opcional
└── yt-dlp.exe # opcional (auto-update)

## 🏗️ Arquitectura

Main thread ──────► Tkinter mainloop (ventana oculta)
Tray thread ──────► pystray (icono + menú dinámico)
Watcher thread ───► polling portapapeles (pausable)
Worker threads ───► yt-dlp × N (default 2, concurrentes)


## ⚠️ Legal

Tiny-dlp es una interfaz para `yt-dlp`. **Tú** eres responsable del uso que le des. Respeta los términos de servicio de cada plataforma y las leyes de propiedad intelectual de tu país.

## 🙏 Créditos

Hecho por **MarkBN**.

Motor de descarga: yt-dlp (Unlicense).
