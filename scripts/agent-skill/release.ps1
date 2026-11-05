# Parse official stable release metadata; no network or program execution.
function Resolve-AgentRelease {
    param([Parameter(Mandatory=$true)]$Metadata, [string]$Version = 'latest')
    if ($Metadata.draft -ne $false -or $Metadata.prerelease -ne $false) {
        throw 'Only published stable releases are supported.'
    }
    $releaseTag = [string]$Metadata.tag_name
    if ($releaseTag -notmatch '^v?(\d+\.\d+\.\d+(?:\+[0-9A-Za-z.-]+)?)$') {
        throw 'The release tag must identify a stable semantic version.'
    }
    $releaseVersion = $Matches[1]
    if ($Version -ne 'latest' -and $Version.TrimStart('v') -ne $releaseVersion) {
        throw 'Release metadata does not match the requested version.'
    }
    $releaseBase = 'https://github.com/DreamSoul-AI/cpu-memory-cleanup/releases/'
    if ([string]$Metadata.url -notmatch '^https://api\.github\.com/repos/DreamSoul-AI/cpu-memory-cleanup/releases/\d+$' -or
        $Metadata.html_url -cne ($releaseBase + 'tag/' + $releaseTag)) {
        throw 'Release metadata is not from the official product repository.'
    }
    $assetPattern = '^cpu-memory-cleanup-v?' + [regex]::Escape($releaseVersion) + '-windows-x64\.zip$'
    $releaseAssets = @($Metadata.assets | Where-Object { $_.name -match $assetPattern })
    if ($releaseAssets.Count -ne 1) { throw 'Expected exactly one Windows x64 portable ZIP.' }
    $releaseAsset = $releaseAssets[0]
    if ($releaseAsset.state -ne 'uploaded' -or $releaseAsset.digest -notmatch '^sha256:([a-fA-F0-9]{64})$') {
        throw 'The official asset has no usable SHA256 digest.'
    }
    $releaseHash = $Matches[1].ToLowerInvariant()
    $releaseDownload = [string]$releaseAsset.browser_download_url
    if ([uri]::UnescapeDataString($releaseDownload) -cne
        ($releaseBase + 'download/' + $releaseTag + '/' + $releaseAsset.name)) {
        throw 'Asset URL is outside the selected official release.'
    }
    [pscustomobject]@{
        version = $releaseVersion; tag = $releaseTag; release = $Metadata.html_url
        asset_name = $releaseAsset.name; asset_id = $releaseAsset.id
        url = $releaseDownload; sha256 = $releaseHash
    }
}
