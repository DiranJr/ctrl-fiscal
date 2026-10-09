<#
.SYNOPSIS
  Anexa o certificado A1 (PFX) da empresa pela API da plataforma (mesmo endpoint da tela do painel):
  POST /api/core/v1/companies/{id}/certificate (multipart). Use quando a tela ainda não estiver disponível.

.NOTES
  - A senha do PFX e a senha da conta (dono/admin da empresa) são digitadas aqui, ocultas; vão somente no corpo das
    requisições locais ao gateway; não são gravadas em disco nem impressas. A sessão aberta é encerrada no fim.
  - O conector valida CNPJ/validade/uso e cifra o PFX no cofre (AES-GCM, AAD empresa:ambiente).
  - Exige conta com permissão admin na empresa. Imprime apenas o resultado público (aceito, validade, ambientes).
#>
param(
  [Parameter(Mandatory = $true)][string]$EmpresaId,
  [Parameter(Mandatory = $true)][string]$ArquivoPfx,
  [Parameter(Mandatory = $true)][string]$Email,
  [string]$Ambientes = 'restricted,production',
  [string]$Base = 'http://127.0.0.1:8088'
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http
$contaSegura = Read-Host -AsSecureString "Senha da conta $Email (não será exibida)"
$pfxSeguro = Read-Host -AsSecureString 'Senha do PFX (não será exibida)'
$bstrConta = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($contaSegura)
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pfxSeguro)
try {
  $senhaConta = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstrConta)
  $senhaPfx = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  $http = New-Object System.Net.Http.HttpClient
  $login = New-Object System.Net.Http.HttpRequestMessage 'POST', "$Base/api/core/v1/auth/login"
  $login.Headers.Add('Origin', $Base)
  $login.Content = New-Object System.Net.Http.StringContent ((@{ email = $Email; password = $senhaConta } | ConvertTo-Json), [Text.Encoding]::UTF8, 'application/json')
  $r = $http.SendAsync($login).Result
  if (-not $r.IsSuccessStatusCode) { throw "login falhou: HTTP $([int]$r.StatusCode)" }
  $token = ($r.Content.ReadAsStringAsync().Result | ConvertFrom-Json).access_token
  $senhaConta = $null
  $form = New-Object System.Net.Http.MultipartFormDataContent
  $bytes = [IO.File]::ReadAllBytes((Resolve-Path $ArquivoPfx))
  $file = New-Object System.Net.Http.ByteArrayContent (, $bytes)
  $file.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/x-pkcs12')
  $form.Add($file, 'file', 'certificado.pfx')
  $form.Add((New-Object System.Net.Http.StringContent $senhaPfx), 'password')
  $form.Add((New-Object System.Net.Http.StringContent $Ambientes), 'environments')
  $req = New-Object System.Net.Http.HttpRequestMessage 'POST', "$Base/api/core/v1/companies/$EmpresaId/certificate"
  $req.Headers.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue 'Bearer', $token
  $req.Content = $form
  $resp = $http.SendAsync($req).Result
  $body = $resp.Content.ReadAsStringAsync().Result | ConvertFrom-Json
  if ($resp.IsSuccessStatusCode) {
    "certificado aceito={0} cnpj_confere={1} valido_ate={2} dias={3} ambientes={4}" -f $body.accepted, $body.subject_cnpj_matches, $body.not_after, $body.days_to_expiry, ($body.environments -join ',')
    if ($body.problems) { $body.problems | ForEach-Object { "  aviso: $($_.code)" } }
  } else {
    "recusado: HTTP {0} {1} - {2}" -f [int]$resp.StatusCode, $body.code, $body.detail
  }
  # Encerra a sessão aberta por este script (cookie de refresh no HttpClient).
  $csrf = ($http.GetAsync("$Base/api/core/v1/auth/csrf").Result.Content.ReadAsStringAsync().Result | ConvertFrom-Json).csrf_token
  $out = New-Object System.Net.Http.HttpRequestMessage 'POST', "$Base/api/core/v1/auth/logout"
  $out.Headers.Add('Origin', $Base); $out.Headers.Add('X-CSRF-Token', $csrf)
  "logout: HTTP {0}" -f [int]$http.SendAsync($out).Result.StatusCode
} finally {
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstrConta)
  Remove-Variable senhaPfx, senhaConta, bytes -ErrorAction SilentlyContinue
}
