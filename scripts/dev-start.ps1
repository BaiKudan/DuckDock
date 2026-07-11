param(
  [switch]$NoFrontend
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LogsDir = Join-Path $RepoRoot ".logs"
$BackendRoot = Join-Path $RepoRoot "backend"
$FrontendRoot = Join-Path $RepoRoot "frontend"
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$NpmExe = "C:\Program Files\nodejs\npm.cmd"

function Wait-TcpPort {
  param(
    [string]$TargetHost,
    [int]$Port,
    [int]$TimeoutSeconds = 60
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    try {
      $client = New-Object System.Net.Sockets.TcpClient
      $iar = $client.BeginConnect($TargetHost, $Port, $null, $null)
      if ($iar.AsyncWaitHandle.WaitOne(1000, $false) -and $client.Connected) {
        $client.EndConnect($iar) | Out-Null
        $client.Close()
        return
      }
      $client.Close()
    } catch {
    }
    Start-Sleep -Milliseconds 500
  }
  throw "Timed out waiting for ${TargetHost}:$Port"
}

New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

Push-Location $RepoRoot
try {
  & (Join-Path $PSScriptRoot "dev-stop.ps1")

  # Core DuckDock middleware only. Optional observability components are
  # started from the admin component console after the core is healthy.
  docker compose up -d mysql redis minio | Out-Host
  Wait-TcpPort -TargetHost "127.0.0.1" -Port 3307
  Wait-TcpPort -TargetHost "127.0.0.1" -Port 6379
  Wait-TcpPort -TargetHost "127.0.0.1" -Port 9000

  & $PythonExe -m alembic -c (Join-Path $BackendRoot "alembic.ini") upgrade head | Out-Host

  $backend = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8801" `
    -WorkingDirectory $BackendRoot `
    -RedirectStandardOutput (Join-Path $LogsDir "backend.log") `
    -RedirectStandardError (Join-Path $LogsDir "backend.err.log") `
    -PassThru
  Set-Content -Path (Join-Path $LogsDir "backend.pid") -Value $backend.Id

  $worker = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList "-m", "celery", "-A", "app.workers.celery_app", "worker", "--loglevel=info" `
    -WorkingDirectory $BackendRoot `
    -RedirectStandardOutput (Join-Path $LogsDir "celery.log") `
    -RedirectStandardError (Join-Path $LogsDir "celery.err.log") `
    -PassThru
  Set-Content -Path (Join-Path $LogsDir "celery.pid") -Value $worker.Id

  $beat = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList "-m", "celery", "-A", "app.workers.celery_app", "beat", "--loglevel=info" `
    -WorkingDirectory $BackendRoot `
    -RedirectStandardOutput (Join-Path $LogsDir "beat.log") `
    -RedirectStandardError (Join-Path $LogsDir "beat.err.log") `
    -PassThru
  Set-Content -Path (Join-Path $LogsDir "beat.pid") -Value $beat.Id

  if (-not $NoFrontend) {
    $frontend = Start-Process `
      -FilePath $NpmExe `
      -ArgumentList "run", "dev", "--", "--host", "127.0.0.1", "--port", "5174" `
      -WorkingDirectory $FrontendRoot `
      -RedirectStandardOutput (Join-Path $LogsDir "frontend.log") `
      -RedirectStandardError (Join-Path $LogsDir "frontend.err.log") `
      -PassThru
    Set-Content -Path (Join-Path $LogsDir "frontend.pid") -Value $frontend.Id
  }

  Start-Sleep -Seconds 8

  Write-Output "Backend:  http://127.0.0.1:8801/health"
  Write-Output "Frontend: http://127.0.0.1:5174"
  Write-Output "Optional components can be managed at: http://127.0.0.1:5174/components"

  & (Join-Path $PSScriptRoot "dev-status.ps1")
}
finally {
  Pop-Location
}
