<#
.SYNOPSIS
  Prepara o ambiente LOCAL do CTRL Fiscal no Windows: confere Docker/Compose e a
  porta HTTP (padrão 8088), cria .env a partir de .env.example e gera segredos
  aleatórios em .\secrets (nunca sobrescreve os existentes, nunca imprime valores).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\gerar-segredos.ps1
  powershell -ExecutionPolicy Bypass -File scripts\gerar-segredos.ps1 -HttpPort 8090
#>
param(
    [int]$HttpPort = 0,
    [switch]$SkipPortCheck
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Utf8 = New-Object System.Text.UTF8Encoding($false)

function Write-Secret([string]$Name, [string]$Value) {
    $path = Join-Path $Root "secrets\$Name"
    if (Test-Path $path) { Write-Host "  mantido: secrets\$Name"; return }
    [System.IO.File]::WriteAllText($path, $Value, $Utf8)
    Write-Host "  criado:  secrets\$Name"
}

function New-RandomHex([int]$Bytes = 32) {
    $b = New-Object byte[] $Bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($b); $rng.Dispose()
    return -join ($b | ForEach-Object { $_.ToString('x2') })
}

function New-RandomB64([int]$Bytes = 32) {
    $b = New-Object byte[] $Bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($b); $rng.Dispose()
    return [Convert]::ToBase64String($b)
}

# ---------------------------------------------------------------- Docker
Write-Host "Verificando Docker..."
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker CLI não encontrado. Instale o Docker Desktop." }
$null = docker info --format '{{.ServerVersion}}' 2>$null
if ($LASTEXITCODE -ne 0) { throw "Docker não está em execução. Inicie o Docker Desktop e repita." }
$null = docker compose version 2>$null
if ($LASTEXITCODE -ne 0) { throw "Plugin 'docker compose' (v2) não encontrado." }
Write-Host "  Docker OK"

# ---------------------------------------------------------------- .env
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Criado .env a partir de .env.example (sem segredos)."
}
if ($HttpPort -eq 0) {
    $line = Select-String -Path .env -Pattern '^HTTP_PORT=(\d+)' | Select-Object -First 1
    $HttpPort = if ($line) { [int]$line.Matches[0].Groups[1].Value } else { 8088 }
} else {
    $content = Get-Content .env | ForEach-Object { if ($_ -match '^HTTP_PORT=') { "HTTP_PORT=$HttpPort" } else { $_ } }
    [System.IO.File]::WriteAllLines((Join-Path $Root '.env'), $content, $Utf8)
}

# ---------------------------------------------------------------- porta
if (-not $SkipPortCheck) {
    $listener = $null
    try {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $HttpPort)
        $listener.Start()
        Write-Host "Porta 127.0.0.1:$HttpPort livre."
    } catch {
        $owner = docker ps --filter "publish=$HttpPort" --format '{{.Names}}' 2>$null
        if ($owner -match '^ctrlfiscal-gateway') { Write-Host "Porta $HttpPort já usada pelo gateway do CTRL Fiscal (ok)." }
        else { throw "Porta $HttpPort ocupada. Use -HttpPort <outra> (ex.: 8090)." }
    } finally { if ($listener) { $listener.Stop() } }
}

# ---------------------------------------------------------------- segredos
$SecretsDir = Join-Path $Root 'secrets'
if (-not (Test-Path $SecretsDir)) { New-Item -ItemType Directory $SecretsDir | Out-Null }
try {
    # Restringe a pasta ao usuário atual (Docker Desktop roda com o mesmo usuário).
    icacls $SecretsDir /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" | Out-Null
} catch { Write-Warning "Não foi possível restringir permissões de secrets\: $_" }

Write-Host "Gerando segredos (valores não são exibidos)..."
foreach ($n in 'mysql_root_password', 'core_db_password', 'core_migrate_db_password', 'fiscal_db_password',
               'fiscal_migrate_db_password', 'nfse_db_password', 'nfse_migrate_db_password', 'csrf_secret',
               'internal_fiscal_api_to_core', 'internal_fiscal_worker_to_core', 'internal_fiscal_worker_to_nfse',
               'internal_core_to_nfse') {
    Write-Secret "$n.txt" (New-RandomHex 32)
}
Write-Secret 'nfse_vault_key.txt' (New-RandomB64 32)

# Par Ed25519 do JWT: gerado pela própria imagem do core-api (cryptography), sem dependência no host.
$priv = Join-Path $SecretsDir 'jwt_private_key.pem'
$pub = Join-Path $SecretsDir 'jwt_public_key.pem'
if (-not (Test-Path $priv)) {
    Write-Host "Construindo imagem do core-api para gerar o par de chaves JWT..."
    docker build -q -f services/core-api/Dockerfile -t ctrlfiscal/core-api:dev services | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Falha ao construir core-api." }
    $pem = (docker run --rm --network none ctrlfiscal/core-api:dev python -m core_api.keygen private) -join "`n"
    if ($LASTEXITCODE -ne 0 -or -not $pem.Contains('PRIVATE KEY')) { throw "Falha ao gerar chave privada." }
    [System.IO.File]::WriteAllText($priv, $pem + "`n", $Utf8)
    Write-Host "  criado:  secrets\jwt_private_key.pem"
    if (Test-Path $pub) { Remove-Item $pub }
}
if (-not (Test-Path $pub)) {
    $pubPem = (Get-Content $priv -Raw | docker run --rm -i --network none ctrlfiscal/core-api:dev python -m core_api.keygen public-stdin) -join "`n"
    if ($LASTEXITCODE -ne 0 -or -not $pubPem.Contains('PUBLIC KEY')) { throw "Falha ao derivar chave pública." }
    [System.IO.File]::WriteAllText($pub, $pubPem + "`n", $Utf8)
    Write-Host "  criado:  secrets\jwt_public_key.pem"
}

Write-Host ""
Write-Host "Pronto. Próximos passos:"
Write-Host "  docker compose up -d --build"
Write-Host "  Acesse http://127.0.0.1:$HttpPort"
