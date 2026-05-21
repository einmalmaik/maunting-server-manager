from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import User, Permission, Server
from schemas.user import UserUpdate, UserResponse
from schemas.permission import PermissionCreate, PermissionResponse
from dependencies import get_current_owner, verify_csrf

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users", response_model=list[UserResponse])
def list_users(db: Session = Depends(get_db), owner: User = Depends(get_current_owner)) -> list[User]:
    return db.query(User).all()


@router.get("/users/{user_id}", response_model=UserResponse)
def get_user(user_id: int, db: Session = Depends(get_db), owner: User = Depends(get_current_owner)) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    return user


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(user_id: int, req: UserUpdate, db: Session = Depends(get_db), owner: User = Depends(get_current_owner), _: None = Depends(verify_csrf)) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    if req.email is not None:
        user.email = req.email
    if req.is_active is not None:
        user.is_active = req.is_active
    if req.two_factor_enabled is not None:
        user.two_factor_enabled = req.two_factor_enabled
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), owner: User = Depends(get_current_owner), _: None = Depends(verify_csrf)) -> dict:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    if user.is_owner:
        raise HTTPException(status_code=403, detail="Owner kann nicht gelöscht werden")
    db.delete(user)
    db.commit()
    return {"message": "User gelöscht"}


@router.get("/permissions/{user_id}", response_model=list[PermissionResponse])
def list_permissions(user_id: int, db: Session = Depends(get_db), owner: User = Depends(get_current_owner)) -> list[Permission]:
    return db.query(Permission).filter(Permission.user_id == user_id).all()


@router.post("/permissions", response_model=PermissionResponse, status_code=201)
def create_permission(req: PermissionCreate, db: Session = Depends(get_db), owner: User = Depends(get_current_owner), _: None = Depends(verify_csrf)) -> Permission:
    # Upsert-Logik: Wenn schon existiert, updaten
    perm = db.query(Permission).filter(
        Permission.user_id == req.user_id,
        Permission.server_id == req.server_id
    ).first()
    if perm:
        for key, val in req.model_dump().items():
            setattr(perm, key, val)
    else:
        perm = Permission(**req.model_dump())
        db.add(perm)
    db.commit()
    db.refresh(perm)
    return perm
