@echo off
setlocal enabledelayedexpansion

:: 1. Enable Native ANSI Virtual Terminal Processing for Windows CMD
reg add HKCU\Console /v VirtualTerminalLevel /t REG_DWORD /d 1 /f >nul 2>&1
for /F %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"

:: 2. Define Colors
set "CYAN=!ESC![36m"
set "MAGENTA=!ESC![35m"
set "GREEN=!ESC![32m"
set "GRAY=!ESC![90m"
set "YELLOW=!ESC![33m"
set "RED=!ESC![31m"
set "RESET=!ESC![0m"

set "BOTS_DIR=%~dp0bots"
if not exist "%BOTS_DIR%\bot_info\bot_logs" mkdir "%BOTS_DIR%\bot_info\bot_logs"
if not exist "%BOTS_DIR%\bot_info\bot_startup_scripts" mkdir "%BOTS_DIR%\bot_info\bot_startup_scripts"
if not exist "%BOTS_DIR%\bot_info\python_modules" mkdir "%BOTS_DIR%\bot_info\python_modules"

:MENU
cls
echo !CYAN!===================================================================!RESET!
echo                    !MAGENTA!DISCORD BOT MANAGER!RESET! !GRAY!(WINDOWS)!RESET!
echo !CYAN!===================================================================!RESET!
echo.
echo !CYAN![AVAILABLE BOTS]!RESET!

set count=0
set "bot_list="
if exist "%BOTS_DIR%" (
    for %%F in ("%BOTS_DIR%\*.py") do (
        set /a count+=1
        set "bot_name_!count!=%%~nxF"
        set "bot_path_!count!=%BOTS_DIR%"
        set "bot_list=!bot_list! %%~nxF"
    )
)

:: SINGLE fast PowerShell call just for process matching
if %count% GTR 0 (
    powershell -NoProfile -Command "$onlines = (Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty CommandLine) -join ' '; $bots = '!bot_list!'.Trim().Split(' ', [StringSplitOptions]::RemoveEmptyEntries); for ($i=0; $i -lt $bots.Count; $i++) { $name = $bots[$i]; $idx = $i + 1; $padded = \"  $idx. $name\".PadRight(35); if ($onlines -match [regex]::Escape($name)) { Write-Host $padded -NoNewline; Write-Host '[ ONLINE ]' -ForegroundColor Green } else { Write-Host $padded -NoNewline; Write-Host '[ OFFLINE ]' -ForegroundColor DarkGray } }"
) else (
    echo !YELLOW!  No .py scripts found in: %BOTS_DIR%!RESET!
)

echo.
echo !CYAN!===================================================================!RESET!
echo Options:
echo   !MAGENTA![1-%count%]!RESET! Toggle Bot   (Starts bot if offline / Stops bot if online)
echo   !MAGENTA![C]!RESET!     Create Bot   (Generate a new bot with a custom prompt)
echo   !MAGENTA![E]!RESET!     Edit Config  (Change variables, models, or manage keywords)
echo   !MAGENTA![H]!RESET!     History      (Set the global max history length)
echo   !MAGENTA![A]!RESET!     API Keys     (Add or update Discord/DeepInfra/Giphy keys)
echo   !MAGENTA![K]!RESET!     Kill All     (Force close every running bot process)
echo   !MAGENTA![R]!RESET!     Refresh      (Reload the menu and online statuses)
echo   !MAGENTA![Q]!RESET!     Quit         (Close this manager window)
echo.
set /p choice="Select an option: "

if /i "%choice%"=="Q" exit /b
if /i "%choice%"=="R" goto MENU
if /i "%choice%"=="K" goto KILL_ALL
if /i "%choice%"=="C" goto CREATE_BOT
if /i "%choice%"=="H" goto SET_HISTORY
if /i "%choice%"=="A" goto ADD_KEY
if /i "%choice%"=="E" goto EDIT_CONFIG

