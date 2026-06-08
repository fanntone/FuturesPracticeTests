@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================
echo    期貨題庫　更新工具
echo ============================
echo.
echo 正在解析所有 PDF 並重新產生 期貨刷題.html ...
echo.
where python >nul 2>nul
if %errorlevel%==0 (
  python build.py
) else (
  py -3 build.py
)
echo.
echo ----------------------------------------
echo 若上方每一行科目都是「✓」即更新成功。
echo 直接使用 期貨刷題.html 即可。
echo ----------------------------------------
pause
