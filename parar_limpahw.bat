@echo off
chcp 65001 >nul
title Finalizador LimpaHW
cd /d "%~dp0"

echo ==============================================================
echo        LIMPA-HW - Encerrando Servico Web (Porta 8501)
echo ==============================================================

set "FOUND=0"

:: Encerra processos que estao escutando na porta 8501
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8501" ^| findstr "LISTENING"') do (
    if not "%%a"=="0" (
        echo Encerrando processo na porta 8501 ^(PID: %%a^)...
        taskkill /F /T /PID %%a >nul 2>&1
        set "FOUND=1"
    )
)

:: Encerra a janela em segundo plano "LimpaHW Service", caso ainda exista
taskkill /F /T /FI "WINDOWTITLE eq LimpaHW Service*" >nul 2>&1

if "%FOUND%"=="1" (
    echo.
    echo [OK] Servico LimpaHW finalizado com sucesso!
) else (
    echo.
    echo [INFO] Nenhum servico LimpaHW ativo foi encontrado na porta 8501.
)

timeout /t 3 >nul