if %choice% GEQ 1 if %choice% LEQ %count% (
    call :TOGGLE_BOT %choice%
    goto MENU
)

echo !RED![ERROR] Invalid choice.!RESET!
timeout /t 2 >nul
goto MENU

:TOGGLE_BOT
set "TARGET_NAME=!bot_name_%1!"
set "TARGET_PATH=!bot_path_%1!"
set "SCRIPT_FILE=!bot_name_%1!"

cls
echo !CYAN!Managing: %TARGET_NAME%!RESET!
echo !CYAN!-------------------------------------------------------------------!RESET!

powershell -NoProfile -ExecutionPolicy Bypass -Command "$name='%TARGET_NAME%'; $p = Get-CimInstance Win32_Process -Filter \"Name = 'python.exe' or Name = 'pythonw.exe'\" | Where-Object { $_.CommandLine -like ('*' + $name + '*') }; if ($p) { Write-Host ('[STOPPING] Terminating PID {0}...' -f $p.ProcessId) -ForegroundColor Yellow; Stop-Process -Id $p.ProcessId -Force; Write-Host '[SUCCESS] Bot process stopped.' -ForegroundColor Red; exit 0 } else { exit 1 }"
if %errorlevel%==0 (
    echo.
    pause
    goto :EOF
)

:: Pre-launch Env Check via a temporary .ps1 file
set "PS_CHECK=%TEMP%\env_check.ps1"
echo param($scriptPath, $envPath) > "%PS_CHECK%"
echo $pyTxt = Get-Content $scriptPath -Raw >> "%PS_CHECK%"
echo $envTxt = if(Test-Path $envPath) { Get-Content $envPath } else { @() } >> "%PS_CHECK%"
echo $m = @() >> "%PS_CHECK%"
echo if($pyTxt -match 'TOKEN\s*=\s*os\.getenv\("([^"']+)"\)') { if(-not ($envTxt -match "^$($Matches[1])=")) { $m += $Matches[1] } } >> "%PS_CHECK%"
echo if($pyTxt -match 'API_KEY\s*=\s*os\.getenv\("([^"']+)"\)') { if(-not ($envTxt -match "^$($Matches[1])=")) { $m += $Matches[1] } } >> "%PS_CHECK%"
echo if($pyTxt -match 'ENABLE_GIFS\s*=\s*True' -and $pyTxt -match 'GIPHY_KEY\s*=\s*os\.getenv\("([^"']+)"\)') { if(-not ($envTxt -match "^$($Matches[1])=")) { $m += $Matches[1] } } >> "%PS_CHECK%"
echo if($m.Count -gt 0) { Write-Host '[ERROR] Missing keys in bot_info\.env:' -ForegroundColor Red; foreach($k in $m) { Write-Host " - $k" -ForegroundColor Red }; exit 1 } >> "%PS_CHECK%"
echo exit 0 >> "%PS_CHECK%"

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_CHECK%" "%TARGET_PATH%\%SCRIPT_FILE%" "%BOTS_DIR%\bot_info\.env"
if errorlevel 1 (
    echo.
    pause
    goto :EOF
)

:: Launch detached
powershell -NoProfile -ExecutionPolicy Bypass -Command "$name='%TARGET_NAME%'; $path='%TARGET_PATH%'; $script='%SCRIPT_FILE%'; Write-Host '[STARTING] Launching detached process...' -ForegroundColor Cyan; $py = (Get-Command python.exe -ErrorAction SilentlyContinue).Path; if (-not $py) { $py = 'python.exe' }; $startupDir = [System.IO.Path]::Combine($path, 'bot_info', 'bot_startup_scripts'); $logDir = [System.IO.Path]::Combine($path, 'bot_info', 'bot_logs'); $batPath = [System.IO.Path]::Combine($startupDir, ('headless_start_' + $name + '.bat')); $logFile = [System.IO.Path]::Combine($logDir, ('debug_' + $name + '.log')); $scriptPath = [System.IO.Path]::Combine($path, $script); $lines = @('@echo off', ('\"' + $py + '\" \"' + $scriptPath + '\" > \"' + $logFile + '\" 2>&1')); Set-Content -Path $batPath -Value $lines; $wmiArgs = @{ CommandLine = ('cmd.exe /c \"' + $batPath + '\"'); CurrentDirectory = $path }; Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments $wmiArgs > $null; Start-Sleep -Seconds 3; $chk = Get-CimInstance Win32_Process -Filter \"Name = 'python.exe' or Name = 'pythonw.exe'\" | Where-Object { $_.CommandLine -like ('*' + $name + '*') }; if ($chk) { Write-Host '[SUCCESS] Bot started successfully in background' -ForegroundColor Green } else { Write-Host ('[ERROR] Bot crashed. Open ' + $logFile + ' to see why') -ForegroundColor Red }"
echo.
pause
goto :EOF

