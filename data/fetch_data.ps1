# Default is offline verification. Restore preserves all pinned records; Update
# requires an acknowledged, distinct empty destination and supplied checksums.
# See docs\reproducibility.md. Never use a current -latest URL as a pinned restore.
param(
    [switch]$Smoke,
    [switch]$Aux,
    [switch]$All,
    [ValidateSet("Verify", "Restore", "Update")]
    [string]$Mode = "Verify",
    [string]$DataDirectory,
    [string]$RestoreFrom,
    [string]$SourceUrl,
    [string]$ExpectedSha256,
    [string]$ExpectedMd5,
    [long]$ExpectedSizeBytes = -1,
    [switch]$ConfirmNewSnapshot,
    [string]$AuxManifestPath,
    [switch]$UseRecordedAuxiliaryUrls
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0
$scriptDir = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
if ($DataDirectory) {
    $dataDir = [IO.Path]::GetFullPath($DataDirectory)
} else {
    $dataDir = $scriptDir
}
$auxNames = @(
    "LAU_RG_01M_2021_4326.geojson",
    "NUTS_RG_01M_2021_4326_LEVL_2.geojson",
    "NUTS_RG_01M_2021_4326_LEVL_3.geojson"
)
$doPbf = $All -or -not $Aux
$doAux = $All -or $Aux
$name = "greece-latest.osm.pbf"
$snapshotName = "SNAPSHOT.txt"
if ($Smoke) {
    $name = "malta-latest.osm.pbf"
    $snapshotName = "SNAPSHOT-malta.txt"
}

function Read-KeyRecord([string]$Path) {
    $record = @{}
    foreach ($line in (Get-Content -LiteralPath $Path)) {
        $line = $line.Trim().TrimStart([char]0xFEFF)
        if (-not $line -or $line.StartsWith("#")) { continue }
        if ($line -notmatch "^([A-Za-z0-9_]+)=(.*)$") {
            throw "Malformed snapshot record in $Path"
        }
        $key = $matches[1].ToLowerInvariant()
        if ($record.ContainsKey($key)) { throw "Duplicate snapshot field: $key" }
        $record[$key] = $matches[2].Trim()
    }
    return $record
}

function Add-Expectation($Expectation, [string]$Field, $Value) {
    if ($null -eq $Value -or [string]$Value -eq "") { return }
    if ($Field -eq "size_bytes") {
        [long]$size = 0
        if (-not [long]::TryParse([string]$Value, [ref]$size) -or $size -lt 0) {
            throw "Invalid recorded size for $($Expectation.name)"
        }
        if ($Expectation.size -ge 0 -and $Expectation.size -ne $size) {
            throw "Conflicting recorded sizes for $($Expectation.name)"
        }
        $Expectation.size = $size
        return
    }
    $length = 64
    if ($Field -eq "md5") { $length = 32 }
    $digest = ([string]$Value).Trim().ToLowerInvariant()
    if ($digest -notmatch ("^[0-9a-f]{" + $length + "}$")) {
        throw "Invalid recorded $Field for $($Expectation.name)"
    }
    if ($Expectation.hashes.ContainsKey($Field) -and $Expectation.hashes[$Field] -ne $digest) {
        throw "Conflicting recorded $Field for $($Expectation.name)"
    }
    $Expectation.hashes[$Field] = $digest
}

function Read-Md5([string]$Path, [string]$Name) {
    $digest = $null
    foreach ($line in (Get-Content -LiteralPath $Path)) {
        $line = $line.Trim().TrimStart([char]0xFEFF)
        if (-not $line) { continue }
        $candidate = $null
        if ($line -match "^([0-9a-fA-F]{32})(?:\s+\*?(.+))?$") {
            $candidate = $matches[1].ToLowerInvariant()
            if ($matches.ContainsKey(2) -and $matches[2] -ne $Name) {
                throw "MD5 sidecar filename mismatch in $Path"
            }
        } elseif ($line -match "^MD5\s+\(([^)]+)\)\s*=\s*([0-9a-fA-F]{32})$") {
            if ($matches[1] -ne $Name) { throw "MD5 sidecar filename mismatch in $Path" }
            $candidate = $matches[2].ToLowerInvariant()
        } else {
            throw "Malformed MD5 sidecar: $Path"
        }
        if ($null -ne $digest -and $digest -ne $candidate) {
            throw "Conflicting MD5 sidecar entries in $Path"
        }
        $digest = $candidate
    }
    if (-not $digest) { throw "Empty MD5 sidecar: $Path" }
    return $digest
}

function Read-PbfExpectation([string]$Directory, [string]$Name, [string]$Snapshot) {
    $expectation = @{ name = $Name; hashes = @{}; size = [long]-1 }
    $snapshotPath = Join-Path $Directory $Snapshot
    if (Test-Path -LiteralPath $snapshotPath -PathType Leaf) {
        $record = Read-KeyRecord $snapshotPath
        if ($record["file"] -ne $Name) { throw "Snapshot filename mismatch: $snapshotPath" }
        foreach ($field in @("sha256", "md5", "size_bytes")) {
            Add-Expectation $expectation $field $record[$field]
        }
    }
    $pbfPath = Join-Path $Directory $Name
    $provenancePath = "$pbfPath.provenance.json"
    if (Test-Path -LiteralPath $provenancePath -PathType Leaf) {
        $record = Get-Content -LiteralPath $provenancePath -Raw | ConvertFrom-Json
        $contentProperty = $record.PSObject.Properties["content"]
        if ($null -eq $contentProperty -or $null -eq $contentProperty.Value) {
            throw "Missing content fingerprints in $provenancePath"
        }
        foreach ($field in @("sha256", "md5", "bytes")) {
            $property = $contentProperty.Value.PSObject.Properties[$field]
            if ($null -ne $property) {
                $targetField = $field
                if ($field -eq "bytes") { $targetField = "size_bytes" }
                Add-Expectation $expectation $targetField $property.Value
            }
        }
    }
    if (Test-Path -LiteralPath "$pbfPath.md5" -PathType Leaf) {
        Add-Expectation $expectation "md5" (Read-Md5 "$pbfPath.md5" $Name)
    }
    if ($expectation.hashes.Count -eq 0) {
        throw "No pinned checksums for $Name; restoration cannot establish the frozen snapshot."
    }
    return $expectation
}

function Read-AuxExpectations([string]$Path, [switch]$RequireSha256) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Missing auxiliary checksum inventory: $Path"
    }
    $records = @{}
    foreach ($line in (Get-Content -LiteralPath $Path)) {
        $line = $line.Trim().TrimStart([char]0xFEFF)
        if (-not $line -or $line.StartsWith("#")) { continue }
        $fields = @{}
        foreach ($pair in ($line -split "\s+(?=[A-Za-z0-9_]+=)")) {
            if ($pair -notmatch "^([A-Za-z0-9_]+)=(.*)$") { throw "Malformed auxiliary inventory: $Path" }
            $key = $matches[1].ToLowerInvariant()
            if ($fields.ContainsKey($key)) { throw "Duplicate auxiliary field: $key" }
            $fields[$key] = $matches[2]
        }
        $file = $fields["file"]
        if ($file -notin $auxNames -or $records.ContainsKey($file)) {
            throw "Unexpected/duplicate auxiliary filename: $file"
        }
        $expectation = @{ name = $file; hashes = @{}; size = [long]-1; url = $null }
        foreach ($field in @("sha256", "md5", "size_bytes")) {
            Add-Expectation $expectation $field $fields[$field]
        }
        if ($expectation.hashes.Count -eq 0 -or
            ($RequireSha256 -and -not $expectation.hashes.ContainsKey("sha256"))) {
            throw "Missing required auxiliary checksum for $file"
        }
        if ($fields.ContainsKey("declared_source_url")) {
            $expectation.url = $fields["declared_source_url"]
        } elseif ($fields.ContainsKey("url")) {
            $expectation.url = $fields["url"]
        }
        $records[$file] = $expectation
    }
    foreach ($file in $auxNames) {
        if (-not $records.ContainsKey($file)) { throw "Auxiliary inventory lacks $file" }
        $records[$file]
    }
}

