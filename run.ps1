param(
  [ValidateSet("run", "dev", "build", "headless")]
  [string]$Mode = "run",

  [switch]$SkipInstall,
  [switch]$ForceSidecarBuild,
  [switch]$Profile,
  [switch]$ProfileCaptureContent
)

$ErrorActionPreference = "Stop"

$RootDir = $PSScriptRoot
$ApiDir = Join-Path $RootDir "api"
$UiDir = Join-Path $RootDir "ui"
$SrcTauriDir = Join-Path $UiDir "src-tauri"
$UvCacheDir = Join-Path $RootDir ".uv-cache"
$BuildDir = Join-Path $RootDir ".build"
$SidecarName = "automata-api"
$SandboxHostName = "automata-sandbox-host"
$SandboxCrateDir = Join-Path $RootDir "native\windows-sandbox"

function Write-Step {
  param([string]$Message)
  Write-Host ""
  Write-Host "==> $Message" -ForegroundColor Cyan
}

function Import-DotEnv {
  param([string]$Path)

  if (-not (Test-Path $Path)) {
    return
  }

  Write-Step "Loading environment from $Path"
  foreach ($Line in Get-Content -LiteralPath $Path) {
    $Trimmed = $Line.Trim()
    if (-not $Trimmed -or $Trimmed.StartsWith("#") -or -not $Trimmed.Contains("=")) {
      continue
    }

    $Parts = $Trimmed.Split("=", 2)
    $Name = $Parts[0].Trim()
    $Value = $Parts[1].Trim().Trim('"').Trim("'")
    $CurrentValue = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ($Name -and [string]::IsNullOrWhiteSpace($CurrentValue)) {
      [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
  }
}

function Import-LocalEnvironment {
  Import-DotEnv (Join-Path $RootDir ".env")
  Import-DotEnv (Join-Path $ApiDir ".env")
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

function Invoke-CheckedCommand {
  param(
    [string]$Name,
    [string[]]$Arguments
  )

  & $Name @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "'$Name $($Arguments -join ' ')' failed with exit code $LASTEXITCODE."
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
      Invoke-CheckedCommand "npm" @("install")
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
  $ApiProject = Join-Path $ApiDir "pyproject.toml"
  $ApiLock = Join-Path $ApiDir "uv.lock"
  $ApiPackageDir = Join-Path $ApiDir "automata_api"
  $SourceFiles = @($ApiEntry, $ApiProject)
  if (Test-Path $ApiLock) {
    $SourceFiles += $ApiLock
  }
  if (Test-Path $ApiPackageDir) {
    $SourceFiles += Get-ChildItem -Path $ApiPackageDir -Recurse -File -Filter "*.py" |
      Select-Object -ExpandProperty FullName
  }

  $ShouldBuild = $ForceSidecarBuild -or -not (Test-Path $SidecarPath)
  if (-not $ShouldBuild) {
    $SidecarTimestamp = (Get-Item $SidecarPath).LastWriteTimeUtc
    $ShouldBuild = @(
      $SourceFiles | Where-Object { (Get-Item $_).LastWriteTimeUtc -gt $SidecarTimestamp }
    ).Count -gt 0
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
  $MigrationSourceData = (
    (Join-Path $ApiDir "automata_api\db\migrations\*.py") +
    ";automata_api\db\migrations"
  )

  $Arguments = @(
    "run",
    "--directory", $ApiDir,
    "--extra", "build",
    "--locked",
    "pyinstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--name", $SidecarName,
    "--distpath", $DistDir,
    "--workpath", $WorkDir,
    "--specpath", $SpecDir,
    "--collect-data", "certifi",
    "--add-data", $MigrationSourceData,
    "--hidden-import", "uvicorn.lifespan.on",
    "--hidden-import", "uvicorn.protocols.http.h11_impl",
    "--hidden-import", "uvicorn.protocols.websockets.websockets_impl",
    "--hidden-import", "uvicorn.loops.asyncio",
    $ApiEntry
  )

  Invoke-CheckedCommand "uv" $Arguments

  if (-not (Test-Path $BuiltExe)) {
    throw "PyInstaller did not produce $BuiltExe."
  }

  Copy-Item -Force -LiteralPath $BuiltExe -Destination $SidecarPath
  Write-Host "Sidecar: $SidecarPath" -ForegroundColor DarkGray
  return $SidecarPath
}

function Build-SandboxHost {
  $TargetTriple = Get-RustHostTriple
  $BinariesDir = Join-Path $SrcTauriDir "binaries"
  $PackagedPath = Join-Path $BinariesDir "$SandboxHostName-$TargetTriple.exe"
  $ManifestPath = Join-Path $SandboxCrateDir "Cargo.toml"
  $LockPath = Join-Path $SandboxCrateDir "Cargo.lock"
  $SourceDir = Join-Path $SandboxCrateDir "src"
  $SourceFiles = @($ManifestPath)
  if (Test-Path $LockPath) {
    $SourceFiles += $LockPath
  }
  $SourceFiles += Get-ChildItem -Path $SourceDir -Recurse -File |
    Select-Object -ExpandProperty FullName

  $ShouldBuild = $ForceSidecarBuild -or -not (Test-Path $PackagedPath)
  if (-not $ShouldBuild) {
    $PackagedTimestamp = (Get-Item $PackagedPath).LastWriteTimeUtc
    $ShouldBuild = @(
      $SourceFiles | Where-Object { (Get-Item $_).LastWriteTimeUtc -gt $PackagedTimestamp }
    ).Count -gt 0
  }
  if (-not $ShouldBuild) {
    Write-Step "Sandbox host already built for $TargetTriple"
    return $PackagedPath
  }

  Write-Step "Building Windows AppContainer sandbox host for $TargetTriple"
  New-Item -ItemType Directory -Force -Path $BinariesDir | Out-Null
  Invoke-CheckedCommand "cargo" @(
    "build",
    "--locked",
    "--release",
    "--manifest-path", $ManifestPath
  )
  $BuiltHost = Join-Path $SandboxCrateDir "target\release\$SandboxHostName.exe"
  if (-not (Test-Path $BuiltHost)) {
    throw "Cargo did not produce $BuiltHost."
  }
  Copy-Item -Force -LiteralPath $BuiltHost -Destination $PackagedPath
  Write-Host "Sandbox host: $PackagedPath" -ForegroundColor DarkGray
  return $PackagedPath
}

function Sync-DevSidecar {
  $TargetTriple = Get-RustHostTriple
  $SourcePath = Join-Path $SrcTauriDir "binaries\$SidecarName-$TargetTriple.exe"
  $DebugDir = Join-Path $SrcTauriDir "target\debug"
  $DebugPath = Join-Path $DebugDir "$SidecarName.exe"

  if (-not (Test-Path $SourcePath)) {
    throw "Missing sidecar binary at $SourcePath."
  }

  New-Item -ItemType Directory -Force -Path $DebugDir | Out-Null

  $ShouldCopy = -not (Test-Path $DebugPath)
  if (-not $ShouldCopy) {
    $ShouldCopy = (Get-Item $SourcePath).LastWriteTimeUtc -gt (Get-Item $DebugPath).LastWriteTimeUtc
  }

  if ($ShouldCopy) {
    Write-Step "Syncing development sidecar"
    Copy-Item -Force -LiteralPath $SourcePath -Destination $DebugPath
  }

  $SandboxSource = Join-Path $SrcTauriDir "binaries\$SandboxHostName-$TargetTriple.exe"
  $SandboxDebugPath = Join-Path $DebugDir "$SandboxHostName.exe"
  if (-not (Test-Path $SandboxSource)) {
    throw "Missing sandbox host binary at $SandboxSource."
  }
  if (
    -not (Test-Path $SandboxDebugPath) -or
    (Get-Item $SandboxSource).LastWriteTimeUtc -gt (Get-Item $SandboxDebugPath).LastWriteTimeUtc
  ) {
    Write-Step "Syncing development sandbox host"
    Copy-Item -Force -LiteralPath $SandboxSource -Destination $SandboxDebugPath
  }
}

function Invoke-Tauri {
  param([string[]]$Arguments)

  Push-Location $UiDir
  try {
    $NpmArguments = @("run", "tauri", "--") + $Arguments
    Invoke-CheckedCommand "npm" $NpmArguments
  } finally {
    Pop-Location
  }
}

function Invoke-HeadlessApi {
  New-Item -ItemType Directory -Force -Path $UvCacheDir | Out-Null
  $env:UV_CACHE_DIR = $UvCacheDir

  if (-not $env:AUTOMATA_WORKSPACE_DIR) {
    $env:AUTOMATA_WORKSPACE_DIR = $RootDir
  }

  if (-not $env:AUTOMATA_API_TOKEN -or $env:AUTOMATA_API_TOKEN.Trim().Length -lt 32) {
    throw "Headless mode requires AUTOMATA_API_TOKEN with at least 32 characters."
  }

  $ApiHostValue = if ($env:AUTOMATA_API_HOST) { $env:AUTOMATA_API_HOST } else { "127.0.0.1" }
  $ApiPortValue = if ($env:AUTOMATA_API_PORT) { $env:AUTOMATA_API_PORT } else { "8765" }

  Write-Step "Running API in headless mode"
  Write-Host "API: http://${ApiHostValue}:${ApiPortValue}" -ForegroundColor DarkGray
  Write-Host "Health: http://${ApiHostValue}:${ApiPortValue}/health" -ForegroundColor DarkGray

  Invoke-CheckedCommand "uv" @(
    "run",
    "--directory", $ApiDir,
    "--locked",
    "python",
    "main.py"
  )
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

Require-Command "uv" "Install uv, or add it to PATH: https://docs.astral.sh/uv/"

Import-LocalEnvironment

if ($ProfileCaptureContent -and -not $Profile) {
  throw "-ProfileCaptureContent requires -Profile."
}

if ($Profile) {
  $env:AUTOMATA_OBSERVABILITY_MODE = "profile"
  $env:AUTOMATA_PROFILE_CAPTURE_CONTENT = if ($ProfileCaptureContent) {
    "true"
  } else {
    "false"
  }
  Write-Step "Profiling enabled"
  Write-Host (
    "Content capture: " +
    $(if ($ProfileCaptureContent) { "enabled" } else { "disabled" })
  ) -ForegroundColor DarkGray
}

if ($Mode -eq "headless") {
  Require-Command "cargo" "Install Rust/Cargo so the sandbox host can be built."
  Require-Command "rustc" "Install Rust/Cargo so the sandbox host can be built."
  Build-SandboxHost | Out-Null
  Invoke-HeadlessApi
  exit 0
}

Require-Command "node" "Install Node.js 22+ and reopen the terminal."
Require-Command "npm" "Install npm with Node.js and reopen the terminal."
Require-Command "cargo" "Install Rust/Cargo for Tauri desktop builds."
Require-Command "rustc" "Install Rust/Cargo for Tauri desktop builds."

Ensure-FrontendDependencies
Build-SandboxHost | Out-Null
Build-ApiSidecar | Out-Null

if ($Mode -eq "dev") {
  Sync-DevSidecar
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
