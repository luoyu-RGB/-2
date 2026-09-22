# 一键启动：初始化离线演示库 → 启动统一后端
# 前端页面与接口文档都在同一个端口：http://127.0.0.1:8000/
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

Write-Host "使用解释器：$python" -ForegroundColor Cyan
Write-Host "1) 初始化数据库（幂等，已有数据会跳过）..." -ForegroundColor Cyan
& $python scripts\init_db.py
if ($LASTEXITCODE -ne 0) { Write-Host "初始化失败，请检查 .env 与 docs/sql 脚本。" -ForegroundColor Red; exit 1 }

Write-Host "2) 启动 FastAPI（前端 http://127.0.0.1:8000/ ，接口文档 /docs ）..." -ForegroundColor Cyan
& $python -m uvicorn app.main:app --reload --port 8000
