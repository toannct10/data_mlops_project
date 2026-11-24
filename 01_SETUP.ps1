#requires -Version 5.1
param([ValidateSet('Local','Cloud')][string]$Mode,[ValidateSet('CPU','GPU')][string]$ResourceProfile='CPU',[switch]$EnableCloudWrites,[switch]$SkipBuild)
. (Join-Path $PSScriptRoot 'portable_common.ps1')
try {
    Assert-PortableDocker
    if(-not $Mode){$Mode=Read-Host 'Choose Local or Cloud (default Local)';if(-not $Mode){$Mode='Local'};if($Mode -notin @('Local','Cloud')){throw 'Choose Local or Cloud.'}}
    if(Test-Path -LiteralPath $script:ConfigPath){$cfg=Read-PortableConfig}else{
        $cfg=@{};foreach($line in Get-Content (Join-Path $PSScriptRoot '.env.example') -Encoding UTF8){if($line -match '^([A-Z][A-Z0-9_]*)=(.*)$'){$cfg[$matches[1]]=$matches[2]}}
        foreach($key in @('PORTABLE_DB_PASSWORD','PORTABLE_AI_PASSWORD','PORTABLE_GRAFANA_PASSWORD','PORTABLE_AIRFLOW_PASSWORD')){$cfg[$key]=New-PortableSecret}
    }
    $cfg['PORTABLE_MODE']=$Mode.ToLowerInvariant();$cfg['PORTABLE_RESOURCE_PROFILE']=$ResourceProfile
    $cfg['PORTABLE_CLOUD_WRITES']='false';$cfg['PORTABLE_CROSS_BACKEND']='postgres'
    Save-PortableConfig $cfg
    $mem=[double](& docker info --format '{{.MemTotal}}')/1GB
    Write-Host ('Docker memory allocation: {0:N1} GB. Full AI needs additional headroom; co-resident stacks may prevent safe testing.' -f $mem)
    $drive=Get-PSDrive -Name ([IO.Path]::GetPathRoot($PSScriptRoot).Substring(0,1))
    if($drive.Free -lt 8GB){throw 'Less than 8 GB free on extraction drive. Free space without deleting demo data.'}
    Write-Host ('Extraction-drive free space: {0:N1} GB' -f ($drive.Free/1GB))
    if($mem -lt 7){Write-Warning 'Docker allocation is below the build-host allocation. Full AI minimum has not been measured; startup may fail under pressure.'}
    if($ResourceProfile -eq 'GPU'){
        if($cfg['PORTABLE_QWEN_IMAGE'] -notmatch 'cuda'){throw 'GPU profile requires an explicitly selected compatible CUDA llama.cpp image. Default is CPU; GPU acceptance is unverified.'}
        & docker run --rm --gpus all --entrypoint /bin/sh $cfg['PORTABLE_QWEN_IMAGE'] -c 'test -e /dev/nvidiactl'
        if($LASTEXITCODE -ne 0){throw 'GPU runtime probe failed. Use CPU profile.'}
        $cfg['PORTABLE_GPU_LAYERS']='99';Save-PortableConfig $cfg
    }
    Assert-PortableModels
    Assert-PortablePorts
    if(-not $SkipBuild){Invoke-PortableCompose -DockerArgs @('build','rag-backend','airflow')}
    if($Mode -eq 'Cloud'){
        if(-not $EnableCloudWrites){throw 'Cloud writes remain disabled. Configure authorized isolated resources, verify permissions, then rerun with -Mode Cloud -EnableCloudWrites.'}
        Invoke-PortableCompose -DockerArgs @('run','--rm','--no-deps','-T','rag-backend','python','-m','src.portable.control','cloud-preflight')
        $cfg['PORTABLE_CLOUD_WRITES']='true';$cfg['PORTABLE_CROSS_BACKEND']='snowflake';Save-PortableConfig $cfg
    }
    Invoke-PortableCompose -DockerArgs @('up','-d','postgres','data-init')
    Invoke-PortableCompose -DockerArgs @('run','--rm','--no-deps','-T','rag-backend','python','-m','src.portable.control','bootstrap-ready')
    Write-Host 'Setup ready. Run .\02_START.ps1. Existing portable volumes/configuration were reused.'
} catch {[Console]::Error.WriteLine($_.Exception.Message);exit 1}
