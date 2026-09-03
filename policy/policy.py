"""Safety policy: allowlist, risk classification, confirmation requirements.

Only Actions whose `type` is in the allowlist may be executed. Risk is
classified per type plus confidence. `confirm_required` actions must always
pass explicit user confirmation before the executor runs.
"""

from __future__ import annotations

from intent import rules

# Types that are destructive, irreversible, or have global side effects.
_CONFIRM_TYPES = frozenset({
    "close_active_window",
    "toggle_fullscreen",
    "lock_screen",
})


def allowed(action_type: str) -> bool:
    return action_type in rules.ALLOWED_TYPES


def classify(action: dict, cfg: dict) -> dict:
    """Set `risk` on the action per type + confidence."""
    action_type = action.get("type", "")
    if action_type in _CONFIRM_TYPES:
        risk = "confirm_required"
    elif float(action.get("confidence", 1.0)) < float(cfg.get("min_confidence", 0.6)):
        risk = "confirm_required"  # low confidence forces confirmation
    else:
        risk = "low"
    action["risk"] = risk
    return action


def requires_confirm(action: dict, cfg: dict) -> bool:
    """Whether the user must confirm before execution."""
    if action.get("risk") == "confirm_required":
        return True
    # Conservative default: even low-risk actions get one explicit confirmation.
    return bool(cfg.get("confirm_low", True))


def verdict(action: dict, cfg: dict) -> dict:
    """Final decision for an Action: allow + confirm requirement, or deny with reason."""
    action_type = action.get("type", "")
    if not allowed(action_type):
        return {"allowed": False, "reason": "action type not allowed: %s" % action_type}
    if float(action.get("confidence", 1.0)) < float(cfg.get("min_confidence", 0.6)):
        return {"allowed": False, "reason": "low confidence; could not resolve target safely"}
    return {"allowed": True, "confirm": requires_confirm(action, cfg)}
