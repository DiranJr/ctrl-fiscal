<#
.SYNOPSIS
  Chamadas REAIS somente leitura (GET) às APIs oficiais com o e-CNPJ do repositório do Windows: conectividade
  Sefin (consulta de DPS inexistente da série da plataforma), Parametrização municipal e ADN Contribuintes.

.NOTES
  - Nenhum POST. Produção: só Parametrização e ADN (leitura), nunca Sefin.
  - Evidência em docs\evidencias\chamadas-oficiais-<data>.jsonl: método, caminho MASCARADO (CNPJ e Id DPS
    ocultos), status HTTP, bytes, chaves do envelope e códigos de erro. Corpos completos em
    data\oficial\<ambiente>\leitura\ (fora do git).
#>
param(
  [Parameter(Mandatory = $true)][string]$Thumbprint,
  [string]$Municipio = '1505536',
  [string]$Competencia = '2026-10-01T00:00:00',
  [string]$Serie = '1',
  [long]$NumeroDps = 1,
  [switch]$SomenteParametrizacao,
  # Parametrização é consultada no município de INCIDÊNCIA do ISSQN (padrão: local da prestação da nota de teste).
  [string]$MunicipioIncidencia = ''
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root = Split-Path -Parent $PSScriptRoot
$cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Thumbprint -like "$Thumbprint*" } | Select-Object -First 1
if (-not $cert) { throw 'certificado não encontrado no repositório do usuário' }
$perfil = Get-Content -Raw -Encoding UTF8 (Join-Path $root 'data\homologacao\empresa.json') | ConvertFrom-Json
$nota = Get-Content -Raw -Encoding UTF8 (Join-Path $root 'data\homologacao\nota-teste.json') | ConvertFrom-Json
$cnpj = $perfil.cnpj
$cTribNac = $nota.nota.service.national_code
if (-not $MunicipioIncidencia) { $MunicipioIncidencia = $nota.nota.service.location_code }
# Parametrização: "O código do serviço deve ser composto por nove dígitos" (resposta oficial 400). Composição usada:
# cTribNac (6) + cTribMun (3), ou "000" quando a nota não tem cTribMun.
# Formato observado nos códigos devolvidos pela própria API (retenções): 99.99.99.999 (cTribNac pontuado + 3).
$suf = $(if ($nota.nota.service.municipal_code) { $nota.nota.service.municipal_code } else { '000' })
$cServ9 = $cTribNac.Substring(0, 2) + '.' + $cTribNac.Substring(2, 2) + '.' + $cTribNac.Substring(4, 2) + '.' + $suf
$dpsId = 'DPS' + $Municipio + '2' + $cnpj + $Serie.PadLeft(5, '0') + ([string]$NumeroDps).PadLeft(15, '0')
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$evid = Join-Path $root "docs\evidencias\chamadas-oficiais-$stamp.jsonl"

function Mask([string]$s) {
  return $s.Replace($dpsId, 'DPS<mun>2<CNPJ><serie><nDPS>').Replace($cnpj, '<CNPJ>').Replace($cServ9, '<cServ9>').Replace($cTribNac, '<cTribNac>')
}

