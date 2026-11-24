#requires -Version 5.1
. (Join-Path $PSScriptRoot 'portable_common.ps1')
try {
    Assert-PortableDocker;Assert-PortableModels;Assert-PortablePorts
    $cfg=Read-PortableConfig
    if($cfg['PORTABLE_MODE'] -eq 'cloud'){
        if($cfg['PORTABLE_CLOUD_WRITES'] -ne 'true'){throw 'Cloud writes are disabled; run explicit Cloud setup first.'}
        Invoke-PortableCompose -DockerArgs @('run','--rm','--no-deps','-T','rag-backend','python','-m','src.portable.control','cloud-preflight')
    }
    Invoke-PortableCompose -DockerArgs @('up','-d','--wait','--wait-timeout','900')
    & (Join-Path $PSScriptRoot '03_HEALTH_CHECK.ps1')
    if($LASTEXITCODE -ne 0){throw 'Required service health failed.'}
    foreach($item in @(@('Business Intelligence','PORTABLE_BI_PORT'),@('Ordering Web','PORTABLE_ORDERING_PORT'),@('Airflow','PORTABLE_AIRFLOW_PORT'),@('Prometheus','PORTABLE_PROM_PORT'),@('Grafana','PORTABLE_GRAFANA_PORT'))){Write-Host ($item[0]+': http://localhost:'+$cfg[$item[1]])}
} catch {[Console]::Error.WriteLine($_.Exception.Message);exit 1}
