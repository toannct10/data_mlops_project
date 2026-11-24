#requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$DatabaseName,[string]$WarehouseName='FOODY_WH')
$ErrorActionPreference='Stop'
try {
    foreach($value in @($DatabaseName,$WarehouseName)){if($value -cnotmatch '^[A-Z][A-Z0-9_]*$'){throw 'Use uppercase simple identifiers only.'}}
    if($DatabaseName -eq 'FOODY'){throw 'Accepted database FOODY is blocked.'}
    $text=Get-Content (Join-Path $PSScriptRoot 'bootstrap/cloud_owner_template.sql') -Raw -Encoding UTF8
    $text=$text.Replace('__DATABASE__',$DatabaseName).Replace('__WAREHOUSE__',$WarehouseName)
    $folder=Join-Path $PSScriptRoot 'diagnostics';New-Item -ItemType Directory -Force $folder | Out-Null
    $path=Join-Path $folder 'cloud_owner_review.sql';[IO.File]::WriteAllText($path,$text,(New-Object Text.UTF8Encoding($false)))
    Write-Host ('Rendered only; no SQL executed: '+$path)
    Write-Host 'Review all statements and use a separately authorized owner session. Grant runtime roles to authorized users separately; never embed administrator credentials.'
} catch {[Console]::Error.WriteLine($_.Exception.Message);exit 1}
