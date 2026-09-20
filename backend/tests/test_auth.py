import pytest

from app.auth import AuthError, AuthService
from app.config.settings import Settings


@pytest.fixture
def auth(tmp_path):
    return AuthService(Settings(auth_db_path=tmp_path / "u.db", livekit_api_secret="s3cret"))


def test_signup_login_roundtrip_and_token(auth):
    user = auth.signup(name="Anshu", email="A@x.com", password="password123")
    assert auth.login(email="a@x.com", password="password123").id == user.id
    assert auth.user_from_token(auth.issue_token(user)).name == "Anshu"


def test_password_is_not_stored_in_plain_text(auth, tmp_path):
    auth.signup(name="A", email="a@x.com", password="password123")
    assert b"password123" not in (tmp_path / "u.db").read_bytes()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "A", "email": "not-an-email", "password": "password123"},
        {"name": "A", "email": "a@x.com", "password": "short"},
        {"name": " ", "email": "a@x.com", "password": "password123"},
    ],
)
def test_signup_validation(auth, kwargs):
    with pytest.raises(AuthError):
        auth.signup(**kwargs)


def test_duplicate_email_and_bad_login(auth):
    auth.signup(name="A", email="a@x.com", password="password123")
    with pytest.raises(AuthError) as dup:
        auth.signup(name="B", email="a@x.com", password="password123")
    assert dup.value.status == 409
    for email, pw in (("a@x.com", "wrongwrong"), ("nobody@x.com", "password123")):
        with pytest.raises(AuthError) as bad:
            auth.login(email=email, password=pw)
        assert bad.value.status == 401


def test_tampered_token_rejected(auth):
    user = auth.signup(name="A", email="a@x.com", password="password123")
    with pytest.raises(AuthError):
        auth.user_from_token(auth.issue_token(user) + "x")
