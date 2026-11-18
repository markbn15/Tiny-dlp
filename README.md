# 📥 Tiny-dlp - El descargador definitivo ultraligero

![Tiny-dlp Banner](https://img.shields.io/badge/Tiny--dlp-Downloader-1DB954?style=for-the-badge&logo=download&logoColor=white)
![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge&logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey?style=for-the-badge&logo=windows&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

**Tiny-dlp** es un descargador de audio y vídeo ultraligero para Windows que vive discretamente en tu bandeja del sistema. Olvídate de interfaces web llenas de anuncios o programas pesados como Electron. Tiny-dlp utiliza el poder de `yt-dlp` y `ffmpeg` bajo el capó, pero con una interfaz nativa, rápida y minimalista.

Detecta automáticamente los enlaces de YouTube, TikTok, Instagram, Twitter/X, SoundCloud y muchos más en tu portapapeles, y los encola para que los descargues con el formato y la calidad exacta que desees.

---

## ⚡ Ultraligero y Rápido
* **RAM en reposo (UI cerrada):** Apenas **~60–65 MB**.
* **Arranque:** Instantáneo (el icono aparece en la bandeja en ~1 segundo).
* **Cero dependencias pesadas:** Sin base de datos, sin navegadores embebidos, sin publicidad.

---

## ✨ Características (Navaja suiza de descargas)

* 📋 **Detección automática en el portapapeles:** Copia un enlace y Tiny-dlp lo detectará al instante. Si copias varios enlaces a la vez, te mostrará un cuadro de diálogo para elegir cuáles descargar.
* 🧩 **Instalador de dependencias integrado:** No necesitas configurar nada. En el primer arranque, Tiny-dlp descargará automáticamente `yt-dlp` y `ffmpeg` desde sus repositorios oficiales a tu carpeta de usuario.
* 🎚️ **Selector de Formato y Calidad en tiempo real:**
  * **Audio:** `MP3`, `M4A`, `FLAC`, `WAV`, `OPUS` (con bitrates de 128, 192 o 320 kbps).
  * **Vídeo:** `MP4`, `MKV`, `WEBM` (hasta resolución 4K).
  * **Modos de descarga:** Solo audio, solo vídeo, o **Ambos** en una sola pasada.
* 📂 **Auto-organización inteligente:** Mueve automáticamente las descargas a `Descargas/Tiny-dlp/Música/` (creando subcarpetas por artista) o a `Descargas/Tiny-dlp/Vídeos/`.
* 🖥️ **Mini-Reproductor Flotante (UI):** Panel de control con `CustomTkinter` para gestionar la cola, el historial, las dependencias y los ajustes.
* 🔗 **Protocolo personalizado `tinydlp://`:** Puedes usar un *bookmarklet* en tu navegador para enviar enlaces directamente a Tiny-dlp sin necesidad de copiarlos.
* 🔔 **Notificaciones interactivas:** Integración con Windows 11 (`win11toast`) para avisarte cuando termine una descarga, con botones de acción rápida ("Abrir carpeta", "Descargar otra vez").
* ⌨️ **Hotkey Global `Ctrl+Alt+D`:** Analiza el portapapeles al instante sin importar en qué ventana estés.
* 🛡️ **Instancia Única:** No se abrirán múltiples ventanas si haces doble clic en el ejecutable varias veces.
* 🍪 **Opciones Avanzadas:** Soporte para usar las *cookies* de tu navegador (para vídeos privados), límite de velocidad de descarga, descarga de subtítulos y bloqueo de patrocinadores (SponsorBlock).
* 💾 **Cola persistente:** Si cierras la app, las descargas pendientes se guardan y se reanudan al volver a abrir.

---

## 🚀 Instalación y Uso

### Opción 1: Usar el Ejecutable (Recomendado)
Si ya tienes el archivo `Tiny-dlp.exe`:
1. Haz doble clic. (No verás ventana, se irá directo a la bandeja del sistema).
2. **Clic derecho** en el icono de la flecha verde para abrir el menú de control.
3. Copia cualquier enlace compatible y selecciona la acción (Audio, Vídeo o Ambos).

### Opción 2: Compilar desde el código fuente
Si tienes Python instalado, clona el repositorio y ejecuta el script de compilación o corre el código directamente:

```bat
# Para compilar el .exe (requiere PyInstaller)
build.bat
```
# ⚙️ Configuración y Ajustes

El panel principal y el menú de la bandeja te permiten configurar:

| Sección | Opciones disponibles |
|---|---|
| **Modo por defecto** | Preguntar, Solo Audio, Solo Vídeo o Ambos |
| **Calidad** | Formatos y bitrates/resoluciones exactas |
| **Red** | Cookies del navegador (`chrome`, `firefox`, `edge`, `brave`) y límite de velocidad (ej. `500K` o `2M`) |
| **Descargas** | Carpeta destino, auto-organización, apertura automática y número de descargas simultáneas |
| **Sistema** | Instalación/actualización de `yt-dlp` y `ffmpeg`, y atajo de teclado global |

> 💡 **Tip:** Puedes crear un marcador en tu navegador con este código para descargar el vídeo que estés viendo al instante:
>
> ```javascript
> javascript:location.href='tinydlp://add?url='+encodeURIComponent(location.href)+'&kind=ask'
> ```

---

## 📁 Archivos de Datos

Todo se guarda de forma limpia en tu perfil de usuario, sin ensuciar el sistema:

```
~/.tinydlp/config.json       # Ajustes de la app
~/.tinydlp/history.json      # Historial de las últimas 500 descargas
~/.tinydlp/queue.json        # Cola de pendientes
~/.tinydlp/recent_urls.json  # Últimas 10 URLs copiadas
~/.tinydlp/bin/              # Binarios de yt-dlp.exe, ffmpeg.exe y ffprobe.exe
~/.tinydlp/tinydlp.log       # Log rotativo para depuración
```

---

## 🐛 Solución de Problemas

- **No aparece el icono:** Revisa el archivo `~/.tinydlp/tinydlp.log`.
- **Falla la descarga de FFmpeg:** Ve a **Ajustes → Sistema** y pulsa "Instalar". Se descargará desde los repos oficiales de [BtbN](https://github.com/BtbN/FFmpeg-Builds).
- **YouTube pide iniciar sesión (Age-restricted):** Ve a **Ajustes → Red** y selecciona el navegador donde tengas tu cuenta iniciada. Asegúrate de cerrar el navegador antes de descargar.
- **El navegador no abre `tinydlp://`:** Ve a **Ajustes → Red** y pulsa **Registrar `tinydlp://`**.

---

## ❤️ Créditos

Desarrollado con cariño por [@markbn115](https://github.com/markbn15)

- Instagram: [@markbn115](https://www.instagram.com/markbn115/?hl=es)

# Si te gusta el proyecto, ¡deja una estrella ⭐ en el repo!
