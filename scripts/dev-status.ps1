$ErrorActionPreference = "SilentlyContinue"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LogsDir = Join-Path $RepoRoot ".logs"

function Get-HealthLine {
  param(
    [string]$Name,
    [string]$Url
  )

  try {
    $resp = Invoke-WebRequest -UseBasicParsing $Url -TimeoutSec 5
    "{0,-10} {1}" -f $Name, $resp.StatusCode
  }
  catch {
    "{0,-10} down" -f $Name
  }
}

function Get-PidLine {
  param(
    [string]$Name,
    [string]$PidFile
  )

  $path = Join-Path $LogsDir $PidFile
  if (-not (Test-Path $path)) {
    if ($Name -eq "frontend") {
      $fallback = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine -like '*vite.js*' -and $_.CommandLine -like '*5174*'
      } | Select-Object -First 1
      if ($fallback) {
        "{0,-10} running (no pid file)" -f $Name
        return
      }
    }
    "{0,-10} missing" -f $Name
    return
  }

  $pidValue = Get-Content $path | Select-Object -First 1
  $proc = Get-Process -Id ([int]$pidValue) -ErrorAction SilentlyContinue
  if ($proc) {
    "{0,-10} pid={1}" -f $Name, $pidValue
  }
  else {
    "{0,-10} stale pid={1}" -f $Name, $pidValue
  }
}

Push-Location $RepoRoot
try {
  Write-Output "Health"
  Get-HealthLine -Name "backend" -Url "http://127.0.0.1:8801/health"
  Get-HealthLine -Name "frontend" -Url "http://127.0.0.1:5174"

  Write-Output ""
  Write-Output "Pids"
  Get-PidLine -Name "backend" -PidFile "backend.pid"
  Get-PidLine -Name "celery" -PidFile "celery.pid"
  Get-PidLine -Name "beat" -PidFile "beat.pid"
  Get-PidLine -Name "frontend" -PidFile "frontend.pid"

  Write-Output ""
  Write-Output "Docker"
  docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | Select-String "duckdock-"
}
finally {
  Pop-Location
}
