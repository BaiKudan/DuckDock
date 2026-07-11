param(
  [switch]$WithDockerDown
)

$ErrorActionPreference = "SilentlyContinue"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LogsDir = Join-Path $RepoRoot ".logs"
$PidFiles = @(
  "backend.pid",
  "celery.pid",
  "beat.pid",
  "frontend.pid"
)

Push-Location $RepoRoot
try {
  foreach ($file in $PidFiles) {
    $path = Join-Path $LogsDir $file
    if (Test-Path $path) {
      $pidValue = Get-Content $path | Select-Object -First 1
      if ($pidValue) {
        Stop-Process -Id ([int]$pidValue) -Force -ErrorAction SilentlyContinue
      }
      Remove-Item $path -Force -ErrorAction SilentlyContinue
    }
  }

  $targets = Get-CimInstance Win32_Process | Where-Object {
    $command = $_.CommandLine
    if (-not $command) { return $false }
    return (
      $command -like '*uvicorn app.main:app*--port 8801*' -or
      $command -like '*celery -A app.workers.celery_app worker*' -or
      $command -like '*celery -A app.workers.celery_app beat*' -or
      $command -like '*vite --host 127.0.0.1 --port 5174*' -or
      $command -like '*npm*run*dev*--host 127.0.0.1 --port 5174*' -or
      ($command -like '*vite.js*' -and $command -like '*5174*')
    )
  }

  foreach ($proc in $targets) {
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
  }

  if ($WithDockerDown) {
    # Stop core middleware and any optional observability services if they
    # happen to be running. One-shot init containers are recreated on next up.
    docker compose stop `
      mysql redis minio postgres clickhouse `
      langfuse-worker langfuse-web | Out-Null
  }
}
finally {
  Pop-Location
}
