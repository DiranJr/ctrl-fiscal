from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Cookie, Depends, Header, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ctrl_common.problems import ProblemError, problem_responses, trace_id_for

from .app import API_PREFIX, CurrentUser, audit, ctx, current_user, db_session
from .models import Company, CredentialToken, LoginAttempt, Membership, RefreshSession, RefreshToken, User
from .schemas import ActivateRequest, CompanyAccess, CsrfResponse, LoginRequest, MeResponse, TokenResponse, UserOut
from .security import (
    csrf_for_session,
    csrf_matches,
    hash_password,
    needs_rehash,
    new_refresh_token,
    sha256_hex,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])
COOKIE_PATH = API_PREFIX + "/auth"


def _client_ip(request: Request) -> str:
    """IP do cliente para limites de tentativa. X-Real-IP só é aceito quando a conexão vem de um proxy confiável
    (TRUSTED_PROXY_NETS: rede exclusiva gateway↔APIs); de qualquer outro chamador vale o IP da própria conexão."""
    peer = request.client.host if request.client else "unknown"
    fwd = request.headers.get("x-real-ip")
    if fwd and ctx(request).settings.is_trusted_proxy(peer):
        return fwd[:64]
    return peer[:64]


def _require_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin or origin not in ctx(request).settings.allowed_origins:
        raise ProblemError(403, "ORIGIN_NOT_ALLOWED", "Origem da requisição não permitida.")


def _set_refresh_cookie(request: Request, response: Response, token: str) -> None:
    st = ctx(request).settings
    response.set_cookie(
        st.cookie_name,
        token,
        max_age=st.refresh_ttl_seconds,
        path=COOKIE_PATH,
        secure=st.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _clear_refresh_cookie(request: Request, response: Response) -> None:
    st = ctx(request).settings
    response.delete_cookie(st.cookie_name, path=COOKIE_PATH, secure=st.cookie_secure, httponly=True, samesite="strict")


def _issue_refresh(s: Session, session_id: str, request: Request) -> str:
    c = ctx(request)
    now = c.clock.now()
    raw = new_refresh_token()
    s.add(
        RefreshToken(
            session_id=session_id,
            token_hash=sha256_hex(raw),
            issued_at=now,
            expires_at=now + timedelta(seconds=c.settings.refresh_ttl_seconds),
        )
    )
    return raw


def _token_response(request: Request, user: User, session_id: str) -> TokenResponse:
    c = ctx(request)
    return TokenResponse(
        access_token=c.issuer.issue(user.id, session_id, c.clock.now()),
        expires_in=c.issuer.ttl_seconds,
        user=UserOut(id=user.id, name=user.name, email=user.email),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    responses=problem_responses(401, 403, 422, 429),
    summary="Autentica e define o cookie de refresh (HttpOnly).",
)
def login(body: LoginRequest, request: Request, response: Response, s: Session = Depends(db_session)) -> TokenResponse:
    _require_origin(request)
    c = ctx(request)
    now = c.clock.now()
    email = body.email.strip().lower()
    email_hash = sha256_hex(email)
    ip = _client_ip(request)
    window_start = now - timedelta(seconds=c.settings.login_window_seconds)
    fails_email = s.scalar(
        select(func.count()).select_from(LoginAttempt).where(
            LoginAttempt.email_hash == email_hash, LoginAttempt.success.is_(False), LoginAttempt.attempted_at > window_start
        )
    )
    fails_ip = s.scalar(
        select(func.count()).select_from(LoginAttempt).where(
            LoginAttempt.ip == ip, LoginAttempt.success.is_(False), LoginAttempt.attempted_at > window_start
        )
    )
    if fails_email >= c.settings.login_max_failures_per_email or fails_ip >= c.settings.login_max_failures_per_ip:
        raise ProblemError(
            429,
            "LOGIN_RATE_LIMITED",
            "Muitas tentativas. Aguarde antes de tentar novamente.",
            headers={"Retry-After": str(c.settings.login_window_seconds)},
        )
    user = s.scalar(select(User).where(User.email == email))
    ok = verify_password(user.password_hash if user else None, body.password)
    if not ok or user is None or not user.is_active:
        s.add(LoginAttempt(email_hash=email_hash, ip=ip, attempted_at=now, success=False))
        audit(s, now, "auth.login_failed", actor_type="anonymous", details={"email_hash": email_hash[:16]}, trace_id=trace_id_for(request))
        s.commit()
        raise ProblemError(401, "INVALID_CREDENTIALS", "E-mail ou senha inválidos.")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    s.add(LoginAttempt(email_hash=email_hash, ip=ip, attempted_at=now, success=True))
    session = RefreshSession(
        user_id=user.id,
        created_at=now,
        last_used_at=now,
        absolute_expires_at=now + timedelta(seconds=c.settings.session_max_seconds),
    )
    s.add(session)
    s.flush()
    raw = _issue_refresh(s, session.id, request)
    audit(s, now, "auth.login", actor_id=user.id, target_type="session", target_id=session.id, trace_id=trace_id_for(request))
    s.commit()
    _set_refresh_cookie(request, response, raw)
    return _token_response(request, user, session.id)


def _load_refresh(request: Request, s: Session, lock: bool) -> tuple[RefreshToken | None, RefreshSession | None]:
    raw = request.cookies.get(ctx(request).settings.cookie_name)
    if not raw or len(raw) > 200:
        return None, None
    q = select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(raw))
    if lock:
        q = q.with_for_update()
    token = s.scalar(q)
    if token is None:
        return None, None
    return token, s.get(RefreshSession, token.session_id)


