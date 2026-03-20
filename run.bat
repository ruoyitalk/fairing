@echo off
:: fairing — Windows batch entry point
:: Double-click this file, or run from cmd:
::   run.bat
::   run.bat --notebooklm
::   run.bat --chinese
::   run.bat --all

powershell.exe -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
