@echo off
setlocal
cd /d "%~dp0"

set "CONDA_BAT=C:\Users\filip\Miniconda3\condabin\conda.bat"
if not exist "%CONDA_BAT%" (
  echo Could not find conda at %CONDA_BAT%
  pause
  exit /b 1
)

REM Optional local secrets file (gitignored). Example line:
REM OPENAI_API_KEY=sk-...
if exist "%~dp0.env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%~dp0.env") do (
    if not "%%A"=="" if "%%B"=="" (
      rem skip malformed
    ) else (
      if not defined %%A set "%%A=%%B"
    )
  )
)

echo Starting ULP Pipeline Dashboard...
echo Keep this window open while using the dashboard.
echo Press Ctrl+C to stop.
if defined OPENAI_API_KEY (
  echo OpenAI key: set
) else (
  echo OpenAI key: not set — put OPENAI_API_KEY=... in .env for Whisper/API jobs
)
echo.

call "%CONDA_BAT%" run -n expenses python pipeline_dashboard.py --host 127.0.0.1 --port 8787
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
  echo.
  echo Dashboard exited with code %EXITCODE%.
  pause
)
exit /b %EXITCODE%
