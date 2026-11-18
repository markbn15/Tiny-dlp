@echo off
setlocal
cd /d "%~dp0"

title Compilando Tiny-dlp
echo Instalando dependencias...
python -m pip install --upgrade pip
if errorlevel 1 goto :error
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo Limpiando builds previos...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "Tiny-dlp.spec" del /q "Tiny-dlp.spec"

echo Compilando ejecutable...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name "Tiny-dlp" ^
    --icon "icon.ico" ^
    --add-data "icon.ico;." ^
    --collect-all customtkinter ^
    --collect-all pystray ^
    --collect-all plyer ^
    --hidden-import pynput.keyboard._win32 ^
    --hidden-import PIL._tkinter_finder ^
    --hidden-import tkinter ^
    --hidden-import tkinter.filedialog ^
    --exclude-module pygame ^
    --exclude-module setuptools ^
    --exclude-module pip ^
    --exclude-module pytest ^
    --exclude-module unittest ^
    tinydlp.py
if errorlevel 1 goto :error

echo Listo: dist\Tiny-dlp.exe
pause
exit /b 0

:error
echo FALLO LA COMPILACION
pause
exit /b 1
