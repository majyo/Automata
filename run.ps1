param(
  [ValidateSet("run", "dev", "build")]
  [string]$Mode = "run",

  [switch]$SkipInstall,
  [switch]$ForceSidecarBuild
)

$ErrorActionPreference = "Stop"

$RootDir = $PSScriptRoot
$ApiDir = Join-Path $RootDir "api"
$UiDir = Join-Path $RootDir "ui"
$SrcTauriDir = Join-Path $UiDir "src-tauri"
$UvCacheDir = Join-Path $RootDir ".uv-cache"
$BuildDir = Join-Path $RootDir ".build"
$SidecarName = "automata-api"

function Write-Step {
  param([string]$Message)
  Write-Host ""
  Write-Host "==> $Message" -ForegroundColor Cyan
}

function Require-Command {
  param(
    [string]$Name,
    [string]$InstallHint
  )

  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "Missing command '$Name'. $InstallHint"
  }
}

function Get-RustHostTriple {
  $RustVersion = rustc -vV
  $HostLine = $RustVersion | Where-Object { $_ -like "host: *" } | Select-Object -First 1
  if (-not $HostLine) {
    throw "Could not determine Rust host target triple from rustc -vV."
  }

  return $HostLine.Replace("host: ", "").Trim()
}

function Ensure-FrontendDependencies {
  if ($SkipInstall) {
    Write-Step "Skipping frontend dependency install"
    return
  }

  if (-not (Test-Path (Join-Path $UiDir "node_modules"))) {
    Write-Step "Installing frontend dependencies"
    Push-Location $UiDir
    try {
      npm install
    } finally {
      Pop-Location
    }
    return
  }

  Write-Step "Frontend dependencies already installed"
}

function Build-ApiSidecar {
  $TargetTriple = Get-RustHostTriple
  $BinariesDir = Join-Path $SrcTauriDir "binaries"
  $SidecarPath = Join-Path $BinariesDir "$SidecarName-$TargetTriple.exe"
  $ApiEntry = Join-Path $ApiDir "main.py"

  $ShouldBuild = $ForceSidecarBuild -or -not (Test-Path $SidecarPath)
  if (-not $ShouldBuild) {
    $ShouldBuild = (Get-Item $ApiEntry).LastWriteTimeUtc -gt (Get-Item $SidecarPath).LastWriteTimeUtc
  }

  if (-not $ShouldBuild) {
    Write-Step "Sidecar already built for $TargetTriple"
    return $SidecarPath
  }

  Write-Step "Building Python API sidecar for $TargetTriple"
  New-Item -ItemType Directory -Force -Path $BinariesDir | Out-Null
  New-Item -ItemType Directory -Force -Path $UvCacheDir | Out-Null
  New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

  $env:UV_CACHE_DIR = $UvCacheDir

  $PyinstallerRoot = Join-Path $BuildDir "pyinstaller"
  $DistDir = Join-Path $PyinstallerRoot "dist"
  $WorkDir = Join-Path $PyinstallerRoot "work"
  $SpecDir = Join-Path $PyinstallerRoot "spec"
  $BuiltExe = Join-Path $DistDir "$SidecarName.exe"

  $Arguments = @(
    "run",
    "--with", "fastapi==0.136.1",
    "--with", "uvicorn==0.46.0",
    "--with", "websockets==16.0",
    "--with", "pyinstaller",
    "pyinstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--name", $SidecarName,
    "--distpath", $DistDir,
    "--workpath", $WorkDir,
    "--specpath", $SpecDir,
    "--hidden-import", "uvicorn.lifespan.on",
    "--hidden-import", "uvicorn.protocols.http.h11_impl",
    "--hidden-import", "uvicorn.protocols.websockets.websockets_impl",
    "--hidden-import", "uvicorn.loops.asyncio",
    $ApiEntry
  )

  & uv @Arguments

  if (-not (Test-Path $BuiltExe)) {
    throw "PyInstaller did not produce $BuiltExe."
  }

  Copy-Item -Force -LiteralPath $BuiltExe -Destination $SidecarPath
  Write-Host "Sidecar: $SidecarPath" -ForegroundColor DarkGray
  return $SidecarPath
}

function Invoke-Tauri {
  param([string[]]$Arguments)

  Push-Location $UiDir
  try {
    $NpmArguments = @("run", "tauri", "--") + $Arguments
    & npm @NpmArguments
  } finally {
    Pop-Location
  }
}

function Get-ReleaseExecutable {
  $ReleaseDir = Join-Path $SrcTauriDir "target\release"
  $Candidates = @(
    (Join-Path $ReleaseDir "ui.exe"),
    (Join-Path $ReleaseDir "Automata Agent.exe")
  )

  foreach ($Candidate in $Candidates) {
    if (Test-Path $Candidate) {
      return $Candidate
    }
  }

  $Exe = Get-ChildItem -Path $ReleaseDir -Filter "*.exe" -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne "$SidecarName.exe" } |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1

  if ($Exe) {
    return $Exe.FullName
  }

  throw "Could not find a release executable in $ReleaseDir."
}

Require-Command "node" "Install Node.js 22+ and reopen the terminal."
Require-Command "npm" "Install npm with Node.js and reopen the terminal."
Require-Command "cargo" "Install Rust/Cargo for Tauri desktop builds."
Require-Command "rustc" "Install Rust/Cargo for Tauri desktop builds."
Require-Command "uv" "Install uv, or add it to PATH: https://docs.astral.sh/uv/"

Ensure-FrontendDependencies
Build-ApiSidecar | Out-Null

if ($Mode -eq "dev") {
  Write-Step "Running Tauri desktop app in dev mode"
  Invoke-Tauri @("dev")
  exit 0
}

Write-Step "Building production desktop app"
Invoke-Tauri @("build", "--no-bundle")

if ($Mode -eq "build") {
  exit 0
}

$ReleaseExe = Get-ReleaseExecutable
Write-Step "Launching packaged desktop app"
Write-Host $ReleaseExe -ForegroundColor DarkGray
Start-Process -FilePath $ReleaseExe -WorkingDirectory (Split-Path $ReleaseExe) -Wait
