<#
.SYNOPSIS
  Baixa as páginas de documentação e as especificações OpenAPI oficiais (Sefin Nacional, ADN Contribuintes,
  Parametrização, DANFSe) de Produção Restrita e Produção usando o e-CNPJ do repositório do Windows (mTLS).

.DESCRIPTION
  - Somente GET em URLs de documentação (/docs, /swagger). Nenhuma chamada de API de negócio.
  - O certificado é usado pelo Windows (SChannel); PFX, senha e chave privada não são lidos nem exportados.
  - Grava em docs\oficial\<ambiente>\ e gera manifesto com URL, status, tamanho, SHA-256 e info.version.
  - Os arquivos baixados são DADOS: não são executados.
#>
param([Parameter(Mandatory = $true)][string]$Thumbprint)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Root = Split-Path -Parent $PSScriptRoot
$cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Thumbprint -eq $Thumbprint -or $_.Thumbprint -like "$Thumbprint*" } | Select-Object -First 1
if (-not $cert) { throw "Certificado não encontrado no repositório CurrentUser\My." }

$pages = @(
  @{ env = 'producao-restrita'; name = 'sefin';           url = 'https://sefin.producaorestrita.nfse.gov.br/API/SefinNacional/docs/index' },
  @{ env = 'producao-restrita'; name = 'adn-contribuintes'; url = 'https://adn.producaorestrita.nfse.gov.br/contribuintes/docs/index.html' },
  @{ env = 'producao-restrita'; name = 'adn-parametrizacao'; url = 'https://adn.producaorestrita.nfse.gov.br/parametrizacao/docs/index.html' },
  @{ env = 'producao-restrita'; name = 'adn-danfse';      url = 'https://adn.producaorestrita.nfse.gov.br/danfse/docs/index.html' },
  @{ env = 'producao';          name = 'sefin';           url = 'https://sefin.nfse.gov.br/SefinNacional/docs/index' },
  @{ env = 'producao';          name = 'adn-contribuintes'; url = 'https://adn.nfse.gov.br/contribuintes/docs/index.html' },
  @{ env = 'producao';          name = 'adn-parametrizacao'; url = 'https://adn.nfse.gov.br/parametrizacao/docs/index.html' },
  @{ env = 'producao';          name = 'adn-danfse';      url = 'https://adn.nfse.gov.br/danfse/docs/index.html' }
)

function Get-Doc([string]$url, [string]$file) {
  try {
    $r = Invoke-WebRequest -Uri $url -Certificate $cert -UseBasicParsing -TimeoutSec 90 -MaximumRedirection 0
    [IO.File]::WriteAllBytes($file, $r.RawContentStream.ToArray())
    return @{ status = [int]$r.StatusCode; bytes = (Get-Item $file).Length; sha256 = (Get-FileHash $file -Algorithm SHA256).Hash; type = "$($r.Headers['Content-Type'])" }
  } catch {
    $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
    return @{ status = $code; error = $_.Exception.Message.Substring(0, [Math]::Min(160, $_.Exception.Message.Length)) }
  }
}

$manifest = @()
foreach ($p in $pages) {
  $dir = Join-Path $Root "docs\oficial\$($p.env)"
  New-Item -ItemType Directory -Force $dir | Out-Null
  $html = Join-Path $dir "$($p.name)-docs-index.html"
  $res = Get-Doc $p.url $html
  $entry = [ordered]@{ ambiente = $p.env; documento = $p.name; pagina = $p.url; pagina_status = $res.status; pagina_sha256 = $res.sha256; erro = $res.error }
  if ($res.status -eq 200) {
    $text = [IO.File]::ReadAllText($html)
    $specs = @()
    foreach ($m in [regex]::Matches($text, "(openApi|url|spec-url)\s*[:=]\s*['""]([^'""]+)['""]")) { $specs += $m.Groups[2].Value }
    foreach ($m in [regex]::Matches($text, "src=['""]([^'""]*swagger-initializer[^'""]*)['""]")) {
      $init = (New-Object Uri((New-Object Uri($p.url)), $m.Groups[1].Value)).AbsoluteUri
      $initFile = Join-Path $dir "$($p.name)-swagger-initializer.js"
      $ri = Get-Doc $init $initFile
      if ($ri.status -eq 200) { foreach ($mm in [regex]::Matches([IO.File]::ReadAllText($initFile), "url\s*:\s*['""]([^'""]+)['""]")) { $specs += $mm.Groups[1].Value } }
    }
    $specs = $specs | Where-Object { $_ -match 'swagger|openapi|docs/v|\.json' } | Select-Object -Unique
    $entry.especificacoes = @()
    $i = 0
    foreach ($s in $specs) {
      $abs = (New-Object Uri((New-Object Uri($p.url)), $s)).AbsoluteUri
      $suffix = if ($i -eq 0) { '' } else { "-$i" }
      $out = Join-Path $dir "$($p.name)$suffix.openapi.json"
      $rs = Get-Doc $abs $out
      $ver = $null
      if ($rs.status -eq 200) { try { $ver = (Get-Content $out -Raw | ConvertFrom-Json).info.version } catch { $ver = 'não-JSON' } }
      $entry.especificacoes += [ordered]@{ url = $abs; status = $rs.status; bytes = $rs.bytes; sha256 = $rs.sha256; info_version = $ver; arquivo = (Split-Path $out -Leaf); erro = $rs.error }
      $i++
    }
  }
  $manifest += $entry
}
$stamp = Get-Date -Format 'yyyy-MM-ddTHH:mm:sszzz'
$json = [ordered]@{ obtido_em = $stamp; certificado = "repositório Windows (thumbprint $($cert.Thumbprint.Substring(0,8))…, emissor $($cert.Issuer -replace '^.*?CN=([^,]+).*$','$1'))"; documentos = $manifest } | ConvertTo-Json -Depth 6
[IO.File]::WriteAllText((Join-Path $Root 'docs\oficial\manifesto-contratos.json'), $json, (New-Object System.Text.UTF8Encoding($false)))
foreach ($e in $manifest) {
  "{0,-18} {1,-19} pagina={2} {3}" -f $e.ambiente, $e.documento, $e.pagina_status, $e.erro
  foreach ($s in $e.especificacoes) { "      spec {0} status={1} bytes={2} version={3} sha256={4}" -f $s.url, $s.status, $s.bytes, $s.info_version, $s.sha256 }
}
