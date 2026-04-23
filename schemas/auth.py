"""Pydantic schemas for auth endpoints."""
from datetime import datetime
from typing import Optional, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator


# ── Register ──────────────────────────────────────────────────────────────────
class RegisterRequest(BaseModel):
    phone:              str               = Field(..., description="+92XXXXXXXXXX or 03XXXXXXXXX")
    firebase_id_token:  str               = Field(..., description="Firebase Phone Auth ID token (from Android SDK)")
    email:              Optional[EmailStr] = None
    full_name:          str               = Field(..., min_length=2, max_length=255)
    password:           str               = Field(..., min_length=8, max_length=128)
    country:            str               = Field(default="Pakistan", max_length=100)
    cnic_number:        str               = Field(..., pattern=r"^\d{5}-\d{7}-\d{1}$", description="XXXXX-XXXXXXX-X — required for AML/KYC compliance")
    account_type:       Literal["individual", "business"] = "individual"
    device_fingerprint: str               = Field(..., min_length=16, max_length=255,
                                                   description="SHA-256(device_id+model+os). Becomes a trusted device.")
    device_name:        Optional[str]     = None
    device_os:          Optional[str]     = None


class PreRegisterResponse(BaseModel):
    phone_masked: str
    message:      str = "OTP sent to your phone. Please verify to complete registration."


class RegisterResponse(BaseModel):
    user_id:      UUID
    phone_masked: str
    tokens:       "TokenPair"
    message:      str = "Account created successfully."


# ── OTP ───────────────────────────────────────────────────────────────────────
class OtpVerifyRequest(BaseModel):
    phone:   str
    otp:     str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    purpose: Literal["registration", "password_reset", "security_change",
                     "bank_link", "new_device"] = "registration"


class OtpResendRequest(BaseModel):
    phone:   str
    purpose: Literal["registration", "password_reset", "security_change",
                     "bank_link", "new_device"] = "registration"


# ── Login ─────────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    phone:              str
    password:           str
    device_fingerprint: str = Field(..., min_length=16, max_length=255)
    device_name:        Optional[str] = None
    device_os:          Optional[str] = None


class NewDeviceVerifyRequest(BaseModel):
    otp:           str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    session_token: str


class NewDeviceFirebaseRequest(BaseModel):
    """Verify a new device using Firebase Phone Auth instead of backend OTP."""
    firebase_id_token: str
    session_token:     str


class PinLoginRequest(BaseModel):
    phone:              str
    pin:                str = Field(..., min_length=4, max_length=6, pattern=r"^\d{4,6}$")
    device_fingerprint: str


class BiometricLoginRequest(BaseModel):
    phone:             str
    biometric_token:   str
    device_fingerprint: str


# ── Tokens ────────────────────────────────────────────────────────────────────
class TokenPair(BaseModel):
    access_token:  str
    refresh_token: str
    token_type:    Literal["bearer"] = "bearer"
    expires_in:    int


class LoginResponse(BaseModel):
    status:        Literal["authenticated", "otp_required"]
    tokens:        Optional[TokenPair] = None
    session_token: Optional[str]       = None
    message:       str
    is_superuser:  Optional[bool]      = None


class RefreshRequest(BaseModel):
    refresh_token: str


# ── Password reset ────────────────────────────────────────────────────────────
class PasswordResetInitiate(BaseModel):
    phone: str


class PasswordResetComplete(BaseModel):
    phone:        str
    otp:          str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    new_password: str = Field(..., min_length=8, max_length=128)


# ── PIN ───────────────────────────────────────────────────────────────────────
class PinSetRequest(BaseModel):
    pin: str = Field(..., min_length=4, max_length=6, pattern=r"^\d{4,6}$")


class PinVerifyRequest(BaseModel):
    pin: str = Field(..., min_length=4, max_length=6, pattern=r"^\d{4,6}$")


class PinResetInitiate(BaseModel):
    phone: str


class PinResetComplete(BaseModel):
    phone:   str
    otp:     str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    new_pin: str = Field(..., min_length=4, max_length=6, pattern=r"^\d{4,6}$")


# ── Generic ───────────────────────────────────────────────────────────────────
class MessageResponse(BaseModel):
    message: str


# Resolve forward references (RegisterResponse → TokenPair)
RegisterResponse.model_rebuild()