function Content-Matches([string]$Path, $Expectation) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    if ($Expectation.size -ge 0 -and (Get-Item -LiteralPath $Path).Length -ne $Expectation.size) {
        return $false
    }
    foreach ($algorithm in $Expectation.hashes.Keys) {
        $actual = (Get-FileHash -LiteralPath $Path -Algorithm $algorithm).Hash.ToLowerInvariant()
        if ($actual -ne $Expectation.hashes[$algorithm]) { return $false }
    }
    return $true
}

function Assert-Content([string]$Path, $Expectation) {
    if (-not (Content-Matches $Path $Expectation)) {
        throw "Missing input or checksum/size mismatch: $($Expectation.name). Pinned records were not changed."
    }
}

function Assert-Url([string]$Url) {
    [Uri]$parsed = $null
    if (-not [Uri]::TryCreate($Url, [UriKind]::Absolute, [ref]$parsed) -or
        $parsed.Scheme -ne "https" -or $parsed.UserInfo -or $parsed.Fragment -or
        [Uri]::UnescapeDataString($parsed.Query) -match
        "(?i)[?&](?:access[_-]?token|api[_-]?key|token|password|client[_-]?secret|authorization|signature|sig|key|x-amz-credential|x-amz-signature)=") {
        throw "An explicit HTTPS acquisition URL without embedded credentials is required."
    }
}