:EDIT_CONFIG
cls
echo !CYAN!=== SELECT BOT TO EDIT ===!RESET!
echo.
set tcount=0
for %%F in ("%BOTS_DIR%\*.py") do (
    set /a tcount+=1
    echo   !MAGENTA!!tcount!.!RESET! %%~nxF
)
echo   !MAGENTA!0.!RESET! Go Back
echo.
set /p "bot_choice=Choice (number): "
if "%bot_choice%"=="0" goto MENU
set "target="
for /L %%I in (1,1,%tcount%) do (
    if "%bot_choice%"=="%%I" set "target=!bot_path_%%I!\!bot_name_%%I!"
)
if "!target!"=="" goto MENU

if not exist "%BOTS_DIR%\bot_info\python_modules\bot_editor.py" (
    echo !RED![ERROR] bot_editor.py not found in bot_info\python_modules!RESET!
    timeout /t 3 >nul
    goto MENU
)

python "%BOTS_DIR%\bot_info\python_modules\bot_editor.py" "!target!"
goto MENU

:SET_HISTORY
cls
echo !CYAN!=== SET GLOBAL HISTORY LENGTH ===!RESET!
echo.
set "GLOBAL_HISTORY=200"
if exist "%BOTS_DIR%\bot_info\.env" (
    for /f "tokens=1,2 delims==" %%A in ('type "%BOTS_DIR%\bot_info\.env"') do (
        if "%%A"=="MAX_HISTORY_LENGTH" set "GLOBAL_HISTORY=%%B"
    )
)
echo !YELLOW!Current Max History Length: !GLOBAL_HISTORY!!RESET!
echo.
set /p "new_hist=Enter new global history length (e.g. 200) or 0 to Go Back: "
if "!new_hist!"=="0" goto MENU
if "!new_hist!"=="" goto MENU

powershell -NoProfile -ExecutionPolicy Bypass -Command "$k='MAX_HISTORY_LENGTH'; $v='!new_hist!'; $f='%BOTS_DIR%\bot_info\.env'; if(!(Test-Path $f)){New-Item $f -Force | Out-Null}; $c=Get-Content $f; $l=\"$k=$v\"; if($c -match \"^^$k=\"){($c -replace \"^^$k=.*\",$l) | Set-Content $f}else{Add-Content $f $l}; Write-Host \"`n[SUCCESS] Set global history length to $v\" -ForegroundColor Green"
echo.
pause
goto MENU

:KILL_ALL
cls
echo !YELLOW![INFO] Terminating all bot processes...!RESET!
powershell -NoProfile -ExecutionPolicy Bypass -Command "$procs = Get-CimInstance Win32_Process -Filter \"Name = 'pythonw.exe' or Name = 'python.exe'\" | Where-Object { $_.CommandLine -like '*\bots\*' }; if ($procs) { foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force; Write-Host ('[KILLED] Stopped PID {0}' -f $p.ProcessId) -ForegroundColor Red } } else { Write-Host 'No running bot processes to kill.' -ForegroundColor Yellow }"
echo.
pause
goto MENU

