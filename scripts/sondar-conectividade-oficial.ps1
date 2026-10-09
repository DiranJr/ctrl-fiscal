<#
.SYNOPSIS
  Sondagem SOMENTE LEITURA de conectividade com as APIs oficiais (padrão: Produção Restrita), usando o
  e-CNPJ do repositório do Windows. Registra método, URL, status, tamanho e campos de envelope — sem
  imprimir XML, chaves de acesso completas ou dados fiscais.

.NOTES
  Nenhuma chamada de escrita (POST) é feita. Produção só é sondada se -Ambiente producao for informado,
  e apenas com GET de distribuição/consulta.
#>
param(
  [Parameter(Mandatory = $true)][string]$Thumbprint,
  [ValidateSet('producao-restrita', 'producao')][string]$Ambiente = 'producao-restrita',
  [string]$MunicipioPublico = '3550308'  # código IBGE público usado só para sondar a API de parametrização
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Thumbprint -like "$Thumbprint*" } | Select-Object -First 1
$h = if ($Ambiente -eq 'producao') { @{ sefin = 'https://sefin.nfse.gov.br/SefinNacional'; adn = 'https://adn.nfse.gov.br' } } else { @{ sefin = 'https://sefin.producaorestrita.nfse.gov.br/SefinNacional'; adn = 'https://adn.producaorestrita.nfse.gov.br' } }

function Probe([string]$method, [string]$url) {
  $req = [Net.HttpWebRequest]::Create($url)
  $req.Method = $method; $req.Accept = 'application/json'; $req.Timeout = 60000; $req.AllowAutoRedirect = $false
  [void]$req.ClientCertificates.Add($cert)
  try { $resp = $req.GetResponse() } catch [Net.WebException] { $resp = $_.Exception.Response; if (-not $resp) { return [pscustomobject]@{ metodo = $method; url = $url; status = 'SEM RESPOSTA'; detalhe = $_.Exception.Message } } }
  $status = [int]$resp.StatusCode; $body = ''
  if ($method -ne 'HEAD') { $sr = New-Object IO.StreamReader($resp.GetResponseStream()); $body = $sr.ReadToEnd(); $sr.Close() }
  $resp.Close()
  $info = ''
  try {
    $j = $body | ConvertFrom-Json
    $keys = ($j.PSObject.Properties.Name) -join ','
    $codes = @(); foreach ($e in @($j.Erros) + @($j.erros) + @($j.erro) + @($j.Alertas)) { if ($e) { $codes += "$($e.Codigo)$($e.codigo):$((("$($e.Descricao)$($e.descricao)")).Substring(0, [Math]::Min(70, ("$($e.Descricao)$($e.descricao)").Length)))" } }
    $lote = if ($j.LoteDFe) { "LoteDFe=$(@($j.LoteDFe).Count)" } else { '' }
    $info = "campos=[$keys] status=$($j.StatusProcessamento) ambiente=$($j.TipoAmbiente)$($j.tipoAmbiente) $lote erros=[$($codes -join ' | ')]"
  } catch { $info = "corpo não-JSON ($($body.Length) bytes)" }
  [pscustomobject]@{ metodo = $method; url = $url; status = $status; bytes = $body.Length; detalhe = $info }
}

$out = @()
$out += Probe 'HEAD' "$($h.sefin)/dps/DPS000000000000000000000000000000000000000000"
$out += Probe 'GET' "$($h.sefin)/dps/INVALIDO"
$out += Probe 'GET' "$($h.adn)/parametrizacao/$MunicipioPublico/convenio"
$out += Probe 'GET' "$($h.adn)/contribuintes/DFe/0?lote=true"
$out += Probe 'GET' "$($h.adn)/DFe/0?lote=true"
$out | Format-List | Out-String -Width 250
