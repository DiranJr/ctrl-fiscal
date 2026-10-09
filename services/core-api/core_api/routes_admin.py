"""Administração de usuários (Administração → Usuários).

Dois escopos, ambos validados AQUI (esconder botões não é autorização):
- SUPER_ADMIN (users.is_super_admin): gestão GLOBAL de usuários. Não dá acesso fiscal: documentos e dados das
  empresas continuam exigindo Membership (company_access / authz interno).
- Admin de empresa (permissão "admin" na Membership): administra vínculos SOMENTE nas empresas em que é admin; altera
  identidade/status só de usuários cujos vínculos estejam todos nessas empresas; nunca concede SUPER_ADMIN.

Regras transversais:
- Não é possível desativar a si mesmo nem remover/desativar o último SUPER_ADMIN ativo.
- Desativar, reduzir/remover permissões ou vínculos e redefinir senha revogam as sessões do usuário afetado;
  o access token restante deixa de valer na próxima checagem (sessão e vínculo são conferidos a cada requisição).
- Auditoria de cada mudança, sem senha, token ou valor de e-mail.
- Credenciais: conta nasce SEM senha; o administrador recebe um link de ativação de uso único (24 h) e repassa ao
  usuário, que define a própria senha em POST /auth/activate.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ctrl_common.problems import ProblemError, problem_responses, trace_id_for

from .app import CurrentUser, audit, ctx, current_user, db_session
from .models import Company, CredentialToken, Membership, RefreshSession, User
from .schemas import (
    AdminCompanyOut,
    AdminMembershipIn,
    AdminMeta,
    AdminUserCreate,
    AdminUserCreated,
    AdminUserOut,
    AdminUserPage,
    AdminUserPatch,
    CredentialLink,
    SuperAdminIn,
)
from .security import sha256_hex

router = APIRouter(prefix="/admin", tags=["admin"])
ERRS = problem_responses(401, 403, 404, 409, 422)
# Permissões separadas por operação (privilégio mínimo). "admin" = administrar usuários/vínculos e perfil fiscal
# da empresa; NÃO implica as demais (cada operação exige a sua).
PERMISSIONS = ["view", "import", "sync", "review", "prepare", "schedule", "emit", "cancel", "certificates", "admin"]
PERMISSION_LABELS = {
    "view": "Consultar", "import": "Importar XML", "sync": "Sincronizar com o ADN", "review": "Conferir",
    "prepare": "Preparar rascunhos", "schedule": "Agendar lotes", "emit": "Emitir (transmitir NFS-e)",
    "cancel": "Cancelar NFS-e", "certificates": "Gerenciar certificado digital", "admin": "Administrar usuários da empresa",
}
PENDING_HASH = "!pending"
TOKEN_TTL = timedelta(hours=24)


@dataclass
class AdminScope:
    actor: User
    session_id: str
    super_admin: bool
    admin_companies: set[str] = field(default_factory=set)
    own_permissions: dict[str, set[str]] = field(default_factory=dict)

    def can_company(self, company_id: str) -> bool:
        return self.super_admin or company_id in self.admin_companies

    def grant_ceiling(self, company_id: str) -> set[str] | None:
        """None = sem teto (SUPER_ADMIN). Admin de empresa só concede o que ele próprio tem naquela empresa."""
        return None if self.super_admin else self.own_permissions.get(company_id, set())


def admin_scope(cu: CurrentUser = Depends(current_user), s: Session = Depends(db_session)) -> AdminScope:
    own = {
        m.company_id: set(m.permissions or [])
        for m, c in s.execute(select(Membership, Company).join(Company, Company.id == Membership.company_id)
                              .where(Membership.user_id == cu.user.id, Company.is_active.is_(True))).all()
    }
    admin_cos = {cid for cid, perms in own.items() if "admin" in perms}
    if not cu.user.is_super_admin and not admin_cos:
        raise ProblemError(403, "ADMIN_FORBIDDEN", "Sem permissão de administração de usuários.")
    return AdminScope(cu.user, cu.session_id, bool(cu.user.is_super_admin), admin_cos, own)


def _memberships(s: Session, user_id: str) -> list[tuple[Membership, Company]]:
    return s.execute(select(Membership, Company).join(Company, Company.id == Membership.company_id)
                     .where(Membership.user_id == user_id).order_by(Company.name)).all()


def _visible(s: Session, sc: AdminScope, user: User) -> bool:
    if sc.super_admin:
        return True
    return any(m.company_id in sc.admin_companies for m, _ in _memberships(s, user.id))


def _can_manage_identity(s: Session, sc: AdminScope, user: User) -> bool:
    """Nome, e-mail, ativação e senha: super admin; admin de empresa só se TODOS os vínculos estão no seu escopo."""
    if sc.super_admin:
        return True
    if user.is_super_admin:
        return False
    ms = _memberships(s, user.id)
    return bool(ms) and all(m.company_id in sc.admin_companies for m, _ in ms)


def _get_user(s: Session, sc: AdminScope, user_id: str, lock: bool = False) -> User:
    q = select(User).where(User.id == user_id)
    if lock:
        q = q.with_for_update()
    u = s.scalar(q)
    if u is None or not _visible(s, sc, u):
        raise ProblemError(404, "USER_NOT_FOUND", "Usuário não encontrado.")
    return u


def _status(u: User) -> str:
    if not u.is_active:
        return "INACTIVE"
    return "PENDING" if u.password_hash == PENDING_HASH else "ACTIVE"


def _active_sessions(s: Session, user_id: str, now: datetime) -> int:
    return s.scalar(select(func.count()).select_from(RefreshSession).where(
        RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None), RefreshSession.absolute_expires_at > now)) or 0


def user_out(s: Session, sc: AdminScope, u: User, now: datetime) -> dict:
    ms = [(m, c) for m, c in _memberships(s, u.id) if sc.can_company(m.company_id)]
    hidden = 0 if sc.super_admin else len(_memberships(s, u.id)) - len(ms)
    return {
        "id": u.id, "name": u.name, "email": u.email, "status": _status(u), "is_active": u.is_active,
        "is_super_admin": bool(u.is_super_admin), "version": u.version or 1,
        "memberships": [{"company_id": c.id, "company_name": c.name, "company_cnpj": c.cnpj, "company_active": c.is_active,
                         "permissions": [p for p in PERMISSIONS if p in (m.permissions or [])]} for m, c in ms],
        "other_companies": hidden,
        "can_manage_identity": _can_manage_identity(s, sc, u),
        "active_sessions": _active_sessions(s, u.id, now),
        "password_set_at": u.password_set_at, "created_at": u.created_at, "updated_at": u.updated_at,
    }


def revoke_sessions(s: Session, user_id: str, now: datetime, reason: str) -> int:
    res = s.execute(update(RefreshSession).where(RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None))
                    .values(revoked_at=now, revoked_reason=reason).execution_options(synchronize_session=False))
    return res.rowcount or 0


def _lock_active_super_admins(s: Session) -> int:
    """Trava (SELECT ... FOR UPDATE, em ordem de id) as linhas dos SUPER_ADMIN ativos e devolve quantos são.

    Chamado ANTES de travar o usuário-alvo em toda operação que pode reduzir o conjunto (desativar, rebaixar):
    duas requisições concorrentes serializam nesta trava, na mesma ordem (sem deadlock), e a segunda recontagem já
    enxerga o resultado da primeira. Assim não é possível ficar sem SUPER_ADMIN ativo por corrida."""
    return len(list(s.scalars(select(User.id).where(User.is_super_admin.is_(True), User.is_active.is_(True))
                              .order_by(User.id).with_for_update())))


def _check_grant(sc: AdminScope, company_id: str, old: list[str], new: list[str]) -> None:
    ceiling = sc.grant_ceiling(company_id)
    if ceiling is None:
        return
    above = sorted(set(new) - set(old) - ceiling)
    if above:
        raise ProblemError(403, "PERMISSION_ABOVE_OWN",
                           "Você só pode conceder permissões que possui nesta empresa: " + ", ".join(above) + ".")


def issue_credential(s: Session, user: User, purpose: str, actor_id: str | None, now: datetime) -> CredentialLink:
    """Gera link de uso único; invalida links anteriores não usados do mesmo usuário."""
    s.execute(update(CredentialToken).where(CredentialToken.user_id == user.id, CredentialToken.used_at.is_(None),
                                            CredentialToken.revoked_at.is_(None))
              .values(revoked_at=now).execution_options(synchronize_session=False))
    token = secrets.token_urlsafe(32)
    exp = now + TOKEN_TTL
    s.add(CredentialToken(token_hash=sha256_hex(token), user_id=user.id, purpose=purpose, expires_at=exp,
                          created_by=actor_id, created_at=now))
    # O token vai no FRAGMENTO (#), que o navegador não envia ao servidor nem registra em logs de acesso.
    return CredentialLink(purpose=purpose, expires_at=exp, path=f"/ativar#token={token}")


def _check_permissions(perms: list[str]) -> list[str]:
    bad = [p for p in perms if p not in PERMISSIONS]
    if bad:
        raise ProblemError(422, "PERMISSION_UNKNOWN", f"Permissões inexistentes: {', '.join(bad)}.")
    if perms and "view" not in perms:
        raise ProblemError(422, "PERMISSION_VIEW_REQUIRED", "Todo vínculo precisa da permissão 'view'.")
    return [p for p in PERMISSIONS if p in perms]


# ------------------------------------------------------------------------------------------------ leitura
@router.get("/meta", response_model=AdminMeta, responses=ERRS)
def meta(sc: AdminScope = Depends(admin_scope)) -> dict:
    return {"is_super_admin": sc.super_admin, "admin_company_ids": sorted(sc.admin_companies),
            "permissions": [{"id": p, "label": PERMISSION_LABELS[p]} for p in PERMISSIONS]}


@router.get("/companies", response_model=list[AdminCompanyOut], responses=ERRS)
def companies(sc: AdminScope = Depends(admin_scope), s: Session = Depends(db_session)) -> list[dict]:
    q = select(Company).where(Company.is_active.is_(True)).order_by(Company.name)
    if not sc.super_admin:
        q = q.where(Company.id.in_(sc.admin_companies))
    return [{"id": c.id, "name": c.name, "cnpj": c.cnpj} for c in s.scalars(q)]


@router.get("/users", response_model=AdminUserPage, responses=ERRS)
def list_users(
    request: Request,
    query: str | None = Query(None, max_length=100),
    status: str | None = Query(None, pattern="^(ACTIVE|INACTIVE|PENDING)$"),
    company_id: str | None = Query(None, max_length=36),
    permission: str | None = Query(None, pattern="^(" + "|".join(PERMISSIONS) + ")$"),
    super_admin: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    sc: AdminScope = Depends(admin_scope),
    s: Session = Depends(db_session),
) -> dict:
    now = ctx(request).clock.now()
    q = select(User)
    if not sc.super_admin:
        q = q.where(User.id.in_(select(Membership.user_id).where(Membership.company_id.in_(sc.admin_companies))))
    if company_id:
        if not sc.can_company(company_id):
            raise ProblemError(403, "COMPANY_FORBIDDEN", "Empresa fora do seu escopo de administração.")
        q = q.where(User.id.in_(select(Membership.user_id).where(Membership.company_id == company_id)))
    if query:
        like = "%" + query.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        q = q.where(or_(func.lower(User.name).like(like, escape="\\"), func.lower(User.email).like(like, escape="\\")))
    if status == "INACTIVE":
        q = q.where(User.is_active.is_(False))
    elif status == "PENDING":
        q = q.where(User.is_active.is_(True), User.password_hash == PENDING_HASH)
    elif status == "ACTIVE":
        q = q.where(User.is_active.is_(True), User.password_hash != PENDING_HASH)
    if super_admin is not None:
        if not sc.super_admin:
            raise ProblemError(403, "ADMIN_FORBIDDEN", "Filtro disponível só para SUPER_ADMIN.")
        q = q.where(User.is_super_admin.is_(super_admin))
    rows = list(s.scalars(q.order_by(User.name, User.id)))
    if permission:  # filtrado em Python: permissões são lista JSON (só vínculos visíveis ao ator contam)
        def has(u: User) -> bool:
            return any(permission in (m.permissions or []) and sc.can_company(m.company_id) and
                       (company_id is None or m.company_id == company_id) for m, _ in _memberships(s, u.id))
        rows = [u for u in rows if has(u)]
    total = len(rows)
    items = rows[(page - 1) * page_size: page * page_size]
    return {"items": [user_out(s, sc, u, now) for u in items], "page": page, "page_size": page_size, "total": total}


@router.get("/users/{user_id}", response_model=AdminUserOut, responses=ERRS)
def get_user(user_id: str, request: Request, sc: AdminScope = Depends(admin_scope), s: Session = Depends(db_session)) -> dict:
    return user_out(s, sc, _get_user(s, sc, user_id), ctx(request).clock.now())


# ------------------------------------------------------------------------------------------------ escrita
@router.post("/users", status_code=201, response_model=AdminUserCreated, responses=ERRS)
def create_user(body: AdminUserCreate, request: Request, sc: AdminScope = Depends(admin_scope),
                s: Session = Depends(db_session)) -> dict:
    now = ctx(request).clock.now()
    email = body.email.strip().lower()
    if not sc.super_admin and not body.memberships:
        raise ProblemError(422, "MEMBERSHIP_REQUIRED", "Admin de empresa precisa vincular o usuário a uma empresa sua.")
    for m in body.memberships:
        if not sc.can_company(m.company_id) or s.get(Company, m.company_id) is None:
            raise ProblemError(403, "COMPANY_FORBIDDEN", "Empresa fora do seu escopo de administração.")
        _check_grant(sc, m.company_id, [], _check_permissions(m.permissions))
    if s.scalar(select(User.id).where(func.lower(User.email) == email)):
        raise ProblemError(409, "EMAIL_IN_USE", "Já existe usuário com este e-mail.")
    u = User(email=email, name=body.name.strip(), password_hash=PENDING_HASH, is_active=True, is_super_admin=False,
             version=1, created_at=now, updated_at=now)
    s.add(u)
    s.flush()
    for m in body.memberships:
        s.add(Membership(user_id=u.id, company_id=m.company_id, permissions=_check_permissions(m.permissions),
                         created_at=now, updated_at=now))
    link = issue_credential(s, u, "ACTIVATE", sc.actor.id, now)
    tid = trace_id_for(request)
    audit(s, now, "admin.user_created", actor_id=sc.actor.id, target_type="user", target_id=u.id, trace_id=tid,
          details={"memberships": [{"company_id": m.company_id, "permissions": _check_permissions(m.permissions)}
                                   for m in body.memberships], "by_super_admin": sc.super_admin})
    s.commit()
    return {"user": user_out(s, sc, u, now), "credential_link": link}


@router.patch("/users/{user_id}", response_model=AdminUserOut, responses=ERRS)
def patch_user(user_id: str, body: AdminUserPatch, request: Request, sc: AdminScope = Depends(admin_scope),
               s: Session = Depends(db_session)) -> dict:
    now = ctx(request).clock.now()
    active_supers = _lock_active_super_admins(s) if body.is_active is False else None
    u = _get_user(s, sc, user_id, lock=True)
    if not _can_manage_identity(s, sc, u):
        raise ProblemError(403, "USER_OUTSIDE_SCOPE", "Usuário com vínculos fora do seu escopo: só o SUPER_ADMIN altera.")
    if body.expected_version != (u.version or 1):
        raise ProblemError(409, "VERSION_CONFLICT", "Usuário alterado por outra pessoa. Recarregue.")
    changed: list[str] = []
    if body.name is not None and body.name.strip() != u.name:
        u.name = body.name.strip()
        changed.append("name")
    if body.email is not None and body.email.strip().lower() != u.email:
        email = body.email.strip().lower()
        if s.scalar(select(User.id).where(func.lower(User.email) == email, User.id != u.id)):
            raise ProblemError(409, "EMAIL_IN_USE", "Já existe usuário com este e-mail.")
        u.email = email
        changed.append("email")
    revoked = 0
    if body.is_active is not None and body.is_active != u.is_active:
        if not body.is_active:
            if u.id == sc.actor.id:
                raise ProblemError(409, "SELF_DEACTIVATION", "Você não pode desativar a própria conta.")
            if u.is_super_admin and (active_supers or 0) <= 1:
                raise ProblemError(409, "LAST_SUPER_ADMIN", "Não é possível desativar o último SUPER_ADMIN ativo.")
            revoked = revoke_sessions(s, u.id, now, "user_deactivated")
        u.is_active = body.is_active
        changed.append("is_active")
    if changed:
        u.version = (u.version or 1) + 1
        u.updated_at = now
        action = ("admin.user_deactivated" if "is_active" in changed and not u.is_active else
                  "admin.user_reactivated" if "is_active" in changed else "admin.user_updated")
        audit(s, now, action, actor_id=sc.actor.id, target_type="user", target_id=u.id, trace_id=trace_id_for(request),
              details={"fields": changed, "sessions_revoked": revoked})
    s.commit()
    return user_out(s, sc, u, now)


@router.put("/users/{user_id}/memberships/{company_id}", response_model=AdminUserOut, responses=ERRS)
def set_membership(user_id: str, company_id: str, body: AdminMembershipIn, request: Request,
                   sc: AdminScope = Depends(admin_scope), s: Session = Depends(db_session)) -> dict:
    now = ctx(request).clock.now()
    if not sc.can_company(company_id):
        raise ProblemError(403, "COMPANY_FORBIDDEN", "Empresa fora do seu escopo de administração.")
    company = s.get(Company, company_id)
    if company is None:
        raise ProblemError(404, "COMPANY_NOT_FOUND", "Empresa não encontrada.")
    u = s.scalar(select(User).where(User.id == user_id).with_for_update())
    if u is None or not (sc.super_admin or _visible(s, sc, u)):
        raise ProblemError(404, "USER_NOT_FOUND", "Usuário não encontrado.")
    if not sc.super_admin and u.id == sc.actor.id:
        raise ProblemError(409, "SELF_MEMBERSHIP", "Admin de empresa não altera o próprio vínculo.")
    perms = _check_permissions(body.permissions)
    if not perms:
        raise ProblemError(422, "PERMISSION_VIEW_REQUIRED", "Informe ao menos 'view'; para remover o vínculo use DELETE.")
    m = s.scalar(select(Membership).where(Membership.user_id == u.id, Membership.company_id == company_id).with_for_update())
    old = list(m.permissions or []) if m else []
    _check_grant(sc, company_id, old, perms)
    if m is None:
        s.add(Membership(user_id=u.id, company_id=company_id, permissions=perms, created_at=now, updated_at=now))
    else:
        m.permissions = perms
        m.updated_at = now
    reduced = sorted(set(old) - set(perms))
    revoked = revoke_sessions(s, u.id, now, "permissions_reduced") if reduced else 0
    audit(s, now, "admin.membership_set", actor_id=sc.actor.id, company_id=company_id, target_type="user", target_id=u.id,
          trace_id=trace_id_for(request), details={"from": old, "to": perms, "removed": reduced, "sessions_revoked": revoked})
    s.commit()
    return user_out(s, sc, u, now)


@router.delete("/users/{user_id}/memberships/{company_id}", response_model=AdminUserOut, responses=ERRS)
def remove_membership(user_id: str, company_id: str, request: Request, sc: AdminScope = Depends(admin_scope),
                      s: Session = Depends(db_session)) -> dict:
    now = ctx(request).clock.now()
    if not sc.can_company(company_id):
        raise ProblemError(403, "COMPANY_FORBIDDEN", "Empresa fora do seu escopo de administração.")
    u = _get_user(s, sc, user_id, lock=True)
    if not sc.super_admin and u.id == sc.actor.id:
        raise ProblemError(409, "SELF_MEMBERSHIP", "Admin de empresa não altera o próprio vínculo.")
    m = s.scalar(select(Membership).where(Membership.user_id == u.id, Membership.company_id == company_id).with_for_update())
    if m is None:
        raise ProblemError(404, "MEMBERSHIP_NOT_FOUND", "Vínculo não encontrado.")
    old = list(m.permissions or [])
    s.delete(m)
    revoked = revoke_sessions(s, u.id, now, "membership_removed")
    audit(s, now, "admin.membership_removed", actor_id=sc.actor.id, company_id=company_id, target_type="user",
          target_id=u.id, trace_id=trace_id_for(request), details={"from": old, "sessions_revoked": revoked})
    s.commit()
    return user_out(s, sc, u, now)  # só vínculos do escopo do ator; os demais aparecem apenas como contagem


@router.put("/users/{user_id}/super-admin", response_model=AdminUserOut, responses=ERRS)
def set_super_admin(user_id: str, body: SuperAdminIn, request: Request, sc: AdminScope = Depends(admin_scope),
                    s: Session = Depends(db_session)) -> dict:
    now = ctx(request).clock.now()
    if not sc.super_admin:
        raise ProblemError(403, "SUPER_ADMIN_REQUIRED", "Somente SUPER_ADMIN concede ou remove SUPER_ADMIN.")
    active_supers = _lock_active_super_admins(s) if not body.enabled else None
    u = _get_user(s, sc, user_id, lock=True)
    if bool(u.is_super_admin) == body.enabled:
        return user_out(s, sc, u, now)
    if body.enabled and (not u.is_active or u.password_hash == PENDING_HASH):
        raise ProblemError(409, "USER_NOT_ACTIVE", "Conceda SUPER_ADMIN apenas a conta ativa com senha definida.")
    if not body.enabled and u.is_active and (active_supers or 0) <= 1:
        raise ProblemError(409, "LAST_SUPER_ADMIN", "Não é possível remover o último SUPER_ADMIN ativo.")
    u.is_super_admin = body.enabled
    u.version = (u.version or 1) + 1
    u.updated_at = now
    revoked = 0 if body.enabled else revoke_sessions(s, u.id, now, "super_admin_removed")
    audit(s, now, "admin.super_admin_granted" if body.enabled else "admin.super_admin_revoked", actor_id=sc.actor.id,
          target_type="user", target_id=u.id, trace_id=trace_id_for(request), details={"sessions_revoked": revoked})
    s.commit()
    return user_out(s, sc, u, now)


@router.post("/users/{user_id}/revoke-sessions", response_model=AdminUserOut, responses=ERRS)
def admin_revoke_sessions(user_id: str, request: Request, sc: AdminScope = Depends(admin_scope),
                          s: Session = Depends(db_session)) -> dict:
    now = ctx(request).clock.now()
    u = _get_user(s, sc, user_id)
    if not _can_manage_identity(s, sc, u) and u.id != sc.actor.id:
        raise ProblemError(403, "USER_OUTSIDE_SCOPE", "Usuário com vínculos fora do seu escopo: só o SUPER_ADMIN altera.")
    n = revoke_sessions(s, u.id, now, "admin_revoked")
    audit(s, now, "admin.sessions_revoked", actor_id=sc.actor.id, target_type="user", target_id=u.id,
          trace_id=trace_id_for(request), details={"sessions_revoked": n})
    s.commit()
    return user_out(s, sc, u, now)


@router.post("/users/{user_id}/credential-link", response_model=CredentialLink, responses=ERRS)
def credential_link(user_id: str, request: Request, sc: AdminScope = Depends(admin_scope),
                    s: Session = Depends(db_session)) -> CredentialLink:
    """Ativação (conta sem senha) ou redefinição (conta com senha). Link de uso único, 24 h; só exibido agora."""
    now = ctx(request).clock.now()
    u = _get_user(s, sc, user_id, lock=True)
    if not _can_manage_identity(s, sc, u):
        raise ProblemError(403, "USER_OUTSIDE_SCOPE", "Usuário com vínculos fora do seu escopo: só o SUPER_ADMIN altera.")
    if not u.is_active:
        raise ProblemError(409, "USER_INACTIVE", "Reative a conta antes de enviar o link.")
    purpose = "ACTIVATE" if u.password_hash == PENDING_HASH else "RESET"
    if purpose == "RESET" and not sc.super_admin:
        # O link é entregue a quem o gera: com ele, um admin de empresa poderia assumir a conta de um colega.
        raise ProblemError(403, "SUPER_ADMIN_REQUIRED",
                           "Redefinição de senha de conta já ativa: somente SUPER_ADMIN gera o link.")
    link = issue_credential(s, u, purpose, sc.actor.id, now)
    audit(s, now, "admin.password_reset_started" if purpose == "RESET" else "admin.activation_link_issued",
          actor_id=sc.actor.id, target_type="user", target_id=u.id, trace_id=trace_id_for(request),
          details={"expires_at": link.expires_at.isoformat()})
    s.commit()
    return link