:CREATE_BOT
cls
echo !CYAN!=== CREATE A NEW BOT ===!RESET!
echo.
set /p "new_bot_name=Enter Bot Name (e.g. ServerBot) or press Enter to cancel: "
if "%new_bot_name%"=="" goto MENU
set /p "new_bot_token=Enter Discord API Token: "
if "%new_bot_token%"=="" goto MENU
set /p "new_bot_prompt=Enter System Prompt Identity: "
if "%new_bot_prompt%"=="" goto MENU

powershell -NoProfile -ExecutionPolicy Bypass -Command "$n='%new_bot_name%'; $t='%new_bot_token%'; $p='%new_bot_prompt%'; $d='%BOTS_DIR%'; $s=$n.ToLower().Replace(' ','_') -replace '[^a-z0-9_]',''; $k=$s.ToUpper()+'_DISCORD_TOKEN'; $f=$d+'\bot_info\.env'; if(!(Test-Path $f)){New-Item $f -Force | Out-Null}; $c=Get-Content $f; $l=\"$k=$t\"; if($c -match \"^$k=\"){($c -replace \"^$k=.*\",$l) | Set-Content $f}else{Add-Content $f $l}; $py=$d+'\'+$s+'.py'; $pSafe=$p -replace '\$','`$'; $txt = @\"`nimport sys, os`nfrom dotenv import load_dotenv`ncurrent_dir = os.path.dirname(os.path.abspath(__file__))`nsys.path.append(os.path.join(current_dir, 'bot_info', 'python_modules'))`nfrom main_bot import run_bot`nfrom bot_discovery import build_universal_media_instructions`nload_dotenv(os.path.join(current_dir, 'bot_info', '.env'))`n`ndef get_system_prompt(enable_image_gen: bool, enable_voice: bool, enable_gifs: bool = True) -> dict:`n    media_section = build_universal_media_instructions(enable_image_gen=enable_image_gen, enable_voice=enable_voice, enable_gifs=enable_gifs)`n    return {'role': 'system', 'content': f\"\"\"$pSafe`n`n### Strict Formatting & Spacing Rules`n- Write your response as a single compact line or tight paragraph with NO blank lines between sentences.`n- Wrap physical actions/emotes in single asterisks like *sighs* or *grins*, never square brackets.`n`n### Memory & Smart Learning (3-Tier Protocol)`n- User Personal Facts (self-stated only): [LEARN_USER: username | concise objective fact]`n- Shared General Knowledge (universal trivia/science): [LEARN_GENERAL: concise objective fact]`n- Server-Specific Lore (Discord server events/jokes only): [LEARN: concise objective fact]`n`n{media_section}\"\"\"}`n`n# --- BOT CONFIGURATION CONSTANTS ---`nTOKEN = os.getenv('$k')`nAPI_KEY = os.getenv('DEEPINFRA_TOKEN')`nGIPHY_KEY = os.getenv('GIPHY_TOKEN')`n`nCHAT_MEMORY_FILE = os.path.join(current_dir, 'bot_info', 'short_term_memory', 'chat_memory.json')`nSHARED_IMAGE_CACHE_FILE = os.path.join(current_dir, 'bot_info', 'short_term_memory', 'shared_image_cache.json')`nLONG_TERM_MEMORY_FILE = os.path.join(current_dir, 'bot_info', 'long_term_memory', '${s}_long_term_memory.json')`nUSER_PROFILES_FILE = os.path.join(current_dir, 'bot_info', 'long_term_memory', '${s}_user_profiles.json')`nCHANNEL_CONFIG_FILE = os.path.join(current_dir, 'bot_info', 'config_files', '${s}_channel_configs.json')`n`nTEXT_MODEL = 'Qwen/Qwen3-235B-A22B-Instruct-2507'`nVISION_MODEL = 'Qwen/Qwen3-VL-235B-A22B-Instruct'`nIMAGE_GEN_MODEL = 'black-forest-labs/FLUX-1-schnell'`nVOICE_MODEL = 'XiaomiMiMo/MiMo-V2.5-tts-voicedesign'`nTTS_VOICE = ''`n`nENABLE_VISION = True`nENABLE_GIFS = True`nENABLE_IMAGE_GEN = True`nENABLE_VOICE = False`n`nTEMPERATURE = 0.75`nTOP_P = 0.88`nOUTPUT_LENGTH = 550`nCONTEXT_WINDOW = 50`nEMOJI_CONTEXT_LENGTH = 12`nCHANCE_TO_RESPOND = 0.01`nRANDOM_EMOJI_CHANCE = 0.35`nDEFAULT_RANDOM_INTERVAL = 0`nTRIGGER_KEYWORDS = ['$s']`n`nif __name__ == '__main__':`n    system_prompt = get_system_prompt(ENABLE_IMAGE_GEN, ENABLE_VOICE, ENABLE_GIFS)`n    run_bot(token=TOKEN, api_key=API_KEY, giphy_key=GIPHY_KEY, system_prompt=system_prompt, chat_memory_file=CHAT_MEMORY_FILE, shared_image_cache_file=SHARED_IMAGE_CACHE_FILE, long_term_memory_file=LONG_TERM_MEMORY_FILE, user_profiles_file=USER_PROFILES_FILE, channel_config_file=CHANNEL_CONFIG_FILE, text_model=TEXT_MODEL, vision_model=VISION_MODEL, image_gen_model=IMAGE_GEN_MODEL, voice_model=VOICE_MODEL, tts_voice=TTS_VOICE, enable_vision=ENABLE_VISION, enable_gifs=ENABLE_GIFS, enable_image_gen=ENABLE_IMAGE_GEN, enable_voice=ENABLE_VOICE, temperature=TEMPERATURE, top_p=TOP_P, output_length=OUTPUT_LENGTH, context_window=CONTEXT_WINDOW, emoji_context_length=EMOJI_CONTEXT_LENGTH, chance_to_respond=CHANCE_TO_RESPOND, random_emoji_chance=RANDOM_EMOJI_CHANCE, default_random_interval=DEFAULT_RANDOM_INTERVAL, keywords=TRIGGER_KEYWORDS)`n\"@; $txt | Set-Content $py -Encoding UTF8; Write-Host \"[SUCCESS] Created $py and saved token to .env\" -ForegroundColor Green"
echo.
pause
goto MENU

