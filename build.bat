@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1/3] Устанавливаю зависимости...
python -m pip install -r requirements.txt pyinstaller || goto :error

echo [2/3] Рисую иконку...
python make_icon.py || goto :error

echo [3/3] Собираю ZhukoGPT.exe...
python -m PyInstaller --noconfirm --onefile --noconsole ^
    --name ZhukoGPT ^
    --icon assets\zhukogpt.ico ^
    ZhukoGPT.py || goto :error

echo.
echo Готово! Файл: dist\ZhukoGPT.exe
pause
exit /b 0

:error
echo.
echo Ошибка сборки. Посмотри сообщения выше.
pause
exit /b 1