def _session_active(sess: RefreshSession | None, now) -> bool:  # noqa: ANN001
    return sess is not None and sess.revoked_at is None and sess.absolute_expires_at > now


@router.get(
    "/csrf",
    response_model=CsrfResponse,
    responses=problem_responses(401),
    summary="Token CSRF vinculado à sessão de refresh vigente.",
)
def csrf(
    request: Request,
    refresh_cookie: str | None = Cookie(None, alias="ctrl_refresh"),
    s: Session = Depends(db_session),
) -> CsrfResponse:
    c = ctx(request)
    now = c.clock.now()
    token, sess = _load_refresh(request, s, lock=False)
    if token is None or token.used_at is not None or token.expires_at <= now or not _session_active(sess, now):
        raise ProblemError(401, "NO_REFRESH_SESSION", "Sem sessão de refresh válida.")
    return CsrfResponse(csrf_token=csrf_for_session(c.settings.csrf_secret, sess.id))


@router.post(
    "/refresh",
    response_model=TokenResponse,
    responses=problem_responses(401, 403),
    summary="Rotaciona o refresh (cookie) e devolve novo token de acesso. Exige X-CSRF-Token.",
)
def refresh(
    request: Request,
    response: Response,
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token", description="Token de GET /auth/csrf."),
    refresh_cookie: str | None = Cookie(None, alias="ctrl_refresh",
                                        description="Cookie HttpOnly (em produção: __Secure-ctrl_refresh)."),
    s: Session = Depends(db_session),
) -> TokenResponse:
    _require_origin(request)
    c = ctx(request)
    now = c.clock.now()
    token, sess = _load_refresh(request, s, lock=True)
    if token is None or sess is None:
        _clear_refresh_cookie(request, response)
        raise ProblemError(401, "INVALID_REFRESH", "Sessão expirada. Entre novamente.")
    if not csrf_matches(c.settings.csrf_secret, sess.id, request.headers.get("x-csrf-token")):
        raise ProblemError(403, "CSRF_INVALID", "Token CSRF ausente ou inválido.")
    if token.used_at is not None:
        # Reutilização de refresh já rotacionado.
        if (now - token.used_at).total_seconds() <= c.settings.refresh_reuse_grace_seconds:
            # Corrida entre abas: não emite token novo e não revoga a família.
            raise ProblemError(401, "REFRESH_ALREADY_ROTATED", "Refresh já utilizado. Repita com o cookie atual.")
        if sess.revoked_at is None:
            sess.revoked_at = now
            sess.revoked_reason = "REUSE_DETECTED"
            audit(s, now, "auth.refresh_reuse_detected", actor_id=sess.user_id, target_type="session", target_id=sess.id, trace_id=trace_id_for(request))
            s.commit()
        raise ProblemError(401, "REFRESH_REUSE_DETECTED", "Sessão revogada por reutilização de refresh.")
    if token.expires_at <= now or not _session_active(sess, now):
        raise ProblemError(401, "INVALID_REFRESH", "Sessão expirada. Entre novamente.")
    user = s.get(User, sess.user_id)
    if user is None or not user.is_active:
        raise ProblemError(401, "INVALID_REFRESH", "Usuário inativo.")
    token.used_at = now
    sess.last_used_at = now
    raw = _issue_refresh(s, sess.id, request)
    s.commit()
    _set_refresh_cookie(request, response, raw)
    return _token_response(request, user, sess.id)


@router.post(
    "/logout",
    status_code=204,
    responses=problem_responses(403),
    summary="Revoga a sessão de refresh atual. Exige X-CSRF-Token.",
)
def logout(
    request: Request,
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token", description="Token de GET /auth/csrf."),
    refresh_cookie: str | None = Cookie(None, alias="ctrl_refresh",
                                        description="Cookie HttpOnly (em produção: __Secure-ctrl_refresh)."),
    s: Session = Depends(db_session),
) -> Response:
    _require_origin(request)
    c = ctx(request)
    now = c.clock.now()
    response = Response(status_code=204)
    token, sess = _load_refresh(request, s, lock=True)
    if token is not None and sess is not None:
        if not csrf_matches(c.settings.csrf_secret, sess.id, request.headers.get("x-csrf-token")):
            raise ProblemError(403, "CSRF_INVALID", "Token CSRF ausente ou inválido.")
        if sess.revoked_at is None:
            sess.revoked_at = now
            sess.revoked_reason = "LOGOUT"
            audit(s, now, "auth.logout", actor_id=sess.user_id, target_type="session", target_id=sess.id, trace_id=trace_id_for(request))
            s.commit()
    _clear_refresh_cookie(request, response)
    return response


