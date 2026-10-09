<#
.SYNOPSIS
  Leitura SOMENTE LEITURA da distribuição ADN Contribuintes (GET /contribuintes/DFe/{NSU}?lote=true) com o
  e-CNPJ do repositório do Windows. Usado para exploração inicial; a sincronização da plataforma mantém seu
  próprio cursor no banco.

.NOTES
  - Salva cada lote bruto em data\oficial\<ambiente>\adn\lote-<NSU>.json (fora do git).
  - Imprime apenas contagens/NSU/status — nunca XML, chaves ou dados fiscais.
  - Sequencial, com pausa entre chamadas; para em NENHUM_DOCUMENTO_LOCALIZADO, REJEICAO ou no limite de páginas.
#>
param(
  [Parameter(Mandatory = $true)][string]$Thumbprint,
  [ValidateSet('producao-restrita', 'producao')][string]$Ambiente = 'producao-restrita',
  [long]$NsuInicial = 0,
  [int]$MaxPaginas = 20
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Thumbprint -like "$Thumbprint*" } | Select-Object -First 1
$host_ = if ($Ambiente -eq 'producao') { 'https://adn.nfse.gov.br' } else { 'https://adn.producaorestrita.nfse.gov.br' }
$dir = Join-Path (Split-Path -Parent $PSScriptRoot) "data\oficial\$Ambiente\adn"
New-Item -ItemType Directory -Force $dir | Out-Null
$nsu = $NsuInicial
for ($i = 1; $i -le $MaxPaginas; $i++) {
  $url = "$host_/contribuintes/DFe/$($nsu)?lote=true"
  $req = [Net.HttpWebRequest]::Create($url); $req.Accept = 'application/json'; $req.Timeout = 90000
  [void]$req.ClientCertificates.Add($cert)
  try { $resp = $req.GetResponse() } catch [Net.WebException] { $resp = $_.Exception.Response; if (-not $resp) { "pagina ${i}: sem resposta ($($_.Exception.Message))"; break } }
  $ms = New-Object IO.MemoryStream; $resp.GetResponseStream().CopyTo($ms); $code = [int]$resp.StatusCode; $resp.Close()
  $file = Join-Path $dir ("lote-{0:D12}.json" -f $nsu)
  [IO.File]::WriteAllBytes($file, $ms.ToArray())
  $j = [Text.Encoding]::UTF8.GetString($ms.ToArray()) | ConvertFrom-Json
  $docs = @($j.LoteDFe | Where-Object { $_ })
  $max = if ($docs.Count) { ($docs | Measure-Object -Property NSU -Maximum).Maximum } else { $nsu }
  $erros = (@($j.Erros) | Where-Object { $_ } | ForEach-Object { "$($_.Codigo)" }) -join ','
  "pagina {0}: HTTP {1} status={2} docs={3} NSU {4}..{5} erros=[{6}]" -f $i, $code, $j.StatusProcessamento, $docs.Count, $nsu, $max, $erros
  if ($j.StatusProcessamento -ne 'DOCUMENTOS_LOCALIZADOS' -or $docs.Count -eq 0) { break }
  $nsu = [long]$max
  Start-Sleep -Milliseconds 800
}
"ultimo NSU lido: $nsu (arquivos em $dir)"
