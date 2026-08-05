# MiniMax H3 Local Deployment Script (run in PowerShell on the Windows host)
# Downloads 4 ComfyUI model files from hf-mirror + guides ComfyUI update.
# Usage: paste into PowerShell (or: powershell -ExecutionPolicy Bypass -File deploy_h3.ps1)

$ErrorActionPreference = "Continue"

$modelsRoot = "C:\Users\shrine\App\StabilityMatrix\Data\Packages\ComfyUI\models"
$base = "https://hf-mirror.com/Comfy-Org/MiniMax-H3/resolve/main"

$files = @(
  @("diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors", "diffusion_models"),
  @("text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "text_encoders"),
  @("vae/minimax_h3_video_vae_fp16.safetensors", "vae"),
  @("vae/minimax_h3_audio_vae_fp32.safetensors", "vae")
)

Write-Host "=== [1/3] Disk space check ==="
$drive = Get-PSDrive C
$freeGB = [math]::Round($drive.Free / 1GB, 1)
Write-Host ("C: free space: {0} GB (need ~43 GB)" -f $freeGB)
if ($freeGB -lt 45) {
  Write-Host "WARNING: Not enough free space. Free up space first." -ForegroundColor Red
  Read-Host "Press Enter to continue anyway"
}

Write-Host "`n=== [2/3] Downloading 4 model files ==="
foreach ($f in $files) {
  $rel = $f[0]
  $url = "$base/$rel"
  $dest = Join-Path $modelsRoot $rel
  $destDir = Split-Path $dest
  if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Force -Path $destDir | Out-Null }
  $destGB = if (Test-Path $dest) { [math]::Round((Get-Item $dest).Length / 1GB, 2) } else { 0 }
  Write-Host ("Downloading: {0} (existing {1} GB)" -f $rel, $destGB)
  curl.exe -L --retry 8 --retry-delay 5 -C - -o $dest $url
  if ($LASTEXITCODE -eq 0) {
    $size = [math]::Round((Get-Item $dest).Length / 1GB, 2)
    Write-Host ("  OK: {0} GB" -f $size) -ForegroundColor Green
  } else {
    Write-Host ("  FAILED (curl exit {0})" -f $LASTEXITCODE) -ForegroundColor Red
  }
}

Write-Host "`n=== [3/3] Model files summary ==="
Get-ChildItem -Recurse $modelsRoot -Include "minimax_h3*" -File | ForEach-Object {
  Write-Host ("  {0}  {1:N2} GB" -f $_.FullName.Replace($modelsRoot, "..."), ($_.Length / 1GB))
}

Write-Host "`n=== DONE ==="
Write-Host "Next step: update ComfyUI to 0.30.0+ via StabilityMatrix (or git pull in the ComfyUI package folder), then restart."
Read-Host "Press Enter to close"
