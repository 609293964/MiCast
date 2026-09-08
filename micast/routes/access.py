"""First deployment and local administrator access routes."""

from fastapi import APIRouter, HTTPException, Request, Response

from micast.access import COOKIE_NAME, SESSION_AGE, AccessManager

router = APIRouter(prefix="/api/access", tags=["access"])


def _set_cookie(response: Response, manager: AccessManager) -> None:
    response.set_cookie(
        COOKIE_NAME,
        manager.issue_session(),
        max_age=SESSION_AGE,
        httponly=True,
        samesite="strict",
        secure=False,
    )


def install(manager: AccessManager) -> APIRouter:
    @router.get("/status")
    async def status(request: Request):
        return {
            "access_configured": manager.access_configured,
            "setup_complete": manager.setup_complete,
            "auth_enabled": manager.auth_enabled,
            "username": manager.username,
            "authenticated": manager.valid_session(request.cookies.get(COOKIE_NAME)),
        }

    @router.post("/setup")
    async def setup(payload: dict, response: Response):
        if manager.access_configured:
            raise HTTPException(status_code=409, detail="管理访问已经设置")
        enabled = bool(payload.get("auth_enabled"))
        password = str(payload.get("password") or "")
        if password != str(payload.get("password_confirm") or ""):
            raise HTTPException(status_code=400, detail="两次输入的密码不一致")
        try:
            manager.configure(
                enabled=enabled,
                username=str(payload.get("username") or "admin"),
                password=password,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if enabled:
            _set_cookie(response, manager)
        return {"ok": True}

    @router.post("/setup/complete")
    async def setup_complete(request: Request):
        if manager.auth_enabled and not manager.valid_session(request.cookies.get(COOKIE_NAME)):
            raise HTTPException(status_code=401, detail="请先登录")
        try:
            manager.complete_setup()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @router.post("/login")
    async def login(payload: dict, response: Response):
        if not manager.verify_password(
            str(payload.get("username") or ""), str(payload.get("password") or "")
        ):
            raise HTTPException(status_code=401, detail="用户名或密码不正确")
        _set_cookie(response, manager)
        return {"ok": True}

    @router.post("/logout")
    async def logout(response: Response):
        response.delete_cookie(COOKIE_NAME)
        return {"ok": True}

    @router.put("/settings")
    async def update_settings(request: Request, payload: dict, response: Response):
        if manager.auth_enabled and not manager.valid_session(request.cookies.get(COOKIE_NAME)):
            raise HTTPException(status_code=401, detail="请先登录")
        enabled = bool(payload.get("auth_enabled"))
        password = str(payload.get("password") or "")
        if enabled and password != str(payload.get("password_confirm") or ""):
            raise HTTPException(status_code=400, detail="两次输入的密码不一致")
        try:
            manager.configure(
                enabled=enabled,
                username=str(payload.get("username") or manager.username),
                password=password,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if enabled:
            _set_cookie(response, manager)
        else:
            response.delete_cookie(COOKIE_NAME)
        return {"ok": True}

    return router
