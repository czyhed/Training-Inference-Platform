$ErrorActionPreference = 'Stop'

$requiredPorts = 4400, 4401, 4402
$listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $requiredPorts -contains $_.LocalPort } |
  Select-Object -ExpandProperty LocalPort -Unique
$missingPorts = $requiredPorts | Where-Object { $_ -notin $listeners }

if ($missingPorts) {
  Write-Error "Penpot MCP services are not ready. Missing listening ports: $($missingPorts -join ', ')."
  exit 1
}

$pluginConnections = Get-NetTCPConnection -LocalPort 4402 -State Established -ErrorAction SilentlyContinue
if (-not $pluginConnections) {
  Write-Error 'Penpot MCP server is running, but no Penpot plugin is connected. Open the plugin in a design file and keep its window open.'
  exit 2
}

$legacyProxies = Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'mcp-remote.*localhost:4401/sse' }

Write-Output 'Penpot MCP is healthy: HTTP endpoint is ready and one plugin connection is active.'
if ($legacyProxies) {
  Write-Warning "Found $(@($legacyProxies).Count) legacy mcp-remote processes. Restart Codex after switching to http://localhost:4401/mcp."
}
