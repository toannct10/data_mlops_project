#requires -Version 5.1
. (Join-Path $PSScriptRoot 'portable_common.ps1')
try {
    Assert-PortableModels
    Write-Host 'All bundled local AI models match the accepted manifest. No separate model pack or model download is required. Internal teammate distribution only; upstream licenses remain binding.'
} catch {[Console]::Error.WriteLine($_.Exception.Message);exit 1}
