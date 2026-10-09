"""Execução OFICIAL pela própria plataforma (após o certificado real da empresa estar no cofre): conectividade,
consultas de Parametrização e sincronização ADN (restrita e produção, leitura), com acompanhamento do job até o
resultado final e conferência de documentos, eventos, cursor persistido e ausência de duplicatas.

Uso: .venv\\Scripts\\python -I scripts\\executar_operacoes_plataforma.py --empresa <id> [--sem-replay]

- Conta informada explicitamente (--email/--senha-arquivo), autorizada pelo titular; logout ao final.
- Para se o integration-status não estiver READY para distribuição (cadeia ICP-Brasil, LCR, usos, CNPJ).
- Nenhuma emissão: só GETs oficiais feitos pelo conector com o certificado do cofre.
- Registra separadamente: JOB CRIADO, CHAMADA OFICIAL EXECUTADA (log de auditoria do conector) e OPERAÇÃO
  CONCLUÍDA (estado final do job + conferência no banco). Imprime só contagens e códigos.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def sh(cmd: list[str], stdin: str | None = None) -> str:
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, cwd=ROOT, timeout=300).stdout


def sql(query: str) -> list[list[str]]:
    out = sh(["docker", "exec", "ctrlfiscal-mysql-1", "sh", "-c",
              f'MYSQL_PWD="$(cat /run/secrets/mysql_root_password)" mysql -uroot -N -e "{query}"'])
    return [line.split("\t") for line in out.strip().splitlines() if line.strip()]


def calls_since(since: str) -> Counter:
    logs = sh(["docker", "compose", "logs", "--no-color", "--since", since, "nfse-api"])
    c: Counter = Counter()
    for line in logs.splitlines():
        if "chamada_oficial" not in line:
            continue
        kv = dict(part.split("=", 1) for part in line.split("chamada_oficial", 1)[1].split() if "=" in part)
        c[(kv.get("ambiente"), kv.get("servico"), kv.get("resultado"))] += 1
    return c


def connector_param(company: str, cnpj: str, env: str, mun: str) -> str:
    """Consulta de Parametrização executada PELO CONECTOR (certificado do cofre), via endpoint interno."""
    code = (
        "import os,httpx;"
        "k=open(os.environ['INTERNAL_KEY_CORE_TO_NFSE_FILE']).read().strip();"
        f"r=httpx.get(os.environ['NFSE_API_URL']+'/internal/v1/companies/{company}/municipal-parameters/{mun}',"
        f"params={{'environment':'{env}','cnpj':'{cnpj}'}},headers={{'X-Service-Name':'core-api','Authorization':'Bearer '+k}},timeout=90);"
        "b=r.json();print(r.status_code, b.get('http_status'), b.get('code') or '', str((b.get('body') or {}).get('mensagem') or b.get('detail') or '')[:120])"
    )
    return sh(["docker", "compose", "exec", "-T", "core-api", "python", "-c", code]).strip()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--empresa", required=True)
    ap.add_argument("--base", default="http://127.0.0.1:8088")
    ap.add_argument("--email", required=True, help="Conta autorizada pelo titular.")
    ap.add_argument("--senha-arquivo", required=True, help="Arquivo local com a senha (nunca impresso).")
    ap.add_argument("--sem-replay", action="store_true")
    ap.add_argument("--timeout-min", type=int, default=30)
    a = ap.parse_args(argv)
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    c = httpx.Client(base_url=a.base, timeout=60)
    pw = Path(a.senha_arquivo).read_text(encoding="utf-8").strip()
    r = c.post("/api/core/v1/auth/login", json={"email": a.email, "password": pw}, headers={"Origin": a.base})
    r.raise_for_status()
    h = {"Authorization": "Bearer " + r.json()["access_token"]}
    core, fiscal = f"/api/core/v1/companies/{a.empresa}", f"/api/fiscal/v1/companies/{a.empresa}"
    report: dict = {"inicio": since, "empresa": a.empresa, "etapas": []}
    try:
        prof = c.get(f"{core}/fiscal-profile", headers=h).json()
        cnpj = prof["cnpj"]
        nota = json.loads((ROOT / "data/homologacao/nota-teste.json").read_text(encoding="utf-8"))
        muns = sorted({prof["municipality_code"], nota["nota"]["service"]["location_code"]})
        # 1. Conectividade (prontidão) — sem chamada oficial além da LCR (revogação).
        ready = {}
        for env in ("restricted", "production"):
            st = c.get(f"{core}/integration-status", headers=h, params={"environment": env}).json()
            ready[env] = st["capabilities"].get("distribution")
            line = f"[CONECTIVIDADE] {env}: status={st['status']} distribuição={st['capabilities'].get('distribution')} emissão={st['capabilities'].get('emission')} bloqueios={[b['code'] for b in st['blockers']]}"
            print(line)
            report["etapas"].append(line)
        if not any(ready.values()):
            print("PARADO: nenhum ambiente pronto para leitura (ver bloqueios acima).")
            return 3
        # 2. Consultas oficiais pela plataforma: Parametrização (convênio) no município do emitente e no de incidência.
        for env in [e for e in ("restricted", "production") if ready[e]]:
            for mun in muns:
                line = f"[CONSULTA] {env} parametrização {mun}/convenio via conector: {connector_param(a.empresa, cnpj, env, mun)}"
                print(line)
                report["etapas"].append(line)
        # 3. Sincronização ADN (job por ambiente, acompanhado até o estado final) + replay.
        rounds = ["inicial"] if a.sem_replay else ["inicial", "replay"]
        for rnd in rounds:
            for env in [e for e in ("restricted", "production") if ready[e]]:
                before = sql(f"SELECT COUNT(*) FROM ctrl_fiscal.dfe_items WHERE company_id='{a.empresa}' AND environment='{env}'")[0][0]
                jr = c.post(f"{fiscal}/sync/run", headers={**h, "Idempotency-Key": uuid.uuid4().hex}, params={"environment": env})
                if jr.status_code != 202:
                    print(f"[JOB] {rnd} {env}: recusado HTTP {jr.status_code} {jr.json().get('code')}")
                    continue
                job_id = jr.json()["job_id"]
                print(f"[JOB CRIADO] {rnd} {env}: job={job_id}")
                deadline = time.time() + a.timeout_min * 60
                job = {}
                while time.time() < deadline:
                    job = c.get(f"{fiscal}/jobs/{job_id}", headers=h).json()
                    if job.get("status") in ("SUCCEEDED", "FAILED", "PARTIAL", "CANCELLED"):
                        break
                    time.sleep(10)
                stt = c.get(f"{fiscal}/sync/status", headers=h, params={"environment": env}).json()
                q = f"company_id='{a.empresa}' AND environment='{env}'"
                items = sql(f"SELECT COUNT(*), COUNT(DISTINCT nsu), COALESCE(MAX(nsu),0) FROM ctrl_fiscal.dfe_items WHERE {q}")[0]
                kinds = sql(f"SELECT doc_kind, parse_status, COUNT(*) FROM ctrl_fiscal.dfe_items WHERE {q} GROUP BY doc_kind, parse_status")
                docs = sql(f"SELECT direction, status, COUNT(*) FROM ctrl_fiscal.documents WHERE {q} GROUP BY direction, status")
                dupdoc = sql(f"SELECT COUNT(*) FROM (SELECT access_key FROM ctrl_fiscal.documents WHERE {q} GROUP BY access_key HAVING COUNT(*)>1) x")[0][0]
                evs = sql(f"SELECT COUNT(*), COUNT(DISTINCT event_id) FROM ctrl_fiscal.document_events WHERE {q}")[0]
                cur = sql(f"SELECT cursor_nsu, status FROM ctrl_fiscal.sync_schedules WHERE {q}")[0]
                line = (f"[OPERAÇÃO {'CONCLUÍDA' if job.get('status') == 'SUCCEEDED' else 'NÃO CONCLUÍDA'}] {rnd} {env}: job={job.get('status')} "
                        f"agenda={stt['status']} cursor_persistido={cur[0]} itens={items[0]} (antes {before}) NSU_distintos={items[1]} "
                        f"NSU_max={items[2]} tipos/parse={kinds} documentos={docs} documentos_duplicados={dupdoc} "
                        f"eventos={evs[0]} (distintos {evs[1]}) erro={stt.get('last_error')}")
                print(line)
                report["etapas"].append(line)
        cc = calls_since(since)
        for (env, svc, res), n in sorted(cc.items()):
            line = f"[CHAMADA OFICIAL EXECUTADA] {env} {svc} {res}: {n}"
            print(line)
            report["etapas"].append(line)
    finally:
        csrf = c.get("/api/core/v1/auth/csrf").json().get("csrf_token")
        if csrf:
            c.post("/api/core/v1/auth/logout", headers={"Origin": a.base, "X-CSRF-Token": csrf})
    out = ROOT / f"docs/evidencias/operacoes-plataforma-{since.replace(':', '').replace('-', '')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"evidência: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