function Stage-Input($Job, [string]$StagePath) {
    if ($Job.backup) {
        Copy-Item -LiteralPath ([IO.Path]::GetFullPath($Job.backup)) -Destination $StagePath
    } else {
        Assert-Url $Job.url
        Invoke-WebRequest -UseBasicParsing -Uri $Job.url -OutFile $StagePath
    }
    Assert-Content $StagePath $Job.expectation
}

if ($Smoke -and $doAux) { throw "-Smoke cannot be combined with auxiliary acquisition/verification." }
if ($Aux -and $All) { throw "Select -Aux or -All, not both." }
if ($RestoreFrom -and $SourceUrl) { throw "Select -RestoreFrom or -SourceUrl, not both." }
if ($Mode -eq "Verify" -and (
    $PSBoundParameters.ContainsKey("RestoreFrom") -or
    $PSBoundParameters.ContainsKey("SourceUrl") -or $UseRecordedAuxiliaryUrls -or $ConfirmNewSnapshot)) {
    throw "Offline Verify does not accept acquisition options; use -Mode Restore or Update."
}
if ($Mode -eq "Restore" -and $ConfirmNewSnapshot) {
    throw "-ConfirmNewSnapshot is only valid for -Mode Update."
}
if ($Mode -ne "Update" -and (
    $PSBoundParameters.ContainsKey("ExpectedSha256") -or
    $PSBoundParameters.ContainsKey("ExpectedMd5") -or
    $PSBoundParameters.ContainsKey("ExpectedSizeBytes"))) {
    throw "Preservation/restore uses retained checksums; supplied replacement checksums require -Mode Update."
}
if ($Mode -ne "Update" -and $PSBoundParameters.ContainsKey("AuxManifestPath")) {
    throw "Verify/Restore must use the retained auxiliary inventory; an alternate manifest is only valid for Update."
}
if (-not $doPbf -and (
    $PSBoundParameters.ContainsKey("RestoreFrom") -or
    $PSBoundParameters.ContainsKey("SourceUrl") -or
    $PSBoundParameters.ContainsKey("ExpectedSha256") -or
    $PSBoundParameters.ContainsKey("ExpectedMd5") -or
    $PSBoundParameters.ContainsKey("ExpectedSizeBytes"))) {
    throw "PBF backup, URL and checksums are not auxiliary acquisition options."
}
if (-not $doAux -and ($UseRecordedAuxiliaryUrls -or $PSBoundParameters.ContainsKey("AuxManifestPath"))) {
    throw "Auxiliary URLs and manifests require -Aux or -All."
}
if ($UseRecordedAuxiliaryUrls -and $Mode -ne "Restore") {
    throw "-UseRecordedAuxiliaryUrls is only valid for -Mode Restore."
}
if ($Mode -eq "Update") {
    if (-not $ConfirmNewSnapshot -or -not $PSBoundParameters.ContainsKey("DataDirectory")) {
        throw "Update requires -ConfirmNewSnapshot and an explicit distinct empty -DataDirectory."
    }
    if ($All) { throw "PBF and auxiliary new-snapshot updates must be separate." }
    if ($dataDir.TrimEnd("\") -eq $scriptDir.TrimEnd("\") -or
        ((Test-Path -LiteralPath $dataDir) -and
         @(Get-ChildItem -LiteralPath $dataDir -Force).Count -ne 0)) {
        throw "Update cannot overwrite the frozen directory or an existing snapshot destination."
    }
    if ($RestoreFrom) { throw "A local backup is a restore, not a new-snapshot download." }
    if ($doPbf -and -not $ExpectedSha256) { throw "A new PBF requires an externally verified -ExpectedSha256." }
    if ($doAux -and -not $PSBoundParameters.ContainsKey("AuxManifestPath")) {
        throw "New auxiliary acquisition requires an explicit trusted -AuxManifestPath."
    }
}

$jobs = @()
if ($doPbf) {
    if ($Mode -eq "Update") {
        $expectation = @{ name = $name; hashes = @{}; size = [long]-1 }
        Add-Expectation $expectation "sha256" $ExpectedSha256
        Add-Expectation $expectation "md5" $ExpectedMd5
        if ($ExpectedSizeBytes -ne -1) { Add-Expectation $expectation "size_bytes" $ExpectedSizeBytes }
        Assert-Url $SourceUrl
    } else {
        $expectation = Read-PbfExpectation $dataDir $name $snapshotName
    }
    $jobs += @{ expectation = $expectation; target = (Join-Path $dataDir $name); url = $SourceUrl; backup = $RestoreFrom }
}
if ($doAux) {
    if (-not $AuxManifestPath) { $AuxManifestPath = Join-Path (Join-Path $dataDir "external") "AUX_SNAPSHOT.txt" }
    $expectations = @(Read-AuxExpectations $AuxManifestPath -RequireSha256:($Mode -eq "Update"))
    foreach ($expectation in $expectations) {
        $url = $null
        if ($Mode -eq "Update" -or $UseRecordedAuxiliaryUrls) { $url = $expectation.url }
        $jobs += @{ expectation = $expectation; target = (Join-Path (Join-Path $dataDir "external") $expectation.name); url = $url; backup = $null }
    }
}

if ($Mode -eq "Verify") {
    foreach ($job in $jobs) {
        Assert-Content $job.target $job.expectation
        Write-Host "Verified retained bytes: $($job.expectation.name); records unchanged."
    }
    return
}

$needed = @()
foreach ($job in $jobs) {
    if ($Mode -eq "Restore" -and (Content-Matches $job.target $job.expectation)) {
        Write-Host "Already verified; preserved: $($job.expectation.name)"
    } else {
        if (-not $job.backup -and -not $job.url) {
            throw "Restore requires an explicit backup/URL (auxiliary URLs require opt-in): $($job.expectation.name)"
        }
        $needed += $job
    }
}
if ($needed.Count -eq 0) { return }

if (-not (Test-Path -LiteralPath $dataDir -PathType Container)) {
    New-Item -ItemType Directory -Path $dataDir | Out-Null
}
$stageDir = Join-Path $dataDir (".fetch-stage-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $stageDir | Out-Null
$createdTargets = @()
try {
    $staged = @()
    foreach ($job in $needed) {
        $stagePath = Join-Path $stageDir ("input-" + $staged.Count)
        Stage-Input $job $stagePath
        $staged += @{ source = $stagePath; target = $job.target; expectation = $job.expectation }
    }
    if ($Mode -eq "Update") {
        $utf8 = [Text.UTF8Encoding]::new($false)
        if ($doPbf) {
            $md5 = (Get-FileHash -LiteralPath $staged[0].source -Algorithm MD5).Hash.ToLowerInvariant()
            $sha = (Get-FileHash -LiteralPath $staged[0].source -Algorithm SHA256).Hash.ToLowerInvariant()
            $stamp = (Get-Date).ToUniversalTime().ToString("o")
            $snapshotText = "file=$name`nurl=$SourceUrl`ndownloaded=$stamp`nsha256=$sha`nmd5=$md5`nsize_bytes=$((Get-Item -LiteralPath $staged[0].source).Length)`nsnapshot_timestamp_status=OSMHeader-not-decoded`n"
            $snapshotStage = Join-Path $stageDir $snapshotName
            [IO.File]::WriteAllText($snapshotStage, $snapshotText, $utf8)
            $md5Stage = Join-Path $stageDir "$name.md5"
            [IO.File]::WriteAllText($md5Stage, "$md5  $name`n", $utf8)
            $staged += @{ source = $snapshotStage; target = (Join-Path $dataDir $snapshotName) }
            $staged += @{ source = $md5Stage; target = (Join-Path $dataDir "$name.md5") }
        } else {
            $manifestStage = Join-Path $stageDir "AUX_SNAPSHOT.txt"
            Copy-Item -LiteralPath ([IO.Path]::GetFullPath($AuxManifestPath)) -Destination $manifestStage
            $staged += @{ source = $manifestStage; target = (Join-Path (Join-Path $dataDir "external") "AUX_SNAPSHOT.txt") }
        }
    }
    foreach ($item in $staged) {
        $parent = Split-Path -Parent $item.target
        if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
            New-Item -ItemType Directory -Path $parent | Out-Null
        }
        if (Test-Path -LiteralPath $item.target) {
            if ($Mode -eq "Update") { throw "New-snapshot destination appeared during acquisition: $($item.target)" }
            if (Content-Matches $item.target $item.expectation) { continue }
            $backupPath = Join-Path $stageDir ("replaced-" + [Guid]::NewGuid().ToString("N"))
            [IO.File]::Replace($item.source, $item.target, $backupPath)
        } else {
            [IO.File]::Move($item.source, $item.target)
            if ($Mode -eq "Update") { $createdTargets += $item.target }
        }
        Write-Host "Verified $Mode completed: $($item.target)"
    }
} catch {
    if ($Mode -eq "Update") {
        foreach ($target in $createdTargets) {
            if (Test-Path -LiteralPath $target -PathType Leaf) { Remove-Item -LiteralPath $target -Force }
        }
    }
    throw
} finally {
    # Only this invocation's flat staging files are removed; never recurse over data.
    foreach ($file in (Get-ChildItem -LiteralPath $stageDir -File -Force)) {
        Remove-Item -LiteralPath $file.FullName -Force
    }
    Remove-Item -LiteralPath $stageDir -Force
}
