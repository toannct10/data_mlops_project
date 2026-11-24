#requires -Version 5.1
. (Join-Path $PSScriptRoot 'portable_common.ps1')
try{Assert-PortableDocker;Invoke-PortableCompose -DockerArgs @('stop','--timeout','45');Write-Host 'Stopped portable services. All persistent volumes preserved.'}catch{[Console]::Error.WriteLine($_.Exception.Message);exit 1}
