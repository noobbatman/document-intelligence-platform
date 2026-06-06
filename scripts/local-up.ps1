[CmdletBinding()]
param(
    [switch]$Rebuild,
    [switch]$SkipMigrate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example"
}

$composeArgs = @("compose", "up", "-d")
if ($Rebuild) {
    $composeArgs += "--build"
}

Write-Host "> docker $($composeArgs -join ' ')"
& docker @composeArgs
if ($LASTEXITCODE -ne 0) {
    throw "docker compose up failed."
}

if (-not $SkipMigrate) {
    Write-Host "> docker compose exec api alembic upgrade head"
    & docker compose exec api alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "Database migration failed."
    }
}

Write-Host ""
Write-Host "Local services are up."
Write-Host "App UI:        http://localhost:8000/"
Write-Host "API docs:      http://localhost:8000/docs"
Write-Host "Flower:        http://localhost:5555"
Write-Host "Grafana:       http://localhost:13000"
Write-Host "MinIO console: http://localhost:9001"
