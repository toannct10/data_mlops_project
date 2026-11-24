#requires -Version 5.1
. (Join-Path $PSScriptRoot 'portable_common.ps1')
try {Assert-PortableDocker;Invoke-PortableCompose -DockerArgs @('exec','-T','rag-backend','python','-m','src.portable.control','health')}catch{[Console]::Error.WriteLine($_.Exception.Message);exit 1}
