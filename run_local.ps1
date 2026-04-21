# ─────────────────────────────────────────────────────────────────────────────
# SahulatPay — Local Development Server
# Run this script to start the backend on http://192.168.100.15:8000
#
# FIRST TIME SETUP:
#   1. Copy .env.local → .env  (or set env vars manually below)
#   2. Fill in DATABASE_URL with your Railway PostgreSQL URL:
#      Railway Dashboard → your project → PostgreSQL → Variables → DATABASE_URL
#   3. Run:  .\run_local.ps1
# ─────────────────────────────────────────────────────────────────────────────

# Load .env file if it exists
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match "^\s*([^#][^=]+)=(.*)$") {
            [System.Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim())
        }
    }
    Write-Host "[local] Loaded .env" -ForegroundColor Green
} else {
    Write-Host "[local] No .env file found. Using defaults from config.py" -ForegroundColor Yellow
    Write-Host "        Copy .env.local to .env and fill in DATABASE_URL" -ForegroundColor Yellow
}

# Override for local dev
$env:DEV_MODE = "True"
$env:ENVIRONMENT = "development"
$env:ALLOWED_ORIGINS = "*"
$env:PORT = "8000"
$env:FIREBASE_CREDENTIALS_JSON = "$PSScriptRoot\firebase-adminsdk.json"

Write-Host ""
Write-Host "  Starting SahulatPay backend..." -ForegroundColor Cyan
Write-Host "  Local URL  : http://localhost:8000" -ForegroundColor White
Write-Host "  Android URL: http://192.168.100.15:8000" -ForegroundColor White
Write-Host "  API Docs   : http://localhost:8000/docs" -ForegroundColor White
Write-Host "  DEV_MODE   : $($env:DEV_MODE)" -ForegroundColor White
Write-Host ""

# Start uvicorn — accessible on all interfaces (0.0.0.0) so Android can connect
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
