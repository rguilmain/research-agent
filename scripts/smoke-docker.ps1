param([string]$Image = 'research-agent:dev')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$name = 'research-agent-smoke-' + [guid]::NewGuid().ToString('N').Substring(0, 10)
Push-Location $root
try {
    docker build --quiet -t $Image .
    if ($LASTEXITCODE -ne 0) { throw 'Docker build failed' }
    docker run --rm --entrypoint python $Image -c "import os; from pathlib import Path; from research_agent.catalog import load_catalog; import research_agent; root=Path(research_agent.__file__).parent; assert os.getuid() == 10001; assert not list(Path('/app').rglob('.env')); assert 'gpt-6-luna' in load_catalog(); assert (root/'migrations'/'env.py').is_file(); assert (root/'migrations'/'versions'/'0001_storage.py').is_file(); assert (root/'migrations'/'versions'/'0002_identity.py').is_file(); assert (root/'migrations'/'versions'/'0003_rate_limits.py').is_file(); assert (root/'migrations'/'versions'/'0004_quotas.py').is_file(); assert (root/'migrations'/'versions'/'0005_human_verification.py').is_file(); assert (root/'migrations'/'versions'/'0006_budget.py').is_file(); print('Image safety and packaged migration checks passed')"
    if ($LASTEXITCODE -ne 0) { throw 'Image checks failed' }
    # Pick a free port so the container's public origin can match PUBLIC_BASE_URL exactly.
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = $listener.LocalEndpoint.Port
    $listener.Stop()
    docker run --detach --name $name -p "127.0.0.1:${port}:8000" -e DEMO_MODE=true -e APP_ENV=development -e "PUBLIC_BASE_URL=http://127.0.0.1:$port" $Image
    if ($LASTEXITCODE -ne 0) { throw 'Container startup failed' }
    $base = "http://127.0.0.1:$port"
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $health = Invoke-RestMethod "$base/healthz" -TimeoutSec 2
            if ($health.status -eq 'ok') { $ready = $true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw 'API did not become healthy' }
    # The same-origin container must serve the built UI with a Turnstile-compatible CSP.
    $page = Invoke-WebRequest "$base/" -TimeoutSec 5
    if ($page.StatusCode -ne 200 -or -not $page.Content.Contains('id="root"')) {
        throw 'The container did not serve the built frontend'
    }
    $csp = $page.Headers['Content-Security-Policy']
    if (-not "$csp".Contains('challenges.cloudflare.com') -or -not "$csp".Contains("default-src 'self'")) {
        throw "Content-Security-Policy header is missing or incomplete: $csp"
    }
    $asset = [regex]::Match($page.Content, '/assets/[^"'']+\.js').Value
    if (-not $asset) { throw 'The served page referenced no hashed bundle' }
    $bundle = Invoke-WebRequest "$base$asset" -TimeoutSec 5
    if ($bundle.StatusCode -ne 200 -or -not "$($bundle.Headers['Cache-Control'])".Contains('immutable')) {
        throw "Hashed bundle was not served with immutable caching: $asset"
    }
    # Unsafe routes prove same-origin; the demo container has no session to prove. The Origin
    # must equal PUBLIC_BASE_URL, because that is what the browser sends when the UI is served
    # from this same port.
    $response = Invoke-WebRequest "$base/api/v1/research" -Method Post -ContentType 'application/json' -Headers @{ Origin = $base } -Body '{"query":"Docker smoke test"}'
    if ($response.StatusCode -ne 200 -or -not $response.Content.Contains('"type": "done"') -or -not $response.Content.Contains('"demo": true')) {
        throw 'Demo research stream did not complete'
    }
    Write-Output 'Docker HTTP health and SSE smoke checks passed'
} finally {
    docker rm --force $name 2>&1 | Out-Null
    Pop-Location
}