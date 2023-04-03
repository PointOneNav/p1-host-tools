@echo off

set PARENT_DIR=%~dp0
set PARENT_DIR=%PARENT_DIR:~0,-1%

set P1_RUNNER_DIR=%PARENT_DIR%\..\..
set CONFIG_LOADER_DIR=%P1_RUNNER_DIR%\user_config_loader

echo Building from %P1_RUNNER_DIR%.
pushd "%P1_RUNNER_DIR%"

if not exist "%P1_RUNNER_DIR%\venv_windows\" (
    echo Creating Python virtual environment...
    python -m venv venv_windows
    if %errorlevel% neq 0 (
        echo Error creating Python virtual environment.
        exit /b 2
    )
)

rem Note: You can't activate a venv within a batch script (it'll drop you into
rem an interactive console), so we prefix everything with the Scripts dir to
rem run them manually without the venv.
set VENV=%P1_RUNNER_DIR%\venv_windows\Scripts

echo Updating Python requirements.
rem Note: Setting -I to force pip to reinstall fusion-engine-client when its
rem commit hash changes but its setup.py version does not.
"%VENV%\pip" install -I -r requirements.txt
if %errorlevel% neq 0 (
    echo Error installing Python requirements.
    exit /b 2
)

"%VENV%\pip" install PyInstaller
if %errorlevel% neq 0 (
    echo Error installing PyInstaller package.
    exit /b 2
)

echo.
echo ********************************************************************************
echo Compiling p1_runner...
"%VENV%\pyinstaller" ^
    --distpath ./pyinstaller_dist --workpath ./pyinstaller_build ^
    --onefile --paths venv\Lib\site-packages --paths . ^
    --name p1_runner bin\runner.py
if %errorlevel% neq 0 (
    echo Error building p1_runner.
    exit /b 2
)

echo.
echo ********************************************************************************
echo Compiling config_tool...
"%VENV%\pyinstaller" ^
    --distpath ./pyinstaller_dist --workpath ./pyinstaller_build ^
    --onefile --paths venv\Lib\site-packages --paths . --paths %CONFIG_LOADER_DIR% ^
    --name config_tool bin\config_tool.py
if %errorlevel% neq 0 (
    echo Error building config_tool.
    exit /b 2
)

echo.
echo ********************************************************************************
echo Compiling device_bridge...
"%VENV%\pyinstaller" ^
    --distpath ./pyinstaller_dist --workpath ./pyinstaller_build ^
    --onefile --paths venv\Lib\site-packages --paths . ^
    --name device_bridge bin\device_bridge.py
if %errorlevel% neq 0 (
    echo Error building device_bridge.
    exit /b 2
)

popd