:ADD_KEY
cls
echo !CYAN!=== ADD / UPDATE API KEYS ===!RESET!
echo.
echo   !MAGENTA!1.!RESET! DeepInfra API Token
echo   !MAGENTA!2.!RESET! Giphy API Token
echo   !MAGENTA!3.!RESET! Enter custom key name...
echo   !MAGENTA!0.!RESET! Go Back
echo.
set /p "key_choice=Select an option: "
if "%key_choice%"=="0" goto MENU
if "%key_choice%"=="" goto MENU
if "%key_choice%"=="1" set "key_name=DEEPINFRA_TOKEN"
if "%key_choice%"=="2" set "key_name=GIPHY_TOKEN"
if "%key_choice%"=="3" (
    set /p "key_name=Enter Environment Key Name: "
)
if "%key_name%"=="" goto MENU
set /p "key_value=Enter Token Value for %key_name%: "
if "%key_value%"=="" goto MENU

powershell -NoProfile -ExecutionPolicy Bypass -Command "$k='%key_name%'; $v='%key_value%'; $f='%BOTS_DIR%\bot_info\.env'; if(!(Test-Path $f)){New-Item $f -Force | Out-Null}; $c=Get-Content $f; $l=\"$k=$v\"; if($c -match \"^^$k=\"){($c -replace \"^^$k=.*\",$l) | Set-Content $f}else{Add-Content $f $l}; Write-Host \"[SUCCESS] Saved $k to bot_info\.env\" -ForegroundColor Green"
echo.
pause
goto MENU