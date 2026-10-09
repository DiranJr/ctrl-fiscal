"""Validação ponta a ponta no AMBIENTE DESCARTÁVEL (pilha real: gateway, core, fiscal, worker, nfse-api mock, MySQL).

Testes positivos e negativos das regras de segurança e do fluxo de cancelamento, com as contas FICTÍCIAS de
scripts/ambiente_descartavel.py (senhas lidas de secrets-descartavel/, nunca exibidas). Nada aqui toca o
ambiente operacional. Resultado em docs/evidencias/validacao-descartavel-<data>.json (sem segredos/tokens).

Uso: .venv\\Scripts\\python -I scripts\\validar_descartavel.py
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SEC = ROOT / "secrets-descartavel"
FIX = json.loads((SEC / "fixtures.json").read_text(encoding="utf-8"))
BASE = FIX["base"]
A, B = FIX["companies"]["A"], FIX["companies"]["B"]
CORE, FISCAL = "/api/core/v1", "/api/fiscal/v1"
PROJECT = "ctrlfiscal-desc"
RESULTS: list[dict] = []


PROBE_AUDIT = """
from sqlalchemy import text
from ctrl_common.db import make_engine
from ctrl_common.config import database_url
e = make_engine(database_url('__DB__'))
with e.connect() as c:
    n = c.execute(text('select count(*) from audit_events')).scalar()
try:
    with e.begin() as c:
        c.execute(text("UPDATE audit_events SET action=CONCAT(action,'') LIMIT 1"))
    print('ALTEROU', n)
except Exception:
    print('RECUSADO', n)
try:
    with e.begin() as c:
        c.execute(text('DELETE FROM audit_events LIMIT 1'))
    print('APAGOU')
except Exception:
    print('RECUSADO-DEL')