function Call([string]$amb, [string]$servico, [string]$url) {
  $req = [Net.HttpWebRequest]::Create($url)
  $req.Method = 'GET'; $req.Accept = 'application/json'; $req.Timeout = 60000; $req.AllowAutoRedirect = $false
  [void]$req.ClientCertificates.Add($cert)
  $t0 = Get-Date
  try { $resp = $req.GetResponse() } catch [Net.WebException] {
    $resp = $_.Exception.Response
    if (-not $resp) {
      $o = [ordered]@{ ambiente = $amb; servico = $servico; metodo = 'GET'; caminho = (Mask ([Uri]$url).PathAndQuery); status = 'SEM_RESPOSTA'; detalhe = $_.Exception.Message; quando = $t0.ToString('o') }
      ($o | ConvertTo-Json -Compress) | Add-Content -Encoding UTF8 $evid; return $o
    }
  }
  $ms = New-Object IO.MemoryStream; $resp.GetResponseStream().CopyTo($ms); $code = [int]$resp.StatusCode; $resp.Close()
  $bytes = $ms.ToArray(); $text = [Text.Encoding]::UTF8.GetString($bytes)
  $dir = Join-Path $root "data\oficial\$amb\leitura"; New-Item -ItemType Directory -Force $dir | Out-Null
  $nome = ($servico + '-' + (Mask ([Uri]$url).AbsolutePath) -replace '[^A-Za-z0-9_-]', '_') + "-$stamp.json"
  [IO.File]::WriteAllBytes((Join-Path $dir $nome), $bytes)
  $keys = @(); $erros = @(); $status = $null
  try {
    $j = $text | ConvertFrom-Json
    if ($j -is [System.Array]) { $keys = @("[array:$($j.Count)]") } elseif ($j) { $keys = @($j.PSObject.Properties.Name) }
    $status = $j.StatusProcessamento
    foreach ($e in @($j.erros) + @($j.Erros) + @($j.erro)) { if ($e) { $erros += ("" + $e.codigo + $e.Codigo) } }
  } catch { $keys = @('<nao-json>') }
  $o = [ordered]@{ ambiente = $amb; servico = $servico; metodo = 'GET'; caminho = (Mask ([Uri]$url).PathAndQuery)
    status = $code; bytes = $bytes.Length; ms = [int]((Get-Date) - $t0).TotalMilliseconds; chaves = $keys
    statusProcessamento = $status; erros = $erros; arquivo_local = "data\oficial\$amb\leitura\$nome"; quando = $t0.ToString('o') }
  ($o | ConvertTo-Json -Compress) | Add-Content -Encoding UTF8 $evid
  return $o
}

$hosts = @{
  'producao-restrita' = @{ sefin = 'https://sefin.producaorestrita.nfse.gov.br/SefinNacional'; adn = 'https://adn.producaorestrita.nfse.gov.br' }
  'producao'          = @{ adn = 'https://adn.nfse.gov.br' }
}
$out = @()
$r = $hosts['producao-restrita']
$p = $hosts['producao']
if (-not $SomenteParametrizacao) {
  # Produção Restrita: Sefin (consulta da primeira DPS da série da plataforma), convênio, retenções e ADN.
  $out += Call 'producao-restrita' 'sefin-dps' "$($r.sefin)/dps/$dpsId"
  $out += Call 'producao-restrita' 'param-convenio' "$($r.adn)/parametrizacao/$MunicipioIncidencia/convenio"
  $out += Call 'producao-restrita' 'param-retencoes' "$($r.adn)/parametrizacao/$MunicipioIncidencia/$Competencia/retencoes"
  $out += Call 'producao-restrita' 'adn-dfe' "$($r.adn)/contribuintes/DFe/0?lote=true&cnpjConsulta=$cnpj"
  # Produção: somente leitura (parametrização). Sem Sefin.
  $out += Call 'producao' 'param-convenio' "$($p.adn)/parametrizacao/$MunicipioIncidencia/convenio"
  $out += Call 'producao' 'param-retencoes' "$($p.adn)/parametrizacao/$MunicipioIncidencia/$Competencia/retencoes"
}
if ($SomenteParametrizacao) {
  foreach ($amb in @('producao-restrita', 'producao')) {
    $h_ = if ($amb -eq 'producao') { $p } else { $r }
    $out += Call $amb 'param-convenio' "$($h_.adn)/parametrizacao/$MunicipioIncidencia/convenio"
    $out += Call $amb 'param-retencoes' "$($h_.adn)/parametrizacao/$MunicipioIncidencia/$Competencia/retencoes"
  }
}
$out += Call 'producao-restrita' 'param-aliquota' "$($r.adn)/parametrizacao/$MunicipioIncidencia/$cServ9/$Competencia/aliquota"
$out += Call 'producao-restrita' 'param-regimes' "$($r.adn)/parametrizacao/$MunicipioIncidencia/$cServ9/$Competencia/regimes_especiais"
$out += Call 'producao' 'param-aliquota' "$($p.adn)/parametrizacao/$MunicipioIncidencia/$cServ9/$Competencia/aliquota"
$out += Call 'producao' 'param-regimes' "$($p.adn)/parametrizacao/$MunicipioIncidencia/$cServ9/$Competencia/regimes_especiais"
$out | ForEach-Object { '{0,-18} {1,-16} {2,-4} {3,-7}B erros=[{4}] {5} {6}' -f $_.ambiente, $_.servico, $_.status, $_.bytes, ($_.erros -join ','), $_.statusProcessamento, $_.caminho }
"evidência: $evid"
