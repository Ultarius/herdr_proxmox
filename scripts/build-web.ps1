$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function Invoke-Checked {
    param([scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Build command failed with exit code $LASTEXITCODE" }
}
# Initialize separately: a fresh SDK may print first-run notices before JSON.
Invoke-Checked { flutter config --no-analytics }
$versionJson = & flutter --version --machine
if ($LASTEXITCODE -ne 0) { throw 'Unable to read the Flutter version' }
$version = ($versionJson | ConvertFrom-Json).frameworkVersion
if ($version -ne '3.44.8') { throw "Flutter 3.44.8 required, found $version" }
New-Item -ItemType Directory -Force web/public/dashboard | Out-Null
Push-Location web/dashboard
try {
    Invoke-Checked { flutter pub get }
    Invoke-Checked { flutter analyze }
    Invoke-Checked { flutter test }
    Invoke-Checked { flutter build web --release --base-href /dashboard/ --no-web-resources-cdn }
    Copy-Item build/web/* ../public/dashboard -Recurse -Force
} finally { Pop-Location }
Push-Location web/shell
try {
    Invoke-Checked { dart pub get }
    Invoke-Checked { dart analyze }
    Invoke-Checked { dart compile js lib/main.dart -O2 -o ../public/main.dart.js }
    Copy-Item web/index.html ../public/index.html -Force
} finally { Pop-Location }
