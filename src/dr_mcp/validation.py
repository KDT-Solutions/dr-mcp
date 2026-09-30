"""Eingabe-Validierung für die MCP-Tools, bevor ein Request an die API geht.

Grenzwerte und erlaubte Werte stammen aus der OpenAPI-Spec der Digital
Republic API (Schemas ModifySimRequest, ActivateSimRequest,
ActivateESimRequest, ModifySubscriptionRequest und Query-Parameter von
GET /api/v1/sims).
"""

from __future__ import annotations

SIM_ALIAS_MAX = 24  # ModifySimRequest / ActivateSimRequest: maxLength 24
ESIM_ALIAS_MAX = 12  # ActivateESimRequest: maxLength 12

SUBSCRIPTION_TYPES = ("Data_CH", "Data_Roaming")
SIM_STATUS_VALUES = ("subscription_active", "no_subscription_active", "no_data_package_active")
SORT_FIELDS = ("contract_nr", "iccid", "imsi", "msisdn", "provider", "sim_alias", "subscription_status")
SORT_DIRECTIONS = ("asc", "desc")


def validate_iccid(iccid: str) -> str:
    value = (iccid or "").strip()
    if not value:
        raise ValueError("iccid fehlt")
    if not value.isascii() or not value.isdigit():
        raise ValueError("iccid darf nur Ziffern (0-9) enthalten")
    return value


def validate_alias(alias: str, max_len: int) -> str:
    if alias is None:
        raise ValueError("sim_alias fehlt")
    if len(alias) > max_len:
        raise ValueError(f"sim_alias ist {len(alias)} Zeichen lang, erlaubt sind maximal {max_len}")
    return alias


def validate_choice(value: str, allowed: tuple[str, ...], name: str) -> str:
    if value not in allowed:
        raise ValueError(f"{name} muss einer dieser Werte sein: {', '.join(allowed)} (erhalten: {value!r})")
    return value


def validate_positive_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} muss eine ganze Zahl >= 1 sein")
    return value


def validate_order_id(order_id: str) -> str:
    value = (order_id or "").strip()
    if not value:
        raise ValueError("order_id fehlt")
    return value
