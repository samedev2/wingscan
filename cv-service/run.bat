@echo off
REM Helper para rodar o cv-service no Windows.
REM Uso:  cv-service\run.bat

setlocal
pushd "%~dp0"

if not exist .venv (
    echo [run] Criando venv em .venv ...
    python -m venv .venv || goto :error
)

call .venv\Scripts\activate.bat || goto :error

echo [run] Instalando deps (requirements.txt) ...
pip install -r requirements.txt || goto :error

echo [run] Iniciando cv-service em http://%CV_HOST%:%CV_PORT% ...
echo [run] Endpoints:
echo   GET  /api/state
echo   GET  /video_feed        (MJPEG anotado)
echo   GET  /api/frame.jpg     (snapshot)
echo   WS   /ws/events         (eventos de cruzamento)
echo.

REM Permitir override por env vars (defaults em config.py)
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
goto :eof

:error
echo [run] Falhou.
exit /b 1