@router.get("/me", response_model=MeResponse, responses=problem_responses(401))
def me(cu: CurrentUser = Depends(current_user), s: Session = Depends(db_session)) -> MeResponse:
    rows = s.execute(
        select(Company, Membership)
        .join(Membership, Membership.company_id == Company.id)
        .where(Membership.user_id == cu.user.id, Company.is_active.is_(True))
        .order_by(Company.name)
    ).all()
    return MeResponse(
        id=cu.user.id,
        name=cu.user.name,
        email=cu.user.email,
        companies=[
            CompanyAccess(
                id=co.id, name=co.name, cnpj=co.cnpj, timezone=co.timezone,
                permissions=list(m.permissions or []), environment=co.nfse_environment,
            )
            for co, m in rows
        ],
        is_super_admin=bool(cu.user.is_super_admin),
        admin_company_ids=[co.id for co, m in rows if "admin" in (m.permissions or [])],
        workspaces=_workspaces(s, rows),
    )


def _workspaces(s: Session, rows) -> list[dict]:  # noqa: ANN001
    """Workspaces do usuário (um por empresa, mesmo identificador), com as permissões DAQUELE vínculo."""
    from .models import Workspace

    from .routes_workspaces import workspace_out

    ws = {w.company_id: w for w in s.scalars(select(Workspace).where(Workspace.company_id.in_([co.id for co, _ in rows])))}
    return [workspace_out(ws[co.id], co, list(m.permissions or [])) for co, m in rows if co.id in ws]


ACTIVATE_MARK = "activate"  # LoginAttempt.email_hash para tentativas de ativação (limite por IP)


@router.post(
    "/activate",
    status_code=204,
    responses=problem_responses(400, 403, 422, 429),
    summary="Define a senha com link de uso único (ativação de conta nova ou redefinição).",
)
def activate(body: ActivateRequest, request: Request, s: Session = Depends(db_session)) -> Response:
    """O token vem do fragmento do link (#token=...), lido pela página e enviado no corpo. Uso único e expiração;
    a senha nunca é registrada. Na redefinição, todas as sessões do usuário são revogadas."""
    _require_origin(request)
    c = ctx(request)
    now = c.clock.now()
    ip = _client_ip(request)
    window_start = now - timedelta(seconds=c.settings.login_window_seconds)
    fails = s.scalar(select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.email_hash == ACTIVATE_MARK, LoginAttempt.ip == ip, LoginAttempt.success.is_(False),
        LoginAttempt.attempted_at > window_start))
    if fails >= c.settings.login_max_failures_per_ip:
        raise ProblemError(429, "ACTIVATION_RATE_LIMITED", "Muitas tentativas. Aguarde antes de tentar novamente.",
                           headers={"Retry-After": str(c.settings.login_window_seconds)})
    tok = s.scalar(select(CredentialToken).where(CredentialToken.token_hash == sha256_hex(body.token)).with_for_update())
    user = s.get(User, tok.user_id) if tok else None
    if (tok is None or tok.used_at is not None or tok.revoked_at is not None or tok.expires_at <= now
            or user is None or not user.is_active):
        s.add(LoginAttempt(email_hash=ACTIVATE_MARK, ip=ip, attempted_at=now, success=False))
        audit(s, now, "auth.activation_failed", actor_type="anonymous", trace_id=trace_id_for(request))
        s.commit()
        raise ProblemError(400, "ACTIVATION_INVALID", "Link inválido, expirado ou já utilizado. Peça um novo ao administrador.")
    if body.password.strip().lower() == user.email or len(set(body.password)) < 6:
        raise ProblemError(422, "PASSWORD_WEAK", "Escolha uma senha mais forte (mínimo 12 caracteres, variada, diferente do e-mail).")
    # Consumo ATÔMICO: só um pedido consegue marcar o token como usado (condição no próprio UPDATE). Além da trava
    # de linha acima, protege contra dois pedidos simultâneos com o mesmo link em qualquer banco.
    consumed = s.execute(
        CredentialToken.__table__.update()
        .where(CredentialToken.id == tok.id, CredentialToken.used_at.is_(None), CredentialToken.revoked_at.is_(None),
               CredentialToken.expires_at > now)
        .values(used_at=now)).rowcount
    if consumed != 1:
        s.rollback()
        raise ProblemError(400, "ACTIVATION_INVALID", "Link inválido, expirado ou já utilizado. Peça um novo ao administrador.")
    user.password_hash = hash_password(body.password)
    user.password_set_at = now
    user.version = (user.version or 1) + 1
    user.updated_at = now
    revoked = s.execute(
        RefreshSession.__table__.update().where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason="password_set")).rowcount or 0
    s.add(LoginAttempt(email_hash=ACTIVATE_MARK, ip=ip, attempted_at=now, success=True))
    audit(s, now, "auth.password_set", actor_type="user", actor_id=user.id, target_type="user", target_id=user.id,
          trace_id=trace_id_for(request), details={"purpose": tok.purpose, "sessions_revoked": revoked})
    s.commit()
    return Response(status_code=204)