"""


def login_post(client: httpx.Client, body: dict, headers: dict | None = None) -> httpx.Response:
    """O gateway limita o login (10/min por IP, rajada 5): espera e repete em 429 — a própria proteção em ação."""
    for _ in range(20):
        r = client.post(f"{CORE}/auth/login", json=body, headers=headers if headers is not None else {"Origin": BASE})
        if r.status_code != 429:
            return r
        time.sleep(7)
    return r


def pw(email: str) -> str:
    if email == FIX["admin"]:
        return (SEC / "admin_password.txt").read_text(encoding="utf-8").strip()
    return (SEC / f"senha-{email.split('@')[0]}.txt").read_text(encoding="utf-8").strip()


def check(rid: str, requisito: str, teste: str, ok: bool, obtido: str) -> bool:
    RESULTS.append({"id": rid, "requisito": requisito, "teste": teste, "resultado": "PASS" if ok else "FAIL",
                    "obtido": obtido[:300]})
    print(("PASS " if ok else "FAIL ") + rid + " — " + teste + ("" if ok else f" | {obtido[:200]}"))
    return ok


class Session:
    def __init__(self, email: str, password: str | None = None) -> None:
        self.c = httpx.Client(base_url=BASE, timeout=60, headers={"Origin": BASE})
        r = login_post(self.c, {"email": email, "password": password or pw(email)})
        r.raise_for_status()
        self.token = r.json()["access_token"]
        self.login_response = r

    @property
    def h(self) -> dict:
        return {"Authorization": "Bearer " + self.token}

    def get(self, path: str, **kw):  # noqa: ANN003, ANN201
        return self.c.get(path, headers={**self.h, **kw.pop("headers", {})}, **kw)

    def post(self, path: str, **kw):  # noqa: ANN003, ANN201
        return self.c.post(path, headers={**self.h, **kw.pop("headers", {})}, **kw)

    def put(self, path: str, **kw):  # noqa: ANN003, ANN201
        return self.c.put(path, headers={**self.h, **kw.pop("headers", {})}, **kw)

    def patch(self, path: str, **kw):  # noqa: ANN003, ANN201
        return self.c.patch(path, headers={**self.h, **kw.pop("headers", {})}, **kw)


def novo_cnpj() -> str:
    """CNPJ FICTÍCIO com DV válido (base aleatória com prefixo 99), só para o ambiente descartável."""
    import random

    base = "99" + "".join(random.choice("0123456789") for _ in range(6)) + "0001"

    def dv(chars: str, w: list[int]) -> int:
        r = sum(int(c) * k for c, k in zip(chars, w)) % 11
        return 0 if r < 2 else 11 - r

    d1 = dv(base, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = dv(base + str(d1), [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return f"{base}{d1}{d2}"


def code(r) -> str:  # noqa: ANN001
    try:
        return f"{r.status_code} {r.json().get('code', '')}"
    except Exception:  # noqa: BLE001
        return str(r.status_code)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # textos com símbolos (console cp1252)
    anon = httpx.Client(base_url=BASE, timeout=30)
    # ------------------------------------------------------------ 2.6 HTTP / cabeçalhos / rotas internas
    for path, area in (("/fiscal/", "painel"), ("/login", "portal"), (f"{FISCAL}/companies/{A}/documents", "API")):
        r = anon.get(path)
        csp = r.headers.get("content-security-policy", "")
        check(f"HTTP-CSP-{area}", "2.6 cabeçalhos", f"CSP e anti-enquadramento em {area}",
              "frame-ancestors 'none'" in csp and r.headers.get("x-frame-options") == "DENY"
              and r.headers.get("x-content-type-options") == "nosniff", f"{r.status_code} csp={csp[:80]}")
    r = anon.get(f"{CORE}/internal/v1/companies")
    check("HTTP-INTERNAL", "2.6 rotas internas", "/internal não roteado pelo gateway", r.status_code == 404, code(r))
    ports = subprocess.run(["docker", "ps", "--filter", f"label=com.docker.compose.project={PROJECT}", "--format",
                            "{{.Names}} {{.Ports}}"], capture_output=True, text=True).stdout
    published = [ln for ln in ports.splitlines() if "->" in ln]
    check("HTTP-PORTS", "2.6 portas", "só o gateway publica porta (loopback)",
          len(published) == 1 and "gateway" in published[0] and "127.0.0.1:" in published[0], " | ".join(published))
    r = anon.post(f"{CORE}/auth/login", content=b"{" + b" " * (2 * 1024 * 1024) + b"}",
                  headers={"Origin": BASE, "Content-Type": "application/json"})
    check("HTTP-BODY", "2.6 limite de corpo", "corpo de 2 MB no login recusado", r.status_code == 413, code(r))
    for logo in ("/fiscal/logo-ctrl-fiscal.svg", "/logo-ctrl-fiscal.svg", "/fiscal/marca-ctrl-fiscal.svg"):
        r = anon.get(logo)
        check(f"UI-LOGO{logo}", "logomarca", f"logomarca servida em {logo}",
              r.status_code == 200 and "svg" in r.headers.get("content-type", "") and b"CTRL Fiscal" in r.content,
              f"{r.status_code} {r.headers.get('content-type')}")

    # ------------------------------------------------------------ 2.1 autenticação
    r = anon.get(f"{FISCAL}/companies/{A}/documents")
    check("AUTH-401", "2.1 autenticação", "API fiscal sem token => 401", r.status_code == 401, code(r))
    r1 = login_post(anon, {"email": "leitor.a@descartavel.example.com", "password": "errada-123456"})
    r2 = login_post(anon, {"email": "ninguem@descartavel.example.com", "password": "errada-123456"})
    check("AUTH-ENUM", "2.1 enumeração", "senha errada e e-mail inexistente: mesma resposta",
          r1.status_code == r2.status_code == 401 and r1.json().get("detail") == r2.json().get("detail"),
          f"{code(r1)} / {code(r2)}")
    r = login_post(anon, {"email": "leitor.a@descartavel.example.com", "password": "x" * 12}, headers={})
    check("AUTH-ORIGIN", "2.6 CSRF/origem", "login sem Origin recusado", r.status_code == 403, code(r))
    leitor = Session("leitor.a@descartavel.example.com")
    cookie = leitor.login_response.headers.get("set-cookie", "")
    check("AUTH-COOKIE", "2.1 cookie", "refresh em cookie HttpOnly, SameSite=Strict, caminho restrito",
          "httponly" in cookie.lower() and "samesite=strict" in cookie.lower() and "path=/api/core/v1/auth" in cookie.lower(),
          cookie.split(";", 1)[1][:120] if ";" in cookie else cookie[:20])
    check("AUTH-COOKIE-SECURE", "2.1 cookie (HTTPS)", "Secure só com HTTPS (descartável em HTTP: desvio declarado)",
          "secure" not in cookie.lower(), "sem Secure em HTTP local")
    r = leitor.c.post(f"{CORE}/auth/refresh")
    check("AUTH-CSRF", "2.6 CSRF", "refresh sem X-CSRF-Token recusado", r.status_code == 403, code(r))
    tmp = Session("leitor.a@descartavel.example.com")
    csrf = tmp.c.get(f"{CORE}/auth/csrf").json().get("csrf_token")
    r = tmp.c.post(f"{CORE}/auth/logout", headers={"X-CSRF-Token": csrf or ""})
    r2 = tmp.get(f"{FISCAL}/companies/{A}/documents")
    check("AUTH-LOGOUT", "2.1 logout", "logout invalida a sessão no servidor (token restante recusado)",
          r.status_code in (200, 204) and r2.status_code == 401, f"logout {code(r)} depois {code(r2)}")

    # ------------------------------------------------------------ 2.2 permissões / 2.3 isolamento
    admin = Session(FIX["admin"])
    op_a = Session("operador.a@descartavel.example.com")
    op_b = Session("operador.b@descartavel.example.com")
    adm_a = Session("admin.a@descartavel.example.com")
    users = {u["email"]: u for u in admin.get(f"{CORE}/admin/users", params={"page_size": 100}).json()["items"]}
    r = leitor.post(f"{FISCAL}/companies/{A}/sync/run", headers={"Idempotency-Key": uuid.uuid4().hex})
    check("PERM-SYNC", "2.2 permissões", "leitor não sincroniza (sync)", r.status_code == 403, code(r))
    r = adm_a.put(f"{CORE}/admin/users/{users['leitor.a@descartavel.example.com']['id']}/memberships/{A}",
                  json={"permissions": ["view", "cancel"]})
    check("PERM-CEILING", "2.2 teto", "admin de empresa não concede 'cancel' que não possui",
          r.status_code == 403 and r.json().get("code") == "PERMISSION_ABOVE_OWN", code(r))
    r = adm_a.post(f"{CORE}/admin/users/{users['operador.a@descartavel.example.com']['id']}/credential-link")
    check("PERM-RESET", "2.2 escalada", "admin de empresa não gera redefinição de senha de conta ativa",
          r.status_code == 403, code(r))
    r = adm_a.put(f"{CORE}/admin/users/{users['operador.a@descartavel.example.com']['id']}/super-admin", json={"enabled": True})
    check("PERM-SUPER", "2.2 escalada", "admin de empresa não concede SUPER_ADMIN", r.status_code == 403, code(r))
    r = op_b.get(f"{FISCAL}/companies/{A}/documents")
    check("ISO-COMPANY", "2.3 isolamento", "operador de B não lê empresa A", r.status_code == 403, code(r))
    docs_b = op_b.get(f"{FISCAL}/companies/{B}/documents", params={"environment": "mock"}).json()["items"]
    if docs_b:
        r = op_a.get(f"{FISCAL}/companies/{A}/documents/{docs_b[0]['id']}")
        check("ISO-ID", "2.3 isolamento", "id de documento de B pela rota de A => 404", r.status_code == 404, code(r))
    r = op_a.post(f"{FISCAL}/companies/{A}/drafts", json={"customer_id": str(uuid.uuid4()), "competence": "2026-10",
                                                        "amount": "1.00", "company_id": B,
                                                        "service": {"national_code": "010101", "description": "x"}})
    check("INPUT-EXTRA", "2.5 campos internos", "propriedade extra (company_id) recusada", r.status_code == 422, code(r))

    # ------------------------------------------------------------ workspaces (um por empresa/CNPJ)
    multi = Session("multi.ab@descartavel.example.com")
    me = multi.get(f"{CORE}/auth/me").json()
    ws = {w["company"]["id"]: w for w in me.get("workspaces", [])}
    check("WS-ME", "1/4 workspaces", "mesmo usuário em 2 workspaces com permissões diferentes",
          {A, B} <= set(ws) and "admin" in (ws[A]["permissions"] or []) and ws[B]["permissions"] == ["view"],
          json.dumps({k[:8]: v["permissions"] for k, v in ws.items()}))
    prof = multi.post(f"{CORE}/companies/{A}/service-profiles", json={"name": "Perfil teste A", "national_code": "010101"})
    r = multi.post(f"{CORE}/companies/{B}/service-profiles", json={"name": "Perfil teste B", "national_code": "010101"})
    check("WS-PERM", "4 troca não transfere privilégio", "admin em A cria perfil; em B (só leitura) => 403",
          prof.status_code == 201 and r.status_code == 403, f"{code(prof)}/{code(r)}")
    if prof.status_code == 201:
        r = op_b.patch(f"{CORE}/companies/{B}/service-profiles/{prof.json()['id']}",
                       json={"name": "Invasao", "expected_version": 1})
        check("WS-CROSS-ID", "3 isolamento", "perfil de A pela rota de B => 403/404", r.status_code in (403, 404), code(r))
    r = op_b.get(f"{CORE}/companies/{A}/readiness")
    check("WS-CROSS-READ", "3 isolamento", "prontidão de A por usuário só de B => 403", r.status_code in (403, 404), code(r))
    a_cnpj = admin.get(f"{CORE}/admin/workspaces").json()
    dup = next((w["company"]["cnpj"] for w in a_cnpj if w["id"] == A), None)
    r = admin.post(f"{CORE}/admin/workspaces", json={"name": "Duplicado", "company": {
        "cnpj": dup, "name": "Duplicado", "municipality_code": "3550308", "timezone": "America/Sao_Paulo"}})
    check("WS-UNIQUE-CNPJ", "1 um workspace por CNPJ", "segundo workspace com o CNPJ de A => 409",
          r.status_code == 409 and r.json().get("code") == "CNPJ_ALREADY_IN_WORKSPACE", code(r))
    rd = multi.get(f"{CORE}/companies/{A}/readiness").json()
    ops = {(e["environment"], o["operation"]) for e in rd.get("environments", []) for o in e["operations"]}
    check("WS-READINESS", "5 prontidão", "prontidão por ambiente × operação",
          ops == {(e, o) for e in ("production", "restricted") for o in ("consulta", "emissao", "cancelamento")},
          f"{len(ops)} combinações")
    cs = multi.get(f"{CORE}/companies/{A}/certificates")
    check("WS-CERT", "2 certificado", "situação do certificado sem segredos",
          cs.status_code == 200 and "encrypted" not in cs.text and "password" not in cs.text.lower(),
          f"{code(cs)} {cs.json().get('status') if cs.status_code == 200 else ''}")
    # Workspace FICTÍCIO novo (PREPARING): prepara, mas não transmite até a ativação.
    novo = admin.post(f"{CORE}/admin/workspaces", json={"name": "Empresa C (LOCAL)", "company": {
        "cnpj": novo_cnpj(), "name": "Empresa C (LOCAL)", "municipality_code": "3550308",
        "timezone": "America/Sao_Paulo"}})
    check("WS-CREATE", "1/11 novo workspace", "SUPER_ADMIN cria workspace fictício em preparação",
          novo.status_code == 201 and novo.json()["status"] == "PREPARING", code(novo))
    if novo.status_code == 201:
        C_ID = novo.json()["id"]
        admin.put(f"{CORE}/admin/users/{users['multi.ab@descartavel.example.com']['id']}/memberships/{C_ID}",
                  json={"permissions": ["view", "prepare", "schedule", "emit"]})
        multi = Session("multi.ab@descartavel.example.com")
        cust = multi.post(f"{CORE}/companies/{C_ID}/customers", json={
            "name": "Cliente Fictício C", "document_type": "CNPJ", "document": novo_cnpj()})
        d = multi.post(f"{FISCAL}/companies/{C_ID}/drafts", json={
            "customer_id": cust.json().get("id", str(uuid.uuid4())), "competence": datetime.now().strftime("%Y-%m"),
            "amount": "10.00", "service": {"national_code": "010101", "description": "Teste workspace em preparação"}})
        r = multi.post(f"{FISCAL}/companies/{C_ID}/batches", headers={"Idempotency-Key": uuid.uuid4().hex}, json={
            "drafts": [{"id": d.json().get("id", str(uuid.uuid4())), "expected_version": 1}],
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), "timezone": "America/Sao_Paulo"})
        check("WS-PREPARING", "2 preparação", "workspace em preparação: rascunho sim, lote => 422 WORKSPACE_NOT_ACTIVE",
              d.status_code == 201 and r.status_code == 422 and r.json().get("code") == "WORKSPACE_NOT_ACTIVE",
              f"{code(cust)}/{code(d)}/{code(r)}")
        r = op_a.get(f"{FISCAL}/companies/{C_ID}/documents")
        check("WS-NO-IMPLICIT", "4 sem acesso implícito", "operador de A não lê o workspace C", r.status_code == 403, code(r))

    # ------------------------------------------------------------ perfis de serviço a partir das notas EMITIDAS
    sys.path.insert(0, str(ROOT / "tests"))
    sys.path.insert(0, str(ROOT / "services" / "nfse-api"))
    from fixtures_nfse import nota_como_187  # noqa: PLC0415 - XML fictício com a estrutura da nota real

    a_cnpj = next(w["company"]["cnpj"] for w in admin.get(f"{CORE}/admin/workspaces").json() if w["id"] == A)
    base = 41000 + int(time.time()) % 1000

    def xml(num: int, prest: str, toma: str, ctn: str = "072101", nbs: str = "119029000", **kw) -> bytes:
        x = nota_como_187(prest, toma, num, **kw).decode()
        return x.replace("<cTribNac>072101</cTribNac>", f"<cTribNac>{ctn}</cTribNac>").replace(
            "<cNBS>101010100</cNBS>", f"<cNBS>{nbs}</cNBS>").encode()

    toma_f = novo_cnpj()
    arquivos = [("files", (f"e{base + i}.xml", xml(base + i, a_cnpj, toma_f, **kw), "application/xml")) for i, kw in enumerate((
        {}, {"retencoes": True}, {"ibscbs": True}, {"ctn": "071601", "nbs": "101032000"}))]
    arquivos.append(("files", ("r1.xml", xml(base + 9, novo_cnpj(), a_cnpj, ctn="010101", nbs="115021000"), "application/xml")))
    job = op_a.post(f"{FISCAL}/companies/{A}/documents/import", files=arquivos).json()
    for _ in range(60):
        st = op_a.get(f"{FISCAL}/companies/{A}/jobs/{job['job_id']}").json()
        if st.get("status") in ("SUCCEEDED", "PARTIAL", "FAILED"):
            break
        time.sleep(2)
    inv = multi.get(f"{FISCAL}/companies/{A}/service-inventory").json()
    chaves = {g["origin_key"] for g in inv.get("grupos", [])}
    check("PERFIL-INVENTARIO", "1 inventário", "só notas EMITIDAS entram (a recebida 01.01.01 fica fora)",
          {"072101:119029000", "071601:101032000"} <= chaves and not any(k.startswith("010101:") for k in chaves),
          f"grupos={sorted(chaves)}")
    g = next((x for x in inv.get("grupos", []) if x["origin_key"] == "072101:119029000"), {})
    check("PERFIL-VARIACOES", "1 agrupamento", "variações tributárias no mesmo serviço (ISS retido/não retido, IBS/CBS)",
          len(g.get("iss", [])) == 2 and len(g.get("ibs_cbs", [])) == 1 and g.get("ibs_cbs_ausente") == 2, json.dumps(g.get("iss", []))[:120])
    r1 = multi.post(f"{FISCAL}/companies/{A}/service-profiles/prepare").json()
    r2 = multi.post(f"{FISCAL}/companies/{A}/service-profiles/prepare").json()
    check("PERFIL-IDEMPOTENTE", "2 preparação", "preparar duas vezes não duplica nem sobrescreve",
          len(r1.get("created", [])) >= 2 and r2.get("created") == [] and len(r2.get("existing", [])) == len(r1["created"]),
          f"1ª={len(r1.get('created', []))} 2ª criados={len(r2.get('created', []))}")
    perfis = {x["origin_key"]: x for x in multi.get(f"{CORE}/companies/{A}/service-profiles").json()}
    pf = perfis.get("071601:101032000", {})
    check("PERFIL-PENDENTE", "2 estado", "perfil preparado fica pendente, com origem dos campos e pendência exata",
          pf.get("status") == "PENDING_REVIEW" and pf.get("field_sources", {}).get("national_code", {}).get("origem") == "xml"
          and any(i["code"] == "IBS_CBS_REQUIRED" for i in pf.get("pending", [])), pf.get("status", "-"))
    r = op_b.get(f"{CORE}/companies/{A}/service-profiles")
    check("PERFIL-ISOLAMENTO", "3 isolamento", "usuário só de B não vê perfis de A", r.status_code == 403, code(r))
    r = leitor.post(f"{FISCAL}/companies/{A}/service-profiles/prepare")
    check("PERFIL-PERMISSAO", "3 permissão", "leitor não prepara perfis", r.status_code == 403, code(r))
    cust_a = op_a.get(f"{CORE}/companies/{A}/customers").json()["items"][0]["id"]
    ibs = {"operation_indicator": "100301", "cst": "000", "class_code": "000001"}
    d = op_a.post(f"{FISCAL}/companies/{A}/drafts", json={
        "customer_id": cust_a, "competence": datetime.now().strftime("%Y-%m"), "amount": "10.00",
        "service_date": datetime.now().strftime("%Y-%m-%d"), "service_profile_id": pf.get("id"),
        "service": {"national_code": "071601", "nbs_code": "101032000", "description": "Teste de perfil pendente"},
        "tax_settings": {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "ibs_cbs": ibs}}).json()
    rb = op_a.post(f"{FISCAL}/companies/{A}/batches", headers={"Idempotency-Key": uuid.uuid4().hex}, json={
        "drafts": [{"id": d.get("id"), "expected_version": d.get("version", 1)}],
        "scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), "timezone": "America/Sao_Paulo"})
    check("PERFIL-BLOQUEIA", "3 transmissão", "rascunho com perfil pendente é preparado, mas não agenda",
          d.get("service_profile_version") == 1 and rb.status_code == 422 and "SERVICE_PROFILE_NOT_APPROVED" in rb.text,
          f"{code(rb)}")
    url = f"{CORE}/companies/{A}/service-profiles/{pf.get('id')}"
    multi.patch(url, json={"expected_version": 1, "ibs_cbs": ibs})
    ra = multi.post(f"{url}/approve", json={"expected_version": 2})
    hist = multi.get(f"{url}/versions").json()
    check("PERFIL-APROVACAO", "3 revisão", "aprovação explícita com histórico de versões (preparado → editado → aprovado)",
          ra.status_code == 200 and ra.json()["status"] == "APPROVED" and [h["action"] for h in hist][:3] == ["approved", "updated", "prepared"],
          code(ra))
    rd = multi.post(f"{FISCAL}/companies/{A}/drafts/{d.get('id')}/validate").json()
    check("PERFIL-LIBERA", "3 transmissão", "após a aprovação, o perfil deixa de bloquear (aviso de versão alterada)",
          "SERVICE_PROFILE_NOT_APPROVED" not in {i["code"] for i in rd.get("issues", [])}
          and "SERVICE_PROFILE_CHANGED" in {i["code"] for i in rd.get("issues", [])}, "ok")
    pdoc = multi.get(f"{FISCAL}/companies/{A}/provider-data-from-documents").json()
    check("PRESTADOR-NOTAS", "4 cadastro", "dados do prestador lidos do grupo emit das notas emitidas",
          pdoc.get("total_notas", 0) >= 4 and pdoc["campos"]["municipal_registration"]["consistente"], str(pdoc.get("total_notas")))
    rp = multi.post(f"{FISCAL}/companies/{A}/provider-data-from-documents/apply").json()
    rp2 = multi.post(f"{FISCAL}/companies/{A}/provider-data-from-documents/apply").json()
    check("PRESTADOR-SO-VAZIOS", "4 cadastro", "completa só campos vazios; segunda execução não altera nada",
          rp2.get("applied") == {} and (rp.get("applied") or rp.get("equal")), f"aplicados={sorted(rp.get('applied', {}))}")
    rr = multi.get(f"{CORE}/companies/{A}/readiness").json()
    check("PRONTIDAO-SECOES", "4 prontidão", "prontidão em seções (cadastro, certificado, perfil, nota, ambiente)",
          [s["id"] for s in rr.get("sections", [])] == ["cadastro", "certificado", "perfil", "nota", "ambiente"], "ok")

    # Conta nova: ativação de uso único, depois desativação revoga o acesso já aberto.
    email = f"temp.{uuid.uuid4().hex[:6]}@descartavel.example.com"
    r = admin.post(f"{CORE}/admin/users", json={"name": "Temporário Teste", "email": email,
                                                "memberships": [{"company_id": A, "permissions": ["view"]}]})
    token = r.json()["credential_link"]["path"].split("#token=", 1)[1]
    pw_tmp = "Tmp-" + uuid.uuid4().hex
    def activate(password: str) -> httpx.Response:
        for _ in range(20):  # /auth/activate divide a zona de limite do login no gateway
            rr = anon.post(f"{CORE}/auth/activate", json={"token": token, "password": password}, headers={"Origin": BASE})
            if rr.status_code != 429:
                return rr
            time.sleep(7)
        return rr

    a1 = activate(pw_tmp)
    a2 = activate(pw_tmp + "x")
    check("AUTH-ACTIVATE", "2.1 ativação", "link de ativação de uso único", a1.status_code == 204 and a2.status_code == 400,
          f"{code(a1)} / {code(a2)}")
    t = Session(email, pw_tmp)
    ok_before = t.get(f"{FISCAL}/companies/{A}/documents").status_code
    uid = r.json()["user"]["id"]
    v = admin.get(f"{CORE}/admin/users/{uid}").json()["version"]
    admin.patch(f"{CORE}/admin/users/{uid}", json={"is_active": False, "expected_version": v})
    after = t.get(f"{FISCAL}/companies/{A}/documents")
    check("AUTH-REVOKE", "2.1 revogação", "desativar usuário invalida token ainda não expirado",
          ok_before == 200 and after.status_code == 401, f"antes {ok_before} depois {code(after)}")

    # ------------------------------------------------------------ 2.5 uploads: XML com entidade e ZIP com ../
    bomb = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'
            b'<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse">&lol2;</NFSe>')
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("../../fora.xml", "<x/>")
    r = op_a.post(f"{FISCAL}/companies/{A}/documents/import",
                  files=[("files", ("bomba.xml", bomb, "application/xml")), ("files", ("trav.zip", zbuf.getvalue(), "application/zip"))])
    job = r.json().get("job_id") if r.status_code == 202 else None
    st = {}
    for _ in range(40):
        if not job:
            break
        st = op_a.get(f"{FISCAL}/companies/{A}/jobs/{job}").json()
        if st.get("status") in ("SUCCEEDED", "PARTIAL", "FAILED"):
            break
        time.sleep(1.5)
    codes = sorted({e["code"] for e in st.get("errors", [])})
    check("INPUT-XML", "2.5 XML/ZIP", "DTD/entidades e caminho ../ rejeitados como dados (nada importado)",
          bool(job) and st.get("processed_count", 0) == 0 or (st.get("error_count", 0) >= 2), f"{st.get('status')} {codes}")

    # ------------------------------------------------------------ 2.9 tickets de download
    r = op_a.post(f"{FISCAL}/companies/{A}/exports", json={"format": "xlsx", "environment": "mock"})
    ej = r.json().get("job_id")
    for _ in range(60):
        s = op_a.get(f"{FISCAL}/companies/{A}/jobs/{ej}").json()
        if s["status"] in ("SUCCEEDED", "FAILED", "PARTIAL"):
            break
        time.sleep(1.5)
    tk = op_a.post(f"{FISCAL}/companies/{A}/jobs/{ej}/download-ticket").json()["ticket"]
    d1 = anon.post(f"{FISCAL}/companies/{A}/jobs/{ej}/download-file", data={"ticket": tk})
    d2 = anon.post(f"{FISCAL}/companies/{A}/jobs/{ej}/download-file", data={"ticket": tk})
    check("TICKET-ONCE", "2.9 ticket", "ticket de uso único (2º uso recusado)", d1.status_code == 200 and d2.status_code == 403,
          f"{d1.status_code} / {code(d2)}")
    tk2 = op_a.post(f"{FISCAL}/companies/{A}/jobs/{ej}/download-ticket").json()["ticket"]
    d3 = anon.post(f"{FISCAL}/companies/{B}/jobs/{ej}/download-file", data={"ticket": tk2})
    check("TICKET-COMPANY", "2.9 ticket", "ticket de A não serve na rota de B", d3.status_code in (403, 404), code(d3))
    check("TICKET-NOLEAK", "2.8 logs/erros", "resposta de erro não ecoa o ticket", tk2 not in d3.text, "sem eco")

    # ------------------------------------------------------------ 2.10 emissão: GET não emite; worker confere autorização
    def draft(sess: Session, amount: str) -> dict:
        cust = sess.get(f"{CORE}/companies/{A}/customers").json()["items"][0]["id"]
        rr = sess.post(f"{FISCAL}/companies/{A}/drafts", json={
            "customer_id": cust, "competence": datetime.now().strftime("%Y-%m"), "amount": amount,
            "service": {"national_code": "010101", "description": "Serviço de validação (descartável)"},
            "tax_settings": {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED"}})
        rr.raise_for_status()
        return rr.json()

    def batch(sess: Session, d: dict) -> str:
        rr = sess.post(f"{FISCAL}/companies/{A}/batches", headers={"Idempotency-Key": uuid.uuid4().hex}, json={
            "drafts": [{"id": d["id"], "expected_version": d["version"]}],
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat(), "timezone": "America/Sao_Paulo"})
        if rr.status_code != 202:
            raise RuntimeError(code(rr))
        return rr.json()["batch_id"]

    def wait_batch(bid: str, until: tuple, secs: int = 150) -> dict:
        it = {}
        for _ in range(secs // 3):
            it = op_a.get(f"{FISCAL}/companies/{A}/batches/{bid}").json()["items"][0]
            if it["status"] in until:
                return it
            time.sleep(3)
        return it

    before = op_a.get(f"{FISCAL}/companies/{A}/batches").json()["total"]
    for _ in range(3):
        op_a.get(f"{FISCAL}/companies/{A}/dashboard")
        op_a.get(f"{FISCAL}/companies/{A}/documents")
    check("OPS-GET", "2.10 sem emissão acidental", "consultas GET não criam lotes",
          op_a.get(f"{FISCAL}/companies/{A}/batches").json()["total"] == before, "inalterado")
    r = leitor.post(f"{FISCAL}/companies/{A}/batches", headers={"Idempotency-Key": uuid.uuid4().hex},
                    json={"drafts": [], "scheduled_at": datetime.now(timezone.utc).isoformat(), "timezone": "America/Sao_Paulo"})
    check("PERM-EMIT", "2.2 permissões", "sem 'schedule/emit' não agenda", r.status_code in (403, 422), code(r))

    # Revogação de 'emit' depois do agendamento: worker retira o item sem transmitir.
    d_rev = draft(op_a, "11.00")
    bid_rev = batch(op_a, d_rev)
    oid = users["operador.a@descartavel.example.com"]["id"]
    sem_emit = [p for p in ["view", "import", "sync", "review", "prepare", "schedule", "cancel"]]
    admin.put(f"{CORE}/admin/users/{oid}/memberships/{A}", json={"permissions": sem_emit})
    op_a = Session("operador.a@descartavel.example.com")  # sessão revogada pela redução; novo login
    it = wait_batch(bid_rev, ("WITHDRAWN", "AUTHORIZED"))
    check("WORKER-AUTHZ", "2.10 worker", "permissão 'emit' retirada após o agendamento => item retirado, nada transmitido",
          it.get("status") == "WITHDRAWN" and (it.get("last_error") or {}).get("code") == "AUTHORIZATION_REVOKED",
          f"{it.get('status')} {(it.get('last_error') or {}).get('code')}")
    admin.put(f"{CORE}/admin/users/{oid}/memberships/{A}",
              json={"permissions": ["view", "import", "sync", "review", "prepare", "schedule", "emit", "cancel"]})
    op_a = Session("operador.a@descartavel.example.com")

    # ------------------------------------------------------------ 4 cancelamento ponta a ponta (simulado)
    d_ok = draft(op_a, "123.45")
    bid = batch(op_a, d_ok)
    it = wait_batch(bid, ("AUTHORIZED", "REJECTED", "WITHDRAWN"))
    check("EMIT-MOCK", "regressão emissão", "nota simulada autorizada pelo worker", it.get("status") == "AUTHORIZED",
          str(it.get("status")))
    doc_id = it.get("document_id")
    info = op_a.get(f"{FISCAL}/companies/{A}/documents/{doc_id}/cancellation").json()
    check("CANCEL-ELIG", "4 elegibilidade", "nota emitida elegível; permissão refletida",
          info.get("eligible", {}).get("CANCEL") is True and info.get("can_request") is True, json.dumps(info.get("eligible")))
    d = info["document"]
    body = {"kind": "CANCEL", "reason_code": "1", "reason_text": "Erro na emissao: teste no ambiente descartavel",
            "confirm": {"access_key": d["access_key"], "environment": d["environment"], "company_cnpj": info["company"]["cnpj"],
                        "document_number": d["number"]}}
    r = leitor.post(f"{FISCAL}/companies/{A}/documents/{doc_id}/cancellation", json=body,
                    headers={"Idempotency-Key": uuid.uuid4().hex})
    check("CANCEL-PERM", "4 permissão", "leitor não cancela", r.status_code == 403, code(r))
    bad = {**body, "confirm": {**body["confirm"], "document_number": "999999"}}
    r = op_a.post(f"{FISCAL}/companies/{A}/documents/{doc_id}/cancellation", json=bad, headers={"Idempotency-Key": uuid.uuid4().hex})
    check("CANCEL-CONFIRM", "4 confirmação", "confirmação divergente recusada", r.status_code == 409, code(r))
    key = uuid.uuid4().hex
    r1 = op_a.post(f"{FISCAL}/companies/{A}/documents/{doc_id}/cancellation", json=body, headers={"Idempotency-Key": key})
    r2 = op_a.post(f"{FISCAL}/companies/{A}/documents/{doc_id}/cancellation", json=body, headers={"Idempotency-Key": key})
    r3 = op_a.post(f"{FISCAL}/companies/{A}/documents/{doc_id}/cancellation", json=body, headers={"Idempotency-Key": uuid.uuid4().hex})
    check("CANCEL-IDEMP", "4 duplicidade", "mesmo clique repetido = mesmo pedido; outro pedido com o ativo => 409",
          r1.status_code == 202 and r2.json().get("id") == r1.json().get("id") and r3.status_code == 409,
          f"{code(r1)} {code(r2)} {code(r3)}")
    st_doc = op_a.get(f"{FISCAL}/companies/{A}/documents/{doc_id}").json()["status"]
    check("CANCEL-NOSTATUS", "4 situação", "nota continua autorizada antes do evento", st_doc == "AUTHORIZED", st_doc)
    rid = r1.json()["id"]
    req = {}
    for _ in range(40):
        req = op_a.get(f"{FISCAL}/companies/{A}/cancellations/{rid}").json()
        if req["status"] not in ("QUEUED", "PROCESSING", "AWAITING_CONFIRMATION"):
            break
        time.sleep(3)
    docj = op_a.get(f"{FISCAL}/companies/{A}/documents/{doc_id}").json()
    check("CANCEL-FLOW", "4 cancelamento", "evento SIMULADO registrado e aplicado (nota CANCELADA, evento e101101)",
          req.get("status") == "REGISTERED" and req.get("simulated") is True and docj["status"] == "CANCELLED"
          and any(e["event_type"] == "e101101" for e in docj.get("events", [])), f"{req.get('status')} {docj['status']}")

    # ------------------------------------------------------------ 2.8 auditoria imutável (usuário da aplicação)
    for svc, dbname in (("fiscal-api", "FISCAL"), ("core-api", "CORE")):
        probe = PROBE_AUDIT.replace("__DB__", dbname)
        out = subprocess.run(["docker", "exec", f"{PROJECT}-{svc}-1", "python", "-c", probe], capture_output=True, text=True)
        words = out.stdout.split()
        check(f"AUDIT-IMMUTABLE-{dbname}", "2.8 auditoria", f"usuário da aplicação não altera nem apaga audit_events ({dbname})",
              len(words) >= 3 and words[0] == "RECUSADO" and int(words[1]) > 0 and "RECUSADO-DEL" in words,
              (out.stdout + out.stderr).strip()[-120:])

    out_file = ROOT / "docs/evidencias" / f"validacao-descartavel-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
    fails = [x for x in RESULTS if x["resultado"] != "PASS"]
    out_file.write_text(json.dumps({"ambiente": BASE, "projeto": PROJECT, "gerado_em": datetime.now(timezone.utc).isoformat(),
                                    "total": len(RESULTS), "falhas": len(fails), "resultados": RESULTS},
                                   indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} PASS — {out_file.relative_to(ROOT)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
