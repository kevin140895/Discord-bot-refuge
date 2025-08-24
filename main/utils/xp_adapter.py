def get_user_xp(user_id: int) -> int:
    """Retourne le solde XP actuel du joueur. TODO: brancher au système XP."""
    raise NotImplementedError

def add_user_xp(user_id: int, amount: int, reason: str = "pari_xp") -> None:
    """Crédite/débite XP (+/-). TODO: brancher au système XP."""
    raise NotImplementedError

def get_user_account_age_days(user_id: int) -> int:
    """Retourne l'ancienneté du compte en jours. TODO."""
    raise NotImplementedError

def apply_double_xp_buff(user_id: int, minutes: int = 60) -> None:
    """Placeholder, ne rien implémenter réellement."""
    raise NotImplementedError
