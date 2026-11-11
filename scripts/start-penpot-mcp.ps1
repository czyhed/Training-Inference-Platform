$ErrorActionPreference = 'Stop'

$root = Join-Path $HOME 'penpot-mcp'
$logDir = Join-Path $HOME 'penpot-mcp-logs'

if (-not (Test-Path (Join-Path $root 'mcp-server\dist\index.js'))) {
  throw "Penpot MCP is not built at $root. Run npm install, npm run install:all, and npm run build:all there first."
}

$ports = 4400, 4401, 4402
$listeningPorts = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $ports -contains $_.LocalPort } |
  Select-Object -ExpandProperty LocalPort -Unique

if (@($listeningPorts).Count -eq $ports.Count) {
  $pluginConnection = Get-NetTCPConnection -LocalPort 4402 -State Established -ErrorAction SilentlyContinue
  Write-Output 'Penpot MCP services are already running on ports 4400, 4401, and 4402.'
  if ($pluginConnection) {
    Write-Output 'Penpot plugin is connected.'
  } else {
    Write-Warning 'Penpot plugin is not connected. Open the plugin in a Penpot design file and keep its window open.'
  }
  exit 0
}

if (@($listeningPorts).Count -gt 0) {
  throw "Penpot MCP is partially running on ports $($listeningPorts -join ', '). Run scripts\stop-penpot-mcp.ps1 before restarting it."
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdout = Join-Path $logDir 'start-all.out.log'
$stderr = Join-Path $logDir 'start-all.err.log'
$process = Start-Process -FilePath 'npm.cmd' -ArgumentList @('run', 'start:all') -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Write-Output "Started Penpot MCP process $($process.Id)."

$deadline = (Get-Date).AddSeconds(20)
do {
  Start-Sleep -Milliseconds 500
  $listeningPorts = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $ports -contains $_.LocalPort } |
    Select-Object -ExpandProperty LocalPort -Unique
} while (@($listeningPorts).Count -lt $ports.Count -and (Get-Date) -lt $deadline)

if (@($listeningPorts).Count -lt $ports.Count) {
  throw "Penpot MCP did not become ready. Listening ports: $($listeningPorts -join ', '). Check $stderr."
}

Write-Output 'Penpot MCP services are ready. Open the plugin in a Penpot design file and keep its window open.'
