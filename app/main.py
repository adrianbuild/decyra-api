from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import AuthenticatedUser, get_current_user

app = FastAPI(title="Decyra API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/me")
def me(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, str | None]:
    return {"user_id": user.user_id, "email": user.email}
