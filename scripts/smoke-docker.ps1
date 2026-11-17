param([string]$Image = 'research-agent:dev')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$name = 'research-agent-smoke-' + [guid]::NewGuid().ToString('N').Substring(0, 10)
Push-Location $root
try {
    docker build --quiet -t $Image .
    if ($LASTEXITCODE -ne 0) { throw 'Docker build failed' }
    docker run --rm --entrypoint python $Image -c "import os; from pathlib import Path; from research_agent.catalog import load_catalog; import research_agent; root=Path(research_agent.__file__).parent; assert os.getuid() == 10001; assert not list(Path('/app').rglob('.env')); assert 'gpt-6-luna' in load_catalog(); assert (root/'migrations'/'env.py').is_file(); assert (root/'migrations'/'versions'/'0001_storage.py').is_file(); assert (root/'migrations'/'versions'/'0002_identity.py').is_file(); assert (root/'migrations'/'versions'/'0003_rate_limits.py').is_file(); assert (root/'migrations'/'versions'/'0004_quotas.py').is_file(); assert (root/'migrations'/'versions'/'0005_human_verification.py').is_file(); print('Image safety and packaged migration checks passed')"
    if ($LASTEXITCODE -ne 0) { throw 'Image checks failed' }
    docker run --detach --name $name -p '127.0.0.1::8000' -e DEMO_MODE=true -e APP_ENV=development $Image
    if ($LASTEXITCODE -ne 0) { throw 'Container startup failed' }
    $binding = docker port $name 8000/tcp
    $base = 'http://' + $binding.Trim()
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $health = Invoke-RestMethod "$base/healthz" -TimeoutSec 2
            if ($health.status -eq 'ok') { $ready = $true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw 'API did not become healthy' }
    # Unsafe routes prove same-origin; the demo container has no session to prove.
    $response = Invoke-WebRequest "$base/api/v1/research" -Method Post -ContentType 'application/json' -Headers @{ Origin = 'http://127.0.0.1:5173' } -Body '{"query":"Docker smoke test"}'
    if ($response.StatusCode -ne 200 -or -not $response.Content.Contains('"type": "done"') -or -not $response.Content.Contains('"demo": true')) {
        throw 'Demo research stream did not complete'
    }
    Write-Output 'Docker HTTP health and SSE smoke checks passed'
} finally {
    docker rm --force $name 2>&1 | Out-Null
    Pop-Location
}