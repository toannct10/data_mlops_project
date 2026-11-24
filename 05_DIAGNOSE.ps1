#requires -Version 5.1
. (Join-Path $PSScriptRoot 'portable_common.ps1')
try{
    Assert-PortableDocker
    $folder=Join-Path $PSScriptRoot ('diagnostics/'+(Get-Date -Format 'yyyyMMdd_HHmmss'));New-Item -ItemType Directory -Path $folder -Force | Out-Null
    # Only allowlisted status fields and error categories. No raw application logs,
    # inspect environment, credentials, review text, SQL payloads or event bodies.
    Invoke-PortableCompose -DockerArgs @('ps','--format','table {{.Service}}\t{{.State}}\t{{.Health}}\t{{.Ports}}') -Capture | Set-Content (Join-Path $folder 'services.txt') -Encoding UTF8
    (& docker version --format '{{.Server.Version}}') | Set-Content (Join-Path $folder 'docker-version.txt') -Encoding UTF8
    try{Invoke-PortableCompose -DockerArgs @('exec','-T','rag-backend','python','-m','src.portable.control','health') -Capture | Set-Content (Join-Path $folder 'health.json') -Encoding UTF8}catch{'Health command failed; required services may be unavailable.' | Set-Content (Join-Path $folder 'health-error.txt')}
    $cfg=Read-PortableConfig;$lines=@()
    foreach($name in @('postgres','rag-backend','streamlit','ordering-web','qwen','airflow','prometheus','grafana')){
        $id=(& docker ps -a --filter ('label=com.docker.compose.project='+$cfg['COMPOSE_PROJECT_NAME']) --filter ('label=com.docker.compose.service='+$name) --format '{{.ID}}')
        if($id){
            # PS 5.1 maps native stderr to ErrorRecord even for successful reads.
            # Keep it in memory for allowlisted classification, never emit it.
            $previousPreference=$ErrorActionPreference
            try{$ErrorActionPreference='Continue';$raw=@(& docker logs --tail 100 $id 2>&1)}finally{$ErrorActionPreference=$previousPreference}
            foreach($category in @('AccessDenied','OperationalError','ModuleNotFoundError','ConnectionRefusedError','OutOfMemory','ImportError','HTTP 404')){$count=@($raw | Select-String -SimpleMatch $category).Count;if($count){$lines+=($name+': '+$category+' occurrences='+$count)}}
        }
    }
    $lines | Set-Content (Join-Path $folder 'error-categories.txt') -Encoding UTF8
    Write-Host ('Sanitized diagnostic status and error-category log: '+$folder)
}catch{[Console]::Error.WriteLine($_.Exception.Message);exit 1}
