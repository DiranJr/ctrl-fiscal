<#
.SYNOPSIS
  Troca a senha de um usuário LOCAL existente do CTRL Fiscal sem exibir nem publicar a senha.

.DESCRIPTION
  - Gera uma senha aleatória (32 caracteres) em .\secrets\senha-local-<email>.txt ou usa -PasswordFile.
  - Aplica a troca pelo CLI do core-api (python -m core_api.set_password) dentro da imagem
    ctrlfiscal/core-api:dev, na rede interna do Compose. Somente o hash Argon2id é gravado.
  - Mantém usuário, empresas, permissões e dados; revoga todas as sessões desse usuário.
  - A senha nunca aparece na tela, em argumentos de linha de comando ou em logs.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\trocar-senha-local.ps1 -Email usuario@exemplo.com
  # Depois, leia a senha localmente no arquivo indicado (ex.: Notepad) e altere-a no seu gerenciador de senhas.
#>
param(
    [Parameter(Mandatory = $true)][string]$Email,
    [string]$PasswordFile = ""
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Utf8 = New-Object System.Text.UTF8Encoding($false)

$null = docker info --format '{{.ServerVersion}}' 2>$null
if ($LASTEXITCODE -ne 0) { throw "Docker não está em execução." }
$null = docker image inspect ctrlfiscal/core-api:dev 2>$null
if ($LASTEXITCODE -ne 0) { throw "Imagem ctrlfiscal/core-api:dev ausente. Rode: docker compose build core-api" }

if (-not $PasswordFile) {
    $safe = ($Email.ToLower() -replace '[^a-z0-9]', '-')
    $PasswordFile = Join-Path $Root "secrets\senha-local-$safe.txt"
    $alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_'.ToCharArray()
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes); $rng.Dispose()
    $value = -join ($bytes | ForEach-Object { $alphabet[$_ % $alphabet.Length] })
    [System.IO.File]::WriteAllText($PasswordFile, $value, $Utf8)
    Remove-Variable value
}
$PasswordFile = (Resolve-Path $PasswordFile).Path
try { icacls $PasswordFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null } catch { }

$dbPass = (Resolve-Path "secrets\core_db_password.txt").Path
docker run --rm --network ctrlfiscal_backend --read-only --cap-drop ALL --security-opt no-new-privileges:true `
    -e APP_ENV=development -e CORE_DB_HOST=mysql -e CORE_DB_NAME=ctrl_core -e CORE_DB_USER=core `
    -e CORE_DB_PASSWORD_FILE=/run/secrets/core_db_password `
    -v "${dbPass}:/run/secrets/core_db_password:ro" -v "${PasswordFile}:/run/secrets/new_password:ro" `
    ctrlfiscal/core-api:dev python -m core_api.set_password --email $Email --password-file /run/secrets/new_password
if ($LASTEXITCODE -ne 0) { throw "Falha ao trocar a senha (o arquivo de senha foi mantido para nova tentativa)." }
Write-Host "Senha do usuário $Email trocada. Valor salvo SOMENTE em: $PasswordFile (não é exibido)."
