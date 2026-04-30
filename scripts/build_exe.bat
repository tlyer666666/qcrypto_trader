@echo off
setlocal
cd /d "%~dp0\.."
python -m pip install -e ".[build]"
pyinstaller qcrypto_trader.spec

