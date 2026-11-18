# Tiny-dlp v3.0

Descargador ultraligero para Windows que vive en la bandeja del sistema. Detecta enlaces en el portapapeles, encola descargas con [yt-dlp](https://github.com/yt-dlp/yt-dlp) y usa `ffmpeg` solo cuando hace falta. Sin consola, sin Electron, sin base de datos.

- RAM en reposo (UI cerrada): ~60–65 MB
- Icono en bandeja en ~1 s (el ejecutable onefile tarda algo más en extraerse la primera vez)
- Config en `%USERPROFILE%\.tinydlp\config.json`

---

## Instalación

### Opción A — ejecutable

```bat
build.bat
```

Genera `dist\Tiny-dlp.exe` (~30 MB, incluye yt-dlp). **ffmpeg/ffprobe no se empaquetan**: se descargan bajo demanda desde sus repos oficiales en `%USERPROFILE%\.tinydlp\bin\`.

### Opción B — desde código

```bat
python -m pip install -r requirements.txt
python tinydlp.py
```

### Dependencias (yt-dlp / ffmpeg)

- **Primer arranque**: aparece un asistente para descargarlas.
- **Después**: menú del tray → `🧩 Instalar dependencias`, o Ajustes → Sistema → `Instalar`.
- **Actualizar yt-dlp**: tray → `⬇️ Actualizar yt-dlp`.

Todo se guarda en `~/.tinydlp/bin/`, así que el `.exe` solo empaqueta yt-dlp y el resto de dependencias Python.

---

## Formato y calidad

El panel principal muestra siempre una tarjeta **🎚️ Formato y calidad** con cuatro selectores. Cada cambio se guarda al instante y se refleja en el menú del tray y en las etiquetas de los botones de descarga (`🎵 Audio · MP3 320k`, `🎬 Vídeo · MKV 1080p`).

| Selector | Valores | Config (`config.json`) |
| --- | --- | --- |
| Formato de audio | `MP3`, `M4A`, `FLAC`, `WAV`, `OPUS` | `audio_format` |
| Bitrate de audio | `128`, `192`, `320` kbps (solo MP3/M4A/OPUS) | `audio_quality` |
| Contenedor de vídeo | `MP4`, `MKV`, `WEBM` | `video_format` |
| Resolución máxima | `720p`, `1080p`, `4K`, `Mejor` | `video_quality` |

- Los formatos sin pérdida (`FLAC`, `WAV`) desactivan el selector de bitrate y omiten la portada embebida cuando el contenedor no la admite (p. ej. WAV).
- La resolución se aplica como altura máxima: si el vídeo original es menor, se descarga la mejor disponible sin reescalar.
- Los mismos selectores están en **Ajustes → Calidad**, con vista previa inmediata.
- En el menú del tray, `Audio` y `Vídeo` abren submenús con el formato y la calidad activos marcados.

---

## Protocolo `tinydlp://`

Tiny-dlp se registra **solo para el usuario actual** (`HKCU\Software\Classes\tinydlp`), nunca en `HKLM`. El registro se hace una vez al arrancar; también puedes forzarlo desde **Ajustes → Red → 🔗 Registrar tinydlp://**.

Formato:

```
tinydlp://add?url=<url-encoded>&kind=<audio|video|both|ask>
```

- Si Tiny-dlp **no está corriendo**, se arranca y la URL se encola a los 2 s.
- Si **ya está corriendo**, la URL se deja en `~/.tinydlp/ipc_url.txt`; el interceptor la recoge en ≤ 500 ms. Una segunda instancia del `.exe` no se abre.

### Bookmarklet

Crea un marcador con esta URL y pulsa sobre él en cualquier página compatible:

```javascript
javascript:location.href='tinydlp://add?url='+encodeURIComponent(location.href)
```

Para elegir el tipo:

```javascript
javascript:location.href='tinydlp://add?url='+encodeURIComponent(location.href)+'&kind=audio'
```

> Chrome/Edge pueden pedir confirmación la primera vez que se abre el protocolo. Marca "Abrir siempre".

---

## API HTTP local

Escucha **solo en `127.0.0.1:8484`** (nunca `0.0.0.0`). Se activa/desactiva en **Ajustes → Red → Activar API local**. Si el puerto está ocupado, la app sigue funcionando sin API y lo anota en el log. `api_port = 0` la desactiva.

| Endpoint | Descripción | Respuesta |
| --- | --- | --- |
| `GET /add?url=<url>&kind=<audio\|video\|both>` | Encola una descarga | `{"status":"queued","id":"..."}` |
| `GET /status` | Tareas activas | `{"ok":true,"paused":false,"tasks":[...]}` |
| `GET /pause` | Pausa la cola | `{"status":"paused"}` |
| `GET /resume` | Reanuda la cola | `{"status":"resumed"}` |
| `GET /ping` | Health-check | `{"ok":true,"version":"3.0.0"}` |

### Ejemplos `curl`

```bat
curl "http://127.0.0.1:8484/ping"
curl "http://127.0.0.1:8484/add?url=https%3A%2F%2Fyoutu.be%2FdQw4w9WgXcQ&kind=audio"
curl "http://127.0.0.1:8484/status"
curl "http://127.0.0.1:8484/pause"
curl "http://127.0.0.1:8484/resume"
```

PowerShell:

```powershell
Invoke-RestMethod "http://127.0.0.1:8484/add?url=https%3A%2F%2Fyoutu.be%2FdQw4w9WgXcQ&kind=video"
```

---

## Nuevas features v3.0

| Feature | Config (`config.json`) | Default | Dónde se controla |
| --- | --- | --- | --- |
| Protocolo `tinydlp://` + instancia única | — | registrado en 1.er arranque | Ajustes → Red |
| API HTTP local (solo loopback) | `api_port` | `8484` (`0` = off) | Ajustes → Red |
| Auto-organizar en `Música/` y `Vídeos/` (subcarpeta por artista) | `auto_organize` | `true` | Ajustes → Descargas |
| Detección multi-URL en portapapeles | — | activo | Diálogo con checkboxes |
| Últimas 10 URLs copiadas (persistente) | — | activo | Tray → 🔗 Últimas URLs copiadas |
| Cookies desde navegador | `cookies_browser` | `""` | Ajustes → Red (`chrome`, `firefox`, `edge`, `brave`) |
| Límite de velocidad | `rate_limit` | `""` | Ajustes → Red (`500K`, `2M`) |
| Notificaciones con acciones | — | si `win11toast` está disponible | Automático (Abrir carpeta / Descargar otra vez) |
| Selector de formato de audio | `audio_format` | `mp3` | Panel principal o Ajustes → Calidad |
| Bitrate de audio | `audio_quality` | `192` | Panel principal o Ajustes → Calidad |
| Selector de contenedor de vídeo | `video_format` | `mp4` | Panel principal o Ajustes → Calidad |
| Resolución máxima de vídeo | `video_quality` | `best` | Panel principal o Ajustes → Calidad |
| Thumbnail + metadata en audio | `embed_metadata` | `true` | Ajustes → Calidad |
| Instalador de yt-dlp/ffmpeg bajo demanda | — | — | Ajustes → Sistema o menú del tray |

Las configs antiguas (v2.0) se cargan sin problema: las claves que falten toman su valor por defecto.

### Ajustes en pestañas

| Pestaña | Contenido |
| --- | --- |
| **Descargas** | Carpeta de destino, organización por tipo, apertura automática, workers y frecuencia de escaneo |
| **Calidad** | Formatos de audio/vídeo, bitrate, resolución, subtítulos y SponsorBlock |
| **Red** | Cookies, límite de velocidad, API local y registro del protocolo |
| **Sistema** | Dependencias, atajo global, carpeta de datos y log |

---

## Uso diario

1. Copia un enlace de YouTube, SoundCloud, Vimeo, TikTok, etc.
2. Aparece una notificación y queda como `pending` en el tray.
3. Ajusta el **modo** y el **formato/calidad** en la tarjeta superior del panel (o en el tray); los botones `🎵 Audio`, `🎬 Vídeo` y `🎛️ Ambos` muestran el formato exacto que se descargará. `Ambos` descarga audio y vídeo en una sola pasada (con `--keep-video`).
4. Si copias **varias URLs a la vez**, se abre un diálogo para seleccionar cuáles encolar.
5. El archivo final se mueve a `Descargas/Tiny-dlp/Música/<Artista>/` o `.../Vídeos/`.

Atajos:

- `Ctrl+Alt+D`: analiza el portapapeles al instante.
- `Ctrl+C` en cualquier sitio: detección automática (configurable, 0,3–2 s).

---

## Archivos de datos

| Ruta | Contenido |
| --- | --- |
| `~/.tinydlp/config.json` | Ajustes |
| `~/.tinydlp/history.json` | Historial (máx. 500) |
| `~/.tinydlp/queue.json` | Cola pendiente (sobrevive reinicios) |
| `~/.tinydlp/recent_urls.json` | Últimas 10 URLs copiadas |
| `~/.tinydlp/ipc_url.txt` | IPC del protocolo (efímero) |
| `~/.tinydlp/tinydlp.log` | Log rotativo (512 KB × 3) |
| `~/.tinydlp/bin/` | `yt-dlp.exe`, `ffmpeg.exe`, `ffprobe.exe` |

---

## Solución de problemas

- **No aparece el icono**: revisa `%USERPROFILE%\.tinydlp\tinydlp.log`.
- **`/ping` no responde**: la API está desactivada (`api_port = 0`), el puerto está ocupado por otra app o cambiaste el puerto en Ajustes → Red.
- **El navegador no abre `tinydlp://`**: pulsa `Registrar tinydlp://` en Ajustes → Red y acepta el aviso del navegador. El registro es por usuario, así que no requiere administrador.
- **Falla la descarga de ffmpeg**: vuelve a intentarlo desde `🧩 Instalar dependencias`; se descarga desde los releases oficiales de BtbN.
- **Cookies**: si YouTube pide iniciar sesión, elige tu navegador en Ajustes. Cierra el navegador si la base de datos de cookies está bloqueada.

---

Autor: MarkBN · Licencia MIT
