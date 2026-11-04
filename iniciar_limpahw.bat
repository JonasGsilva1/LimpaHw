@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title Inicializador LimpaHW
cd /d "%~dp0"

echo ==============================================================
echo        LIMPA-HW - Inicializador do Painel Web (Porta 8501)
echo ==============================================================
echo.

if not exist "logs" mkdir "logs"

:: 1. Verifica se ja existe algum processo escutando na porta 8501
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8501" ^| findstr "LISTENING"') do (
    echo [OK] O servico LimpaHW ja esta em execucao ^(PID: %%a^).
    echo Abrindo navegador em http://localhost:8501 ...
    start "" "http://localhost:8501"
    timeout /t 3 >nul
    exit /b 0
)

:: 2. Verifica se o ambiente virtual (.venv) ja esta pronto com o Streamlit instalado
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -c "import streamlit, netmiko, paramiko, openpyxl, pandas" >nul 2>&1
    if !errorlevel! equ 0 (
        goto :START_SERVER
    )
)

echo [PRIMEIRA EXECUCAO] Preparando ambiente e instalando dependencias...
echo Isso leva cerca de 1 a 2 minutos apenas na primeira vez neste computador.
echo.

:: 3. Tenta localizar Python instalado no Windows (ignorando o atalho vazio da Microsoft Store)
set "SYS_PYTHON="
py -3 -c "import sys; print(sys.executable)" >nul 2>&1
if !errorlevel! equ 0 (
    set "SYS_PYTHON=py -3"
) else (
    python -c "import sys; print(sys.executable)" >nul 2>&1
    if !errorlevel! equ 0 (
        set "SYS_PYTHON=python"
    )
)

if defined SYS_PYTHON (
    echo [1/2] Criando ambiente virtual Python ^(.venv^) usando !SYS_PYTHON!...
    !SYS_PYTHON! -m venv .venv
    if exist ".venv\Scripts\python.exe" (
        echo [2/2] Instalando pacotes do requirements.txt via pip...
        .venv\Scripts\python.exe -m pip install --upgrade pip --quiet --trusted-host pypi.org --trusted-host files.pythonhosted.org
        .venv\Scripts\python.exe -m pip install -r requirements.txt --trusted-host pypi.org --trusted-host files.pythonhosted.org
        if !errorlevel! equ 0 (
            goto :START_SERVER
        )
    )
)

:: 4. Se o PC corporativo nao tiver Python instalado (ou falhar), usa/instala o gerenciador portatil 'uv' (nao exige Admin)
set "UV_EXE="
where uv >nul 2>&1
if !errorlevel! equ 0 (
    set "UV_EXE=uv"
) else if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "UV_EXE=%USERPROFILE%\.local\bin\uv.exe"
) else if exist "%USERPROFILE%\.cargo\bin\uv.exe" (
    set "UV_EXE=%USERPROFILE%\.cargo\bin\uv.exe"
)

if not defined UV_EXE (
    echo [INFO] Python nao encontrado neste PC. Baixando gerenciador portatil 'uv' ^(sem precisar de Admin^)...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; irm https://astral.sh/uv/install.ps1 | iex"
    if exist "%USERPROFILE%\.local\bin\uv.exe" (
        set "UV_EXE=%USERPROFILE%\.local\bin\uv.exe"
    ) else if exist "%USERPROFILE%\.cargo\bin\uv.exe" (
        set "UV_EXE=%USERPROFILE%\.cargo\bin\uv.exe"
    )
)

if defined UV_EXE (
    echo [1/2] Baixando Python portatil e criando ambiente ^(.venv^) via uv...
    "!UV_EXE!" venv .venv --python 3.12 --native-tls
    echo [2/2] Instalando pacotes do requirements.txt...
    "!UV_EXE!" pip install -r requirements.txt --native-tls
    if !errorlevel! equ 0 (
        goto :START_SERVER
    )
)

echo.
echo [ERRO] Nao foi possivel configurar o Python automaticamente neste computador.
echo Verifique se o PC possui acesso a internet ou instale o Python 3.10+ em https://www.python.org/downloads/
echo.
pause
exit /b 1

:START_SERVER
echo.
echo Iniciando servidor LimpaHW em segundo plano...
start "LimpaHW Service" /min cmd /c ".venv\Scripts\python.exe -m streamlit run app\web_app.py --server.port 8501 --server.headless true > logs\servidor_web.log 2>&1"

echo Aguardando o servidor responder na porta 8501...
set /a TRIES=0
:WAIT_LOOP
set /a TRIES+=1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8501" ^| findstr "LISTENING"') do (
    echo.
    echo [OK] Servidor ativo ^(PID: %%a^)! Abrindo navegador em http://localhost:8501 ...
    start "" "http://localhost:8501"
    echo Para encerrar o servico depois, execute "parar_limpahw.bat".
    timeout /t 3 >nul
    exit /b 0
)

if !TRIES! lss 15 (
    timeout /t 1 /nobreak >nul
    goto :WAIT_LOOP
)

echo.
echo [ERRO] O servidor nao iniciou na porta 8501 dentro do tempo esperado.
if exist "logs\servidor_web.log" (
    echo Detalhes do log ^(logs\servidor_web.log^):
    type "logs\servidor_web.log"
)
echo.
pause
exit /b 1
