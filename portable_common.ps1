#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:PortableRoot = $PSScriptRoot
$script:ConfigPath = Join-Path $script:PortableRoot '.env.portable'
function Read-PortableConfig {
    if (-not (Test-Path -LiteralPath $script:ConfigPath)) { throw 'Run 01_SETUP.ps1 first; .env.portable is missing.' }
    $result=@{}
    foreach ($line in Get-Content -LiteralPath $script:ConfigPath -Encoding UTF8) {
        if ($line -match '^([A-Z][A-Z0-9_]*)=(.*)$') { $result[$matches[1]]=$matches[2].Trim() }
    }
    if ($result['COMPOSE_PROJECT_NAME'] -notmatch '^foody-portable[-a-z0-9]*$') { throw 'Only stable foody-portable project names are allowed. Accepted projects are blocked.' }
    return $result
}
function New-PortableSecret {
    $bytes=New-Object byte[] 24
    $rng=[System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-','').ToLowerInvariant()
}
function Save-PortableConfig($Config) {
    $lines=@($Config.Keys | Sort-Object | ForEach-Object { $_+'='+$Config[$_] })
    [IO.File]::WriteAllLines($script:ConfigPath,$lines,(New-Object Text.UTF8Encoding($false)))
}
function Invoke-PortableCompose {
    param([string[]]$DockerArgs,[switch]$Capture)
    $config=Read-PortableConfig
    $effective=@{};foreach($key in $config.Keys){$effective[$key]=$config[$key]}
    # Explicitly erase inherited cloud credentials and rollout flags in Local mode.
    if ($config['PORTABLE_MODE'] -ne 'cloud') {
        foreach($key in @('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_SESSION_TOKEN','SNOWFLAKE_ACCOUNT','SNOWFLAKE_USER','SNOWFLAKE_PASSWORD')){$effective[$key]=''}
        $effective['PORTABLE_CLOUD_WRITES']='false';$effective['PORTABLE_CROSS_BACKEND']='postgres'
    }
    $saved=@{}
    foreach($key in $effective.Keys){$saved[$key]=[Environment]::GetEnvironmentVariable($key,'Process');[Environment]::SetEnvironmentVariable($key,$effective[$key],'Process')}
    $argsList=@('compose','--project-name',$config['COMPOSE_PROJECT_NAME'],'--env-file',$script:ConfigPath,'-f',(Join-Path $script:PortableRoot 'docker-compose.portable.yml'))
    $localOverride=$null
    if($config['PORTABLE_MODE'] -ne 'cloud'){
        # Windows removes process environment variables when set to ''. Compose
        # then falls back to saved Cloud values in .env.portable. A final env-file
        # layer supplies explicit empty values instead. It contains no secrets.
        $localOverride=[IO.Path]::GetTempFileName()
        $localLines=@('AWS_ACCESS_KEY_ID=','AWS_SECRET_ACCESS_KEY=','AWS_SESSION_TOKEN=','SNOWFLAKE_ACCOUNT=','SNOWFLAKE_USER=','SNOWFLAKE_PASSWORD=','PORTABLE_CLOUD_WRITES=false','PORTABLE_CROSS_BACKEND=postgres')
        [IO.File]::WriteAllLines($localOverride,$localLines,(New-Object Text.UTF8Encoding($false)))
        $argsList+=@('--env-file',$localOverride)
    }
    if($config['PORTABLE_RESOURCE_PROFILE'] -eq 'GPU'){$argsList+=@('-f',(Join-Path $script:PortableRoot 'docker-compose.portable.gpu.yml'))}
    Push-Location $script:PortableRoot
    try {
        if($Capture){$output=& docker @argsList @DockerArgs 2>&1; if($LASTEXITCODE -ne 0){throw 'Compose operation failed; run 05_DIAGNOSE.ps1. Private configuration was not printed.'};return ($output -join "`n")}
        & docker @argsList @DockerArgs
        if($LASTEXITCODE -ne 0){throw 'Compose operation failed; inspect sanitized diagnostics.'}
    } finally {
        Pop-Location
        foreach($key in $saved.Keys){[Environment]::SetEnvironmentVariable($key,$saved[$key],'Process')}
        if($localOverride){[IO.File]::Delete($localOverride)}
    }
}
function Assert-PortableDocker {
    if(-not (Get-Command docker -ErrorAction SilentlyContinue)){throw 'Install Docker Desktop and open it first.'}
    $null=& docker info --format '{{.ServerVersion}}' 2>&1
    if($LASTEXITCODE -ne 0){throw 'Docker Engine is not reachable. Start Docker Desktop, wait for Engine running, then retry.'}
}
function Assert-PortableModels {
    $manifest=Get-Content (Join-Path $script:PortableRoot 'config/portable/model_manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $problems=@()
    foreach($file in $manifest.files){
        $path=Join-Path $script:PortableRoot $file.path
        if(-not (Test-Path -LiteralPath $path)){$problems+=('Missing: '+$file.path);continue}
        if((Get-Item -LiteralPath $path).Length -ne $file.bytes -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.sha256){$problems+=('Wrong version: '+$file.path)}
    }
    if($problems.Count){throw (($problems -join "`n")+"`nAll required AI models are bundled in this authorized INTERNAL release. Re-extract the current checksum-verified ZIP if a file is missing or corrupt, then run .\06_INSTALL_MODELS.ps1 to verify. No separate private model pack, replacement version or automatic model download is required.")}
}
function Assert-PortablePorts {
    $cfg=Read-PortableConfig
    $effective=Invoke-PortableCompose -DockerArgs @('config','--format','json') -Capture | ConvertFrom-Json
    $own=(& docker ps --filter ('label=com.docker.compose.project='+$cfg['COMPOSE_PROJECT_NAME']) --format '{{.Ports}}') -join ' '
    $ownIds=@(& docker ps --filter ('label=com.docker.compose.project='+$cfg['COMPOSE_PROJECT_NAME']) --format '{{.ID}}')
    $allBindings=@(& docker ps --format '{{.ID}}|{{.Ports}}')
    if($LASTEXITCODE -ne 0){throw 'Cannot inspect current Docker port bindings.'}
    $otherPorts=($allBindings | Where-Object { ($_ -split '\|',2)[0] -notin $ownIds }) -join ' '
    foreach($property in $effective.services.PSObject.Properties){
        if(-not $property.Value.PSObject.Properties['ports']){continue}
        foreach($mapping in $property.Value.ports){
            $port=[int]$mapping.published
            if($otherPorts -match (':'+$port+'->')){throw ('Port '+$port+' conflicts for '+$property.Name+'. Change its PORTABLE_*_PORT; another Docker project owns this mapping.')}
            $client=New-Object Net.Sockets.TcpClient
            try{$pending=$client.BeginConnect('127.0.0.1',$port,$null,$null);$occupied=$pending.AsyncWaitHandle.WaitOne(300,$false) -and $client.Connected}finally{$client.Close()}
            if($occupied -and $own -notmatch (':'+$port+'->')){throw ('Port '+$port+' conflicts for '+$property.Name+'. Change its PORTABLE_*_PORT; no existing listener was stopped.')}
            $listener=New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,$port)
            try{$listener.ExclusiveAddressUse=$true;$listener.Start()}catch{
                if($own -notmatch (':'+$port+'->')){throw ('Port '+$port+' conflicts for '+$property.Name+'. Choose another PORTABLE_*_PORT in .env.portable (for example '+($port+100)+'). No unrelated service was stopped.')}
            }finally{$listener.Stop()}
        }
    }
}
